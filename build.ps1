# build.ps1 - Build Tellix.exe with PyInstaller
#
# Usage: .\build.ps1
#
# Produces dist\Tellix.exe (single-file Windows executable).

$ErrorActionPreference = "Stop"

# 1. Sanity-check ffmpeg.exe is present
if (-not (Test-Path .\bin\ffmpeg.exe)) {
    Write-Error @"
Missing bin\ffmpeg.exe.

Download a static FFmpeg build for Windows (e.g. from https://www.gyan.dev/ffmpeg/builds/)
and place ffmpeg.exe in the bin\ folder before building.
"@
    exit 1
}

# 2. Make sure pyinstaller is installed in the active venv
Write-Host "Checking PyInstaller..." -ForegroundColor Cyan
python -m pip install --upgrade pyinstaller | Out-Null

# 3. Wipe previous build artifacts so the build is clean
if (Test-Path .\build) { Remove-Item -Recurse -Force .\build }
if (Test-Path .\dist)  { Remove-Item -Recurse -Force .\dist  }

# 4. Build
Write-Host "Building Tellix.exe (this can take a few minutes)..." -ForegroundColor Cyan
python -m PyInstaller tellix.spec --clean --noconfirm

# 5. Report
$exe = ".\dist\Tellix.exe"
if (Test-Path $exe) {
    $sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Done. Built: $(Resolve-Path $exe) ($sizeMb MB)" -ForegroundColor Green
    Write-Host ""
    Write-Host "First launch will be slow (PyInstaller extracts to a temp dir)."
    Write-Host "The Whisper model downloads on first transcribe; allow ~150 MB."
} else {
    Write-Error "Build failed - $exe not produced. Check the output above."
    exit 1
}
