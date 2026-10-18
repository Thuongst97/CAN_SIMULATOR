# Build the Windows installer.  Usage: scripts\build.cmd [-SkipInstaller]
# Requires: uv and Inno Setup 6 - both installed by: scripts\setup.cmd -Installer
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. (Join-Path $PSScriptRoot "_tools.ps1")
$uvExe = Find-Uv
if (-not $uvExe) { throw "uv not found - run scripts\setup.cmd first" }
Set-Alias uv $uvExe -Scope Script

uv sync --frozen --group build            # exact versions from uv.lock
$version = (uv run --frozen python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
uv run --frozen --group build python -m PyInstaller packaging\autosim.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE) { throw "PyInstaller failed" }
if ($SkipInstaller) { return }

$iscc = Find-Iscc
if (-not $iscc) { throw "Inno Setup not found. Run: scripts\setup.cmd -Installer" }
& $iscc "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Write-Host "Installer: dist\installer\AutoSimStudio-Setup-$version.exe"
