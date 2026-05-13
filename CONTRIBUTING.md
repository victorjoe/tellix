# Contributing to Tellix

Thanks for considering a contribution. Issues and pull requests are both welcome. The project is small and the bar for changes is "does it make Tellix more reliable, easier to use, or better documented without adding surprise". This guide covers how to get set up, what to test, and what a clean PR looks like.

## Development setup

Tellix targets Windows 10 / 11 with Python 3.10 or newer. From a fresh clone:

```powershell
git clone https://github.com/<your-username>/tellix.git
cd tellix
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Drop a Windows static build of `ffmpeg.exe` into `bin\`. An LGPL build is preferred; see the FFmpeg licensing note in `README.md`. To run from source:

```powershell
python app.py
```

To rebuild the bundled `.exe`:

```powershell
.\build.ps1
```

To regenerate the PDF docs after editing `HELP.md` or `RELEASE.md`:

```powershell
python tools\render_docs.py
```

## Code style

Tellix uses standard PEP 8 conventions, type hints where they help readability, and prefers small focused modules over large ones. There's no enforced formatter — readable diffs win. The codebase is single-file-per-concern (`recorder.py`, `audio_stream.py`, `transcriber.py`, `app.py`) and that pattern should hold for new features.

## Testing a change

Tellix doesn't have an automated test suite yet — most of the surface area touches real hardware (microphones, screen capture, GPU) that's hard to fake. Before submitting a PR, manually verify on Windows that recording, stopping, transcribing, and viewing the final `recording.mp4` all still work. If your change touches audio or transcription, test both with and without the "Include system audio" checkbox enabled. If it touches the bundle, test the produced `Tellix.exe` on a machine that doesn't have Python installed.

## Filing an issue

If you're reporting a bug, include the Windows version, Python version (`python --version`), Tellix version or commit hash, and what model size was selected. For audio capture failures, attach the `screen.ffmpeg.log` file from the session folder. For transcription failures, try `python retranscribe.py --model small` against the same session and report whether the result changes — that often distinguishes "bad audio" from "bad transcription settings".

## Pull request checklist

A clean PR usually has a short, descriptive title, a body that explains *why* not just *what*, and the change scoped to a single concern. If the change is user-facing, update `HELP.md` and/or `RELEASE.md` (whichever applies); the PDFs can be re-rendered on release. If the change touches the build, mention in the PR whether you've verified the resulting `Tellix.exe` runs on a clean Windows machine. If you're adding a dependency, justify it in the PR description — Tellix tries to stay light.

## License

By contributing, you agree your contributions are released under the same [MIT License](LICENSE) that covers the rest of the project.
