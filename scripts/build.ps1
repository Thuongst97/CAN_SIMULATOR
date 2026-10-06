# Build the Windows installer.  Usage: .\scripts\build.ps1 [-SkipInstaller]
# Requires: uv (https://docs.astral.sh/uv) and Inno Setup 6 (winget install JRSoftware.InnoSetup)
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

uv sync --frozen --group build            # exact versions from uv.lock
$version = (uv run --frozen python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
uv run --frozen --group build python -m PyInstaller packaging\autosim.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE) { throw "PyInstaller failed" }
if ($SkipInstaller) { return }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "Inno Setup not found. Run: winget install JRSoftware.InnoSetup" }
& $iscc "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Write-Host "Installer: dist\installer\AutoSimStudio-Setup-$version.exe"
