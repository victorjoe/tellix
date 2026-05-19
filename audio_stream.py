"""Microphone and system-audio capture.

- AudioCapture: mic capture with auto-fallback. Opens the device at its
  native sample rate (avoids MME 'Undefined external error' from forcing
  16kHz on devices that don't support it), then resamples to 16 kHz mono
  in the callback. Falls back to WASAPI default input, then PortAudio
  default, if the chosen device refuses.
- LoopbackCapture: system playback via soundcard's WASAPI loopback.
- MixingAudioCapture: owns both captures, mixes them in real time.
"""
from __future__ import annotations

import queue
import threading
import wave
from pathlib import Path
from typing import List, Optional

import numpy as np
import sounddevice as sd


END_OF_STREAM = object()
TARGET_RATE = 16000
TARGET_CHANNELS = 1
AUDIO_QUEUE_MAX_CHUNKS = 120


def _put_latest(q: "queue.Queue", item) -> None:
    """Best-effort queue put for real-time audio callbacks.

    If live transcription falls behind, drop the oldest queued chunk rather
    than blocking an audio callback or allowing unbounded memory growth.
    """
    try:
        q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        q.get_nowait()
    except queue.Empty:
        pass
    try:
        q.put_nowait(item)
    except queue.Full:
        pass


def _resample_to_16k_mono(buf, source_rate: int, source_channels: int):
    """Downmix and resample any int16 buffer to 16 kHz mono int16.

    Accepts either a 1-D interleaved buffer or a 2-D (frames, channels) buffer.
    """
    if buf.ndim == 1 and source_channels > 1:
        buf = buf.reshape(-1, source_channels)
    if buf.ndim == 2 and source_channels > 1:
        mono = buf.astype(np.int32).mean(axis=1).astype(np.int32)
    else:
        mono = buf.astype(np.int32).reshape(-1)

    if source_rate == TARGET_RATE:
        return mono.astype(np.int16)

    n_in = len(mono)
    n_out = max(1, int(round(n_in * TARGET_RATE / source_rate)))
    x_in = np.arange(n_in, dtype=np.float64)
    x_out = np.linspace(0, n_in - 1, n_out, dtype=np.float64)
    out_f = np.interp(x_out, x_in, mono.astype(np.float64))
    return np.clip(out_f, -32768, 32767).astype(np.int16)


def _find_wasapi_default_input() -> Optional[int]:
    """Locate the WASAPI host API's default input device."""
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        return None
    for ha in hostapis:
        if "WASAPI" in ha["name"]:
            idx = ha.get("default_input_device", -1)
            return idx if idx is not None and idx >= 0 else None
    return None


def _find_wasapi_default_output() -> Optional[int]:
    """Locate the WASAPI host API's default output device (for loopback)."""
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        return None
    for ha in hostapis:
        if "WASAPI" in ha["name"]:
            idx = ha.get("default_output_device", -1)
            return idx if idx is not None and idx >= 0 else None
    return None


class AudioCapture:
    """Mic capture that outputs 16 kHz mono int16 to a WAV + queue.

    Opens the device at its native rate/channels and resamples in-callback
    to avoid format-negotiation errors with MME/DirectSound drivers.
    """

    SAMPLE_RATE = TARGET_RATE
    CHANNELS = TARGET_CHANNELS

    def __init__(self, wav_path: Path, device: Optional[int] = None,
                 block_seconds: float = 0.5):
        self.wav_path = Path(wav_path)
        self.device = device
        self.block_seconds = block_seconds
        self.chunk_queue = queue.Queue(maxsize=AUDIO_QUEUE_MAX_CHUNKS)
        self._stream = None
        self._wav = None
        self._wav_lock = threading.Lock()
        # Set when a stream successfully opens:
        self._source_rate = TARGET_RATE
        self._source_channels = 1

    def _build_attempts(self):
        """List of (device, rate, channels) configs to try in order."""
        attempts = []

        # Try the user-picked device at its native format first
        if self.device is not None:
            try:
                info = sd.query_devices(self.device, "input")
                native_rate = int(info.get("default_samplerate") or 44100)
                max_ch = int(info.get("max_input_channels") or 1)
                native_ch = min(max_ch, 2) if max_ch > 0 else 1
            except Exception:
                native_rate, native_ch = 44100, 1
            attempts.append((self.device, native_rate, native_ch))
            if native_ch != 1:
                attempts.append((self.device, native_rate, 1))
            # Some devices accept the target rate directly:
            attempts.append((self.device, TARGET_RATE, 1))

        # Fall back to WASAPI default input (more permissive than MME)
        wasapi_in = _find_wasapi_default_input()
        if wasapi_in is not None and wasapi_in != self.device:
            try:
                info = sd.query_devices(wasapi_in, "input")
                native_rate = int(info.get("default_samplerate") or 48000)
            except Exception:
                native_rate = 48000
            attempts.append((wasapi_in, native_rate, 1))
            attempts.append((wasapi_in, native_rate, 2))

        # Last resort: PortAudio default device
        if self.device is not None:
            attempts.append((None, TARGET_RATE, 1))
            attempts.append((None, 44100, 1))
            attempts.append((None, 48000, 1))

        return attempts

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("AudioCapture already started")

        # WAV writer always at 16 kHz mono (we resample in-callback)
        self.wav_path.parent.mkdir(parents=True, exist_ok=True)
        self._wav = wave.open(str(self.wav_path), "wb")
        self._wav.setnchannels(self.CHANNELS)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self.SAMPLE_RATE)

        attempts = self._build_attempts()
        last_err = None
        for dev, rate, ch in attempts:
            stream = None
            try:
                block = int(rate * self.block_seconds)
                stream = sd.InputStream(
                    samplerate=rate,
                    channels=ch,
                    dtype="int16",
                    device=dev,
                    blocksize=block,
                    callback=self._on_audio,
                )
                stream.start()
                self._stream = stream
                self._source_rate = rate
                self._source_channels = ch
                return
            except Exception as e:
                last_err = e
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue

        # All attempts failed
        with self._wav_lock:
            if self._wav is not None:
                self._wav.close()
                self._wav = None
        raise RuntimeError(
            f"Could not open microphone. Tried {len(attempts)} format(s). "
            f"Last error: {last_err}"
        )

    def _on_audio(self, indata, frames, time_info, status) -> None:
        # Resample/downmix to 16 kHz mono if the device wasn't already
        if (self._source_rate == TARGET_RATE
                and self._source_channels == TARGET_CHANNELS):
            chunk = indata.copy().reshape(-1)
        else:
            chunk = _resample_to_16k_mono(
                indata, self._source_rate, self._source_channels
            )

        with self._wav_lock:
            if self._wav is not None:
                self._wav.writeframes(chunk.tobytes())
        _put_latest(self.chunk_queue, chunk)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        with self._wav_lock:
            if self._wav is not None:
                self._wav.close()
                self._wav = None
        _put_latest(self.chunk_queue, END_OF_STREAM)

    @staticmethod
    def list_input_devices() -> List[dict]:
        devices = sd.query_devices()
        return [
            {"index": i, "name": d["name"]}
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0
        ]


class LoopbackCapture:
    """System-audio capture via WASAPI loopback using the `soundcard` library."""

    SOURCE_RATE = 48000
    SOURCE_CHANNELS = 2

    def __init__(self, wav_path: Path, device: Optional[str] = None,
                 block_seconds: float = 0.5):
        self.wav_path = Path(wav_path)
        self.device = device
        self.block_seconds = block_seconds
        self.chunk_queue = queue.Queue(maxsize=AUDIO_QUEUE_MAX_CHUNKS)
        self._wav = None
        self._wav_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._loopback_mic = None
        self._target_block = int(TARGET_RATE * block_seconds)
        self._down_buffer = np.zeros(0, dtype=np.int16)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("LoopbackCapture already started")
        try:
            import soundcard as sc
        except ImportError as e:
            raise RuntimeError(
                "The `soundcard` package is required for system-audio capture.\n"
                "Install it with: pip install soundcard"
            ) from e

        speaker = sc.default_speaker() if self.device is None else sc.get_speaker(self.device)
        if speaker is None:
            raise RuntimeError("No default speaker found for WASAPI loopback.")

        self._loopback_mic = sc.get_microphone(
            id=str(speaker.name), include_loopback=True
        )

        self.wav_path.parent.mkdir(parents=True, exist_ok=True)
        self._wav = wave.open(str(self.wav_path), "wb")
        self._wav.setnchannels(self.SOURCE_CHANNELS)
        self._wav.setsampwidth(2)
        self._wav.setframerate(self.SOURCE_RATE)

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        frames_per_block = int(self.SOURCE_RATE * self.block_seconds)
        try:
            with self._loopback_mic.recorder(
                samplerate=self.SOURCE_RATE,
                channels=self.SOURCE_CHANNELS,
                blocksize=frames_per_block,
            ) as mic:
                while not self._stop_event.is_set():
                    data_f32 = mic.record(numframes=frames_per_block)
                    data_i16 = np.clip(
                        data_f32 * 32767.0, -32768, 32767
                    ).astype(np.int16)

                    with self._wav_lock:
                        if self._wav is not None:
                            self._wav.writeframes(data_i16.tobytes())

                    down = _resample_to_16k_mono(
                        data_i16, self.SOURCE_RATE, self.SOURCE_CHANNELS
                    )
                    self._down_buffer = np.concatenate([self._down_buffer, down])
                    while len(self._down_buffer) >= self._target_block:
                        chunk = self._down_buffer[: self._target_block]
                        self._down_buffer = self._down_buffer[self._target_block:]
                        _put_latest(self.chunk_queue, chunk.copy())
        except Exception:
            _put_latest(self.chunk_queue, END_OF_STREAM)
            raise

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if len(self._down_buffer) > 0:
            _put_latest(self.chunk_queue, self._down_buffer.copy())
            self._down_buffer = np.zeros(0, dtype=np.int16)
        with self._wav_lock:
            if self._wav is not None:
                try:
                    self._wav.close()
                except Exception:
                    pass
                self._wav = None
        _put_latest(self.chunk_queue, END_OF_STREAM)

    @staticmethod
    def is_available() -> bool:
        try:
            import soundcard as sc
            return sc.default_speaker() is not None
        except Exception:
            return False


class MixingAudioCapture:
    """Mic + system-audio captures running in parallel, mixed in real time."""

    SAMPLE_RATE = TARGET_RATE

    def __init__(self, session_dir: Path, mic_device: Optional[int] = None,
                 loopback_device: Optional[str] = None,
                 block_seconds: float = 0.5):
        self.session_dir = Path(session_dir)
        self.mic = AudioCapture(
            self.session_dir / "mic.wav",
            device=mic_device, block_seconds=block_seconds,
        )
        self.loopback = LoopbackCapture(
            self.session_dir / "system.wav",
            device=loopback_device, block_seconds=block_seconds,
        )
        self.mixed_wav_path = self.session_dir / "mixed.wav"
        self.chunk_queue = queue.Queue(maxsize=AUDIO_QUEUE_MAX_CHUNKS)
        self._mixer_thread = None
        self._stop_event = threading.Event()
        self._mixed_wav = None

    def start(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._mixed_wav = wave.open(str(self.mixed_wav_path), "wb")
        self._mixed_wav.setnchannels(1)
        self._mixed_wav.setsampwidth(2)
        self._mixed_wav.setframerate(self.SAMPLE_RATE)

        self.mic.start()
        try:
            self.loopback.start()
        except Exception:
            self.mic.stop()
            if self._mixed_wav:
                self._mixed_wav.close()
                self._mixed_wav = None
            raise

        self._mixer_thread = threading.Thread(target=self._mix_loop, daemon=True)
        self._mixer_thread.start()

    def _mix_loop(self) -> None:
        mic_done = False
        sys_done = False
        while not self._stop_event.is_set() and not (mic_done and sys_done):
            mic_chunk = None
            sys_chunk = None
            try:
                if not mic_done:
                    mic_chunk = self.mic.chunk_queue.get(timeout=1.0)
                if not sys_done:
                    sys_chunk = self.loopback.chunk_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if mic_chunk is END_OF_STREAM:
                mic_done = True
                mic_chunk = None
            if sys_chunk is END_OF_STREAM:
                sys_done = True
                sys_chunk = None

            if mic_chunk is None and sys_chunk is None:
                continue
            if mic_chunk is None:
                mixed = sys_chunk
            elif sys_chunk is None:
                mixed = mic_chunk
            else:
                n = min(len(mic_chunk), len(sys_chunk))
                a = mic_chunk[:n].astype(np.int32)
                b = sys_chunk[:n].astype(np.int32)
                mixed = np.clip(a + b, -32768, 32767).astype(np.int16)

            if self._mixed_wav is not None:
                self._mixed_wav.writeframes(mixed.tobytes())
            _put_latest(self.chunk_queue, mixed)

        _put_latest(self.chunk_queue, END_OF_STREAM)
        if self._mixed_wav is not None:
            try:
                self._mixed_wav.close()
            except Exception:
                pass
            self._mixed_wav = None

    def stop(self) -> None:
        try:
            self.mic.stop()
        except Exception:
            pass
        try:
            self.loopback.stop()
        except Exception:
            pass
        if self._mixer_thread is not None:
            self._mixer_thread.join(timeout=10.0)
            self._mixer_thread = None
