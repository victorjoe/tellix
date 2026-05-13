# Third-Party Notices

Tellix is released under the MIT License (see `LICENSE`). It depends on, and is built with, the following third-party components. Their licenses apply to those components as distributed, not to Tellix itself.

## Runtime dependencies

**faster-whisper** — MIT License. A reimplementation of OpenAI Whisper on top of CTranslate2.
https://github.com/SYSTRAN/faster-whisper

**CTranslate2** — MIT License. Fast inference engine for Transformer models.
https://github.com/OpenNMT/CTranslate2

**Tokenizers** — Apache License 2.0. Hugging Face's tokenizer library, used transitively by faster-whisper.
https://github.com/huggingface/tokenizers

**OpenAI Whisper model weights** — MIT License. Downloaded on first transcription from the Hugging Face Hub. Cached locally in `~/.cache/huggingface/`.
https://github.com/openai/whisper

**sounddevice** — MIT License. PortAudio binding used for microphone capture.
https://python-sounddevice.readthedocs.io/

**PortAudio** — MIT License. Cross-platform audio I/O library, bundled via the sounddevice wheel.
http://www.portaudio.com/

**soundcard** — BSD-3-Clause License. Pure-Python audio I/O with WASAPI loopback support; used for system-audio capture on Windows.
https://github.com/bastibe/SoundCard

**NumPy** — BSD-3-Clause License.
https://numpy.org/

**Python and the Tk/Tcl widget toolkit** — Python Software Foundation License.
https://www.python.org/

## Build / packaging dependencies

**PyInstaller** — GPL with a runtime exception, allowing distribution of bundled applications under any license. Used only to produce `Tellix.exe`; not redistributed inside the binary.
https://pyinstaller.org/

**ReportLab** — BSD-3-Clause License. Used by `tools/render_docs.py` to render the Help and Release PDFs.
https://www.reportlab.com/

## FFmpeg (not bundled by default)

Tellix invokes **FFmpeg** at runtime to capture the screen and mux the final recording. FFmpeg is **not** included in this repository or in the prebuilt `Tellix.exe` published on the Releases page — users supply their own `bin/ffmpeg.exe`. This is intentional, because FFmpeg's licensing depends on which build is used:

- **LGPL builds** (FFmpeg compiled without `--enable-gpl`) can be redistributed alongside MIT-licensed software with only attribution and a copy of the LGPL.
- **GPL builds** (FFmpeg compiled with `--enable-gpl`, common in the popular static Windows distributions on gyan.dev) require any binary that bundles them to be distributed under GPL-compatible terms. Tellix's MIT license is permissive enough to allow this on the user's side, but it means you can't ship a closed-source product that bundles a GPL FFmpeg.

If you fork Tellix and bundle FFmpeg inside the binary you distribute, you must respect the FFmpeg license you ship with. For details:
https://ffmpeg.org/legal.html

## Trademarks

"Windows", "Google Meet", "Zoom", "Microsoft Teams", and "YouTube" are trademarks of their respective owners and are mentioned in Tellix's documentation only for descriptive purposes. Tellix has no affiliation with any of them.
