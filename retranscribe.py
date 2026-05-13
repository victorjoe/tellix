r"""Re-run the final-pass transcription on an existing WAV.

Use this to recover a transcript from a session that was recorded with
the old (over-aggressive VAD + forced-English) settings, without having
to re-record.

Usage:
    python retranscribe.py                       # auto-picks most recent session
    python retranscribe.py path\to\mic.wav
    python retranscribe.py path\to\session_dir
    python retranscribe.py path\to\session_dir --model small
    python retranscribe.py path\to\session_dir --language en

If you give it a session directory, it will pick mixed.wav if present,
otherwise mic.wav, and write recording.srt + recording.txt next to it.

If you don't give a path at all, it scans ./output/ and uses the
most recently modified tellix-* folder.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from transcriber import transcribe_file, write_srt, write_txt


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = APP_ROOT / "output"


def find_latest_session(output_dir: Path = DEFAULT_OUTPUT_DIR) -> Optional[Path]:
    """Return the most-recently-modified tellix-* folder under output/, if any."""
    if not output_dir.is_dir():
        return None
    sessions = [
        p for p in output_dir.iterdir()
        if p.is_dir() and p.name.startswith("tellix-")
    ]
    if not sessions:
        return None
    return max(sessions, key=lambda p: p.stat().st_mtime)


def resolve_wav(target: Path) -> Path:
    """If target is a session dir, prefer mixed.wav over mic.wav."""
    if target.is_dir():
        mixed = target / "mixed.wav"
        mic = target / "mic.wav"
        if mixed.exists():
            return mixed
        if mic.exists():
            return mic
        raise FileNotFoundError(f"No mixed.wav or mic.wav in {target}")
    if not target.exists():
        raise FileNotFoundError(f"{target} does not exist")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-run Whisper on an existing WAV.")
    ap.add_argument("path", type=Path, nargs="?", default=None,
                    help="Path to a WAV file OR a tellix session directory. "
                         "If omitted, uses the most recent session under ./output/")
    ap.add_argument("--model", default="small",
                    choices=["tiny", "base", "small", "medium"],
                    help="Whisper model size (default: small)")
    ap.add_argument("--language", default=None,
                    help="Language code (e.g. 'en'). Default: auto-detect.")
    args = ap.parse_args()

    target = args.path
    if target is None:
        target = find_latest_session()
        if target is None:
            print(
                f"ERROR: no path given and no tellix-* sessions found under "
                f"{DEFAULT_OUTPUT_DIR}",
                file=sys.stderr,
            )
            return 2
        print(f"Auto-selected most recent session: {target}")

    try:
        wav = resolve_wav(target)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"Transcribing {wav} with model={args.model} language={args.language or 'auto'}...")
    segments = transcribe_file(wav, model_size=args.model, language=args.language)

    out_dir = wav.parent
    srt = out_dir / "recording.srt"
    txt = out_dir / "recording.txt"
    write_srt(segments, srt)
    write_txt(segments, txt)

    print(f"  {len(segments)} segment(s)")
    print(f"  wrote {srt}")
    print(f"  wrote {txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
