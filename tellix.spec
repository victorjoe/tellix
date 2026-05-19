# tellix.spec - PyInstaller build spec for Tellix
#
# Produces a single Windows executable: dist/Tellix.exe
#
# Build:
#   pip install pyinstaller
#   pyinstaller tellix.spec --clean --noconfirm
#
# Notes
# - ffmpeg.exe MUST exist at bin/ffmpeg.exe before building. Download a
#   static Windows build from gyan.dev and drop it in.
# - The Whisper model is NOT bundled. It downloads to the user's
#   ~\.cache\huggingface\ on first run. This keeps the .exe under ~250 MB.
# - UPX compression is OFF: it can corrupt CTranslate2's DLLs.
# - First launch of a --onefile bundle is slow (~5-10s) because the
#   bootloader extracts everything to a temp dir. After that it's fast.

from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

APP_NAME = "Tellix"
APP_ROOT = Path(SPECPATH)

# --- Native deps that PyInstaller often misses ---
hiddenimports = []
hiddenimports += collect_submodules("faster_whisper")
hiddenimports += collect_submodules("ctranslate2")
hiddenimports += collect_submodules("tokenizers")
hiddenimports += collect_submodules("soundcard")
hiddenimports += ["_sounddevice", "_sounddevice_data", "cffi", "numpy"]

# --- Data files (model assets, tokenizer vocab, soundcard platform shims) ---
datas = []
datas += collect_data_files("faster_whisper")
datas += collect_data_files("ctranslate2")
datas += collect_data_files("tokenizers")
datas += collect_data_files("soundcard")
datas += collect_data_files("sounddevice")

# --- Native DLLs ---
binaries = []
binaries += collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("sounddevice")
binaries += collect_dynamic_libs("numpy")

# --- Bundle ffmpeg.exe ---
ffmpeg_path = APP_ROOT / "bin" / "ffmpeg.exe"
if not ffmpeg_path.exists():
    raise SystemExit(
        f"\nMissing {ffmpeg_path}\n"
        "Download a static FFmpeg build for Windows (e.g. from gyan.dev) "
        "and place ffmpeg.exe in the bin\\ folder before building.\n"
    )
binaries.append((str(ffmpeg_path), "bin"))
ffmpeg_hash_path = ffmpeg_path.with_suffix(ffmpeg_path.suffix + ".sha256")
if ffmpeg_hash_path.exists():
    datas.append((str(ffmpeg_hash_path), "bin"))


a = Analysis(
    ["app.py"],
    pathex=[str(APP_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Skip libs we don't use - keeps the bundle smaller
        "torch", "tensorflow", "matplotlib", "PyQt5", "PyQt6",
        "pandas", "scipy", "IPython", "notebook", "jupyter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX can corrupt ctranslate2 DLLs
    upx_exclude=[],
    # Extract the one-file bundle beside the executable instead of %TEMP%.
    # This avoids startup failures on machines with a nearly full C: drive.
    runtime_tmpdir=".",
    console=False,       # windowed Tk app, no terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='tellix.ico',  # uncomment when you add an icon
)
