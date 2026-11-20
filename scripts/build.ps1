# Build the Windows installer (desktop app + web mode in one setup).  Usage: scripts\build.cmd [-SkipInstaller]
# Requires: uv and Inno Setup 6 - both installed by: scripts\setup.cmd -Installer
# Output:   dist\AutoSimStudio\ (desktop app), dist\AutoSimWeb\ (web mode),
#           dist\installer\AutoSimStudio-Setup-<version>.exe (both, as selectable components)
#           dist\installer\AutoSimStudioWeb-Setup-<version>.exe (web mode only, smaller)
param([switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
. (Join-Path $PSScriptRoot "_tools.ps1")
$uvExe = Find-Uv
if (-not $uvExe) { throw "uv not found - run scripts\setup.cmd first" }
Set-Alias uv $uvExe -Scope Script

uv sync --frozen --group build --extra web    # exact versions from uv.lock (web: aiohttp)
$version = (uv run --frozen python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
uv run --frozen --group build python -m PyInstaller packaging\autosim.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE) { throw "PyInstaller (desktop app) failed" }
uv run --frozen --group build python -m PyInstaller packaging\autosim_web.spec --noconfirm --distpath dist --workpath build\web
if ($LASTEXITCODE) { throw "PyInstaller (web mode) failed" }
if ($SkipInstaller) { return }

$iscc = Find-Iscc
if (-not $iscc) { throw "Inno Setup not found. Run: scripts\setup.cmd -Installer" }
& $iscc "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
& $iscc "/DAppVersion=$version" packaging\installer_web.iss
if ($LASTEXITCODE) { throw "Inno Setup (web installer) failed" }
Write-Host "Installers: dist\installer\AutoSimStudio-Setup-$version.exe (desktop + web)"
Write-Host "            dist\installer\AutoSimStudioWeb-Setup-$version.exe (web mode only)"
