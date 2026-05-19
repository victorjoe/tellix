"""Whisper transcription using faster-whisper (CTranslate2).

Provides:
- LiveTranscriber: background thread that consumes audio chunks and emits
  partial captions while recording.
- transcribe_file: final-pass transcription on the saved WAV.
- write_srt / write_txt: serializers for the final transcript.

GPU detection uses try/except on CUDA load - NOT torch.cuda.is_available(),
because faster-whisper uses CTranslate2 and we don't want a useless torch
dependency.

Notes on settings:
- The FINAL pass runs with vad_filter=False and language=None
  (auto-detect). Silero VAD is too aggressive at default settings and
  drops quiet speech entirely; Whisper has its own no-speech detection
  that's used internally and works on more nuanced signals.
- The LIVE pass keeps vad_filter=True because it suppresses Whisper's
  tendency to hallucinate text on long silences when running on short
  rolling windows. Both passes default to language auto-detect.
"""
from __future__ import annotations

import html
import os
import queue
import re
import sys
import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np

try:
    from faster_whisper import WhisperModel
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "faster-whisper is required. Install via `pip install faster-whisper`."
    ) from e

from audio_stream import END_OF_STREAM


MODEL_REVISIONS = {
    "tiny": (
        "Systran/faster-whisper-tiny",
        "d90ca5fe260221311c53c58e660288d3deb8d356",
    ),
    "base": (
        "Systran/faster-whisper-base",
        "a80717a3a48b1b28aa687bca146cb7301feae1b1",
    ),
    "small": (
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
    ),
    "medium": (
        "Systran/faster-whisper-medium",
        "7832330bcea9a8d5fd6d6637c49fe5d256e98277",
    ),
}


def _default_model_cache_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "models"
    return Path(__file__).resolve().parent / ".cache" / "huggingface"


def _resolve_model(model_size: str) -> str:
    """Resolve a UI model name to a pinned local Hugging Face snapshot."""
    model_path = Path(model_size)
    if model_path.exists():
        return str(model_path)
    if model_size not in MODEL_REVISIONS:
        raise ValueError(f"Unsupported model size: {model_size}")

    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "huggingface-hub is required to download pinned Whisper models."
        ) from e

    repo_id, revision = MODEL_REVISIONS[model_size]
    local_files_only = os.environ.get("TELLIX_OFFLINE_MODE") == "1"
    cache_dir = Path(
        os.environ.get("TELLIX_MODEL_CACHE", str(_default_model_cache_dir()))
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    return snapshot_download(
        repo_id=repo_id,
        revision=revision,
        cache_dir=str(cache_dir),
        local_files_only=local_files_only,
    )


def _load_model(model_size: str) -> WhisperModel:
    """Load a Whisper model. Try CUDA first, fall back to CPU on any failure."""
    model_path = _resolve_model(model_size)
    try:
        return WhisperModel(model_path, device="cuda", compute_type="float16")
    except Exception:
        return WhisperModel(model_path, device="cpu", compute_type="int8")


class LiveTranscriber:
    """Streams partial captions from an audio queue."""

    SAMPLE_RATE = 16000
    WINDOW_SECONDS = 5.0
    OVERLAP_SECONDS = 1.0

    def __init__(self, audio_queue, on_text: Callable[[str], None],
                 model_size: str = "base", language: Optional[str] = None):
        # language=None -> auto-detect. Hardcoding 'en' caused silent
        # transcripts for any speaker Whisper wanted to tag as a different
        # language code (or even accented English).
        self.audio_queue = audio_queue
        self.on_text = on_text
        self.model_size = model_size
        self.language = language
        self._model: Optional[WhisperModel] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("LiveTranscriber already started")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        self.on_text(f"[loading Whisper model: {self.model_size}...]")
        try:
            self._model = _load_model(self.model_size)
        except Exception as e:
            self.on_text(f"[transcriber init failed: {e}]")
            return
        self.on_text("[ready]")

        window_samples = int(self.WINDOW_SECONDS * self.SAMPLE_RATE)
        overlap_samples = int(self.OVERLAP_SECONDS * self.SAMPLE_RATE)
        buffer = np.zeros(0, dtype=np.int16)

        while not self._stop_event.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if chunk is END_OF_STREAM:
                if len(buffer) > self.SAMPLE_RATE:
                    self._transcribe_buffer(buffer)
                break

            buffer = np.concatenate([buffer, chunk])

            if len(buffer) >= window_samples:
                self._transcribe_buffer(buffer)
                buffer = buffer[-overlap_samples:]

    def _transcribe_buffer(self, buffer: np.ndarray) -> None:
        if self._model is None:
            self.on_text("[transcription error: model is not loaded]")
            return
        audio_f32 = buffer.astype(np.float32) / 32768.0
        try:
            segments, _info = self._model.transcribe(
                audio_f32,
                beam_size=1,
                vad_filter=True,           # keep VAD on the live path
                language=self.language,    # None = auto-detect
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            if text:
                self.on_text(text)
        except Exception as e:
            self.on_text(f"[transcription error: {e}]")

    def stop(self, timeout: float = 15.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None


Segment = Tuple[float, float, str]


def transcribe_file(wav_path: Path, model_size: str = "small",
                    language: Optional[str] = None) -> List[Segment]:
    """Run a full-quality transcription pass on the recorded WAV.

    language=None means auto-detect. VAD is OFF here - the final pass
    has the full audio context, so we let Whisper's internal
    no_speech_threshold filter silence instead of Silero VAD (which is
    too aggressive at default settings and drops quiet speech).
    """
    model = _load_model(model_size)
    segments, _info = model.transcribe(
        str(wav_path),
        beam_size=5,
        vad_filter=False,                  # was True - too aggressive
        language=language,                 # None = auto-detect
    )
    return [(s.start, s.end, s.text.strip()) for s in segments]


def _format_srt_timestamp(t: float) -> str:
    total_ms = max(0, int(round(t * 1000)))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _sanitize_srt_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("-->", "->")
    return html.escape(text, quote=False)


def write_srt(segments: List[Segment], srt_path: Path) -> None:
    lines: List[str] = []
    cue_number = 1
    for start, end, text in segments:
        safe_text = _sanitize_srt_text(text)
        if not safe_text:
            continue
        lines.append(str(cue_number))
        lines.append(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}")
        lines.append(safe_text)
        lines.append("")
        cue_number += 1
    Path(srt_path).write_text("\n".join(lines), encoding="utf-8")


def write_txt(segments: List[Segment], txt_path: Path) -> None:
    if not segments:
        # Make empty-transcript runs visibly diagnosable instead of writing
        # a zero-byte file that looks like nothing ran.
        Path(txt_path).write_text(
            "(no speech detected - check mic.wav levels or speak louder)\n",
            encoding="utf-8",
        )
        return
    Path(txt_path).write_text(
        "\n".join(text for _, _, text in segments),
        encoding="utf-8",
    )
