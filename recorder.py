"""Screen recorder using FFmpeg gdigrab (Windows-only).

Spawns FFmpeg as a subprocess, captures the primary display to an MP4,
and supports graceful shutdown by sending 'q' to FFmpeg's stdin.

Uses fragmented MP4 flags so the output stays playable even if FFmpeg
is killed abruptly mid-recording.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


class ScreenRecorder:
    """Records the primary display to an MP4 using FFmpeg gdigrab."""

    def __init__(
        self,
        ffmpeg_path: str,
        output_path: Path,
        framerate: int = 30,
        draw_mouse: bool = True,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.output_path = Path(output_path)
        self.framerate = framerate
        self.draw_mouse = draw_mouse
        self._proc: Optional[subprocess.Popen] = None
        self._log_file = None

    def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("ScreenRecorder already started")

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.output_path.exists():
            self.output_path.unlink()

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "gdigrab",
            "-framerate", str(self.framerate),
            "-draw_mouse", "1" if self.draw_mouse else "0",
            "-i", "desktop",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            # Crash-resilient: file stays playable even if FFmpeg is killed
            "-movflags", "+frag_keyframe+empty_moov",
            str(self.output_path),
        ]

        log_path = self.output_path.with_suffix(".ffmpeg.log")
        self._log_file = open(log_path, "wb")

        creationflags = 0
        if os.name == "nt":
            # Avoid flashing a console window when running as a windowed app
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=self._log_file,
            stderr=self._log_file,
            creationflags=creationflags,
        )

    def stop(self, timeout: float = 10.0) -> int:
        """Gracefully stop FFmpeg by sending 'q' to stdin, then wait."""
        if self._proc is None:
            raise RuntimeError("ScreenRecorder not started")

        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                try:
                    self._proc.stdin.write(b"q")
                    self._proc.stdin.flush()
                    self._proc.stdin.close()
                except OSError:
                    pass
            try:
                rc = self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._proc.terminate()
                try:
                    rc = self._proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    rc = self._proc.wait()
        finally:
            self._proc = None
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None
        return rc

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
