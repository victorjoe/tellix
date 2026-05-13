"""Tellix - screen recorder with live + post-record transcription.

Tkinter GUI that coordinates:
  - ScreenRecorder (FFmpeg gdigrab, video only)
  - AudioCapture or MixingAudioCapture (mic alone, or mic + system audio)
  - LiveTranscriber (faster-whisper, streams partial captions)
  - Finalization (transcribe -> mux video+audio+soft subtitles)
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional

from recorder import ScreenRecorder
from audio_stream import AudioCapture, LoopbackCapture, MixingAudioCapture
from transcriber import LiveTranscriber, transcribe_file, write_srt, write_txt


APP_NAME = "Tellix"

# Bundle-aware paths so the same code works from source AND from a
# PyInstaller --onefile build.
#   APP_ROOT  = where bundled assets (ffmpeg.exe etc.) live
#   USER_ROOT = where user-facing output should go (next to the .exe)
if getattr(sys, "frozen", False):
    APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    USER_ROOT = Path(sys.executable).parent
else:
    APP_ROOT = Path(__file__).resolve().parent
    USER_ROOT = APP_ROOT

DEFAULT_OUTPUT_DIR = USER_ROOT / "output"


def find_ffmpeg() -> str:
    """Locate ffmpeg.exe: prefer bundled bin/, fall back to PATH."""
    bundled = APP_ROOT / "bin" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    raise FileNotFoundError(
        "FFmpeg not found. Place ffmpeg.exe in tellix/bin/ or add it to PATH."
    )


class TellixApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} - Screen Recorder + Transcriber")
        self.root.geometry("760x580")
        self.root.minsize(580, 420)

        self.output_dir = DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.ffmpeg_path: Optional[str] = None
        try:
            self.ffmpeg_path = find_ffmpeg()
        except FileNotFoundError as e:
            self._pending_ffmpeg_error = str(e)
        else:
            self._pending_ffmpeg_error = None

        self.recorder: Optional[ScreenRecorder] = None
        self.audio = None
        self.live: Optional[LiveTranscriber] = None
        self.session_dir: Optional[Path] = None
        self.start_time: Optional[float] = None
        self.is_recording = False
        self.is_finalizing = False
        self.system_audio_used = False

        self.caption_queue: "queue.Queue[str]" = queue.Queue()
        self._mic_devices: List[dict] = []

        self._build_ui()
        self._tick()

        if self._pending_ffmpeg_error:
            self.root.after(100, lambda: messagebox.showerror(
                "FFmpeg missing", self._pending_ffmpeg_error
            ))

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        top.pack(fill=tk.X)

        ttk.Label(top, text="Microphone:").pack(side=tk.LEFT)
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(
            top, textvariable=self.mic_var, width=36, state="readonly"
        )
        self.mic_combo.pack(side=tk.LEFT, padx=(5, 12))
        self._refresh_mics()

        ttk.Label(top, text="Model:").pack(side=tk.LEFT)
        self.model_var = tk.StringVar(value="base")
        ttk.Combobox(
            top, textvariable=self.model_var, width=8,
            values=["tiny", "base", "small", "medium"], state="readonly",
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(top, text="Output folder...", command=self._pick_output_dir).pack(side=tk.RIGHT)

        opts = ttk.Frame(self.root, padding=(10, 6, 10, 0))
        opts.pack(fill=tk.X)
        self.include_system_audio = tk.BooleanVar(value=False)
        self.sysaudio_check = ttk.Checkbutton(
            opts,
            text="Include system audio (Google Meet, Zoom, calls, etc.)",
            variable=self.include_system_audio,
        )
        self.sysaudio_check.pack(side=tk.LEFT)
        if not LoopbackCapture.is_available():
            self.sysaudio_check.state(["disabled"])
            ttk.Label(
                opts,
                text="(WASAPI loopback unavailable - try `pip install soundcard`)",
                foreground="#a00",
            ).pack(side=tk.LEFT, padx=8)

        ctrl = ttk.Frame(self.root, padding=(10, 10, 10, 0))
        ctrl.pack(fill=tk.X)
        self.start_btn = ttk.Button(ctrl, text="* Start recording", command=self.start)
        self.start_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(ctrl, text="# Stop", command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(ctrl, textvariable=self.status_var, foreground="#666").pack(side=tk.RIGHT)

        caption_frame = ttk.LabelFrame(self.root, text="Live transcript", padding=10)
        caption_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.caption_text = tk.Text(
            caption_frame, wrap=tk.WORD, height=15,
            state=tk.DISABLED, font=("Segoe UI", 10),
        )
        scroll = ttk.Scrollbar(caption_frame, orient=tk.VERTICAL, command=self.caption_text.yview)
        self.caption_text.configure(yscrollcommand=scroll.set)
        self.caption_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _refresh_mics(self) -> None:
        try:
            devices = AudioCapture.list_input_devices()
        except Exception as e:
            messagebox.showerror("Audio init failed", str(e))
            devices = []
        labels = [f"[{d['index']}] {d['name']}" for d in devices]
        self.mic_combo["values"] = labels
        self._mic_devices = devices
        if labels:
            self.mic_combo.current(0)

    def _selected_mic_index(self) -> Optional[int]:
        idx = self.mic_combo.current()
        if idx < 0 or idx >= len(self._mic_devices):
            return None
        return self._mic_devices[idx]["index"]

    def _pick_output_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self.output_dir))
        if chosen:
            self.output_dir = Path(chosen)

    def start(self) -> None:
        if self.is_recording or self.is_finalizing:
            return
        if not self.ffmpeg_path:
            try:
                self.ffmpeg_path = find_ffmpeg()
            except FileNotFoundError as e:
                messagebox.showerror("FFmpeg missing", str(e))
                return

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        self.session_dir = self.output_dir / f"tellix-{timestamp}"
        self.session_dir.mkdir(parents=True, exist_ok=True)

        screen_path = self.session_dir / "screen.mp4"

        try:
            self.recorder = ScreenRecorder(self.ffmpeg_path, screen_path)
            self.recorder.start()
        except Exception as e:
            messagebox.showerror("Screen recorder failed", str(e))
            return

        self.system_audio_used = bool(self.include_system_audio.get())
        try:
            if self.system_audio_used:
                self.audio = MixingAudioCapture(
                    self.session_dir,
                    mic_device=self._selected_mic_index(),
                    loopback_device=None,
                )
            else:
                self.audio = AudioCapture(
                    self.session_dir / "mic.wav",
                    device=self._selected_mic_index(),
                )
            self.audio.start()
        except Exception as e:
            try:
                self.recorder.stop()
            except Exception:
                pass
            self.system_audio_used = False
            messagebox.showerror(
                "Audio capture failed",
                f"{e}\n\nIf this is the first run, check\n"
                "Settings -> Privacy & security -> Microphone -> allow desktop apps.",
            )
            return

        self._clear_caption()
        self._append_caption(f"[Recording started at {timestamp}]\n\n")

        self.live = LiveTranscriber(
            self.audio.chunk_queue,
            on_text=lambda t: self.caption_queue.put(t),
            model_size=self.model_var.get(),
        )
        self.live.start()

        self.start_time = time.time()
        self.is_recording = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("Recording...")

    def stop(self) -> None:
        if not self.is_recording or self.is_finalizing:
            return
        self.is_recording = False
        self.is_finalizing = True
        self.stop_btn.config(state=tk.DISABLED)
        self.status_var.set("Finalizing...")
        threading.Thread(target=self._finalize, daemon=True).start()

    def _finalize(self) -> None:
        try:
            if self.audio:
                self.audio.stop()
            if self.recorder:
                self.recorder.stop()
            if self.live:
                self.live.stop()

            assert self.session_dir is not None
            screen = self.session_dir / "screen.mp4"
            final = self.session_dir / "recording.mp4"
            srt_path = self.session_dir / "recording.srt"
            txt_path = self.session_dir / "recording.txt"

            if self.system_audio_used:
                audio_for_final = self.session_dir / "mixed.wav"
            else:
                audio_for_final = self.session_dir / "mic.wav"

            # Transcribe FIRST so the .srt is ready to mux into the .mp4
            # as a soft subtitle track (captions travel inside the file).
            self._status_async("Transcribing (final pass)...")
            segments = transcribe_file(audio_for_final, model_size=self.model_var.get())
            write_srt(segments, srt_path)
            write_txt(segments, txt_path)

            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            )

            if segments:
                # Embed captions as a soft subtitle stream (mov_text). Any
                # modern player can toggle these on with its CC button.
                self._status_async("Muxing video + audio + subtitles...")
                mux_cmd = [
                    self.ffmpeg_path, "-y",
                    "-i", str(screen),
                    "-i", str(audio_for_final),
                    "-i", str(srt_path),
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-map", "2:s:0",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=eng",
                    "-shortest",
                    str(final),
                ]
            else:
                # Empty transcript -> no subtitle track to add
                self._status_async("Muxing video + audio (no subs)...")
                mux_cmd = [
                    self.ffmpeg_path, "-y",
                    "-i", str(screen),
                    "-i", str(audio_for_final),
                    "-c:v", "copy", "-c:a", "aac", "-shortest",
                    str(final),
                ]

            result = subprocess.run(
                mux_cmd, capture_output=True, creationflags=creationflags
            )
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")[-2000:]
                raise RuntimeError(f"FFmpeg mux failed:\n{err}")

            self._caption_async(f"\n\n[Done. Output saved to: {self.session_dir}]")
            self._status_async("Done")
            self.root.after(0, self._show_done_dialog)
        except Exception as e:
            err_msg = str(e)
            self.root.after(0, lambda: messagebox.showerror("Finalization failed", err_msg))
            self._status_async("Error")
        finally:
            self.is_finalizing = False
            self.root.after(0, lambda: self.start_btn.config(state=tk.NORMAL))

    def _show_done_dialog(self) -> None:
        if self.session_dir is None:
            return
        if messagebox.askyesno(
            "Recording complete",
            f"Saved to:\n{self.session_dir}\n\n"
            f"Captions are embedded in recording.mp4 - turn on CC in your "
            f"player (e.g. VLC: Subtitle > Subtitle Track).\n\n"
            f"Open folder?",
        ):
            if os.name == "nt":
                os.startfile(str(self.session_dir))  # type: ignore[attr-defined]

    def _tick(self) -> None:
        while True:
            try:
                text = self.caption_queue.get_nowait()
            except queue.Empty:
                break
            self._append_caption(text + " ")

        if self.is_recording and self.start_time:
            elapsed = int(time.time() - self.start_time)
            mm, ss = divmod(elapsed, 60)
            self.status_var.set(f"Recording... {mm:02d}:{ss:02d}")

        self.root.after(200, self._tick)

    def _clear_caption(self) -> None:
        self.caption_text.config(state=tk.NORMAL)
        self.caption_text.delete("1.0", tk.END)
        self.caption_text.config(state=tk.DISABLED)

    def _append_caption(self, text: str) -> None:
        self.caption_text.config(state=tk.NORMAL)
        self.caption_text.insert(tk.END, text)
        self.caption_text.see(tk.END)
        self.caption_text.config(state=tk.DISABLED)

    def _caption_async(self, text: str) -> None:
        self.caption_queue.put(text)

    def _status_async(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(text))


def main() -> None:
    root = tk.Tk()
    TellixApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
