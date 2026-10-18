<#
.SYNOPSIS
    Set up AutoSim Studio on a new Windows machine (prerequisites, Python environment, tests).

.DESCRIPTION
    scripts\setup.cmd                 app: uv + Python 3.11 + exact dependencies (uv.lock) + test run
    scripts\setup.cmd -Installer      + Inno Setup 6 and the PyInstaller group   (to build the Windows installer)
    scripts\setup.cmd -Firmware       + dfu-util (project-local), Arm GNU toolchain, make (CANable firmware)
    scripts\setup.cmd -All            everything above
    scripts\setup.cmd -Check          only report what is installed; changes nothing

    Programs are installed with winget when it works (Windows 10 1809+ / 11); dfu-util is unpacked into .tools\ in
    the project, so PATH is not changed. Needs Git for Windows for the firmware build (its sh / sed / mkdir).
    Guide: docs\MIGRATION.md
#>
param(
    [switch]$Installer,
    [switch]$Firmware,
    [switch]$All,
    [switch]$Check,
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
if ($All) { $Installer = $true; $Firmware = $true }

. (Join-Path $PSScriptRoot "_tools.ps1")

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  [ok]      $text" -ForegroundColor Green }
function Missing($text) { Write-Host "  [missing] $text" -ForegroundColor Yellow }

function Install-Winget([string]$Id, [string]$What) {
    if ($Check) { Missing "$What  (would run: winget install --id $Id -e)"; return $false }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Missing "$What - winget is not available: install it manually (see docs\MIGRATION.md)"
        return $false
    }
    Write-Host "  installing $What (winget $Id) ..."
    winget install --id $Id -e --source winget
    if ($LASTEXITCODE -ne 0) { Missing "$What - winget failed (exit $LASTEXITCODE): install it manually"; return $false }
    Update-SessionPath
    return $true
}

# ---- 1. app ------------------------------------------------------------------------------------------------
Step "App: uv, Python 3.11, dependencies"
$uv = Find-Uv
if ($uv) {
    Ok "uv $((& $uv --version) -replace '^uv\s+','')  ($uv)"
} elseif ($Check) {
    Missing "uv  (would install from https://astral.sh/uv)"
} else {
    if (-not (Install-Winget "astral-sh.uv" "uv")) {
        Write-Host "  installing uv with the official installer (https://astral.sh/uv/install.ps1) ..."
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        Update-SessionPath
    }
    $uv = Find-Uv
    if (-not $uv) { throw "uv could not be installed - see https://docs.astral.sh/uv/getting-started/installation/" }
    Ok "uv installed ($uv)"
}
if ($uv -and -not $Check) {
    & $uv sync --frozen                     # Python 3.11 (.python-version) + exact versions from uv.lock
    if ($LASTEXITCODE) { throw "uv sync failed" }
    Ok "environment .venv ready (uv.lock)"
    if (-not $SkipTests) {
        Step "Tests (no hardware needed)"
        & $uv run --frozen pytest -q
        if ($LASTEXITCODE) { Write-Host "  some tests failed - see above" -ForegroundColor Red } else { Ok "all tests passed" }
    }
} elseif ($uv) {
    if (Test-Path (Join-Path $Root ".venv")) { Ok ".venv exists" } else { Missing ".venv  (would run: uv sync --frozen)" }
}

# ---- 2. Windows installer ----------------------------------------------------------------------------------
if ($Installer -or $Check) {
    Step "Windows installer: Inno Setup 6, PyInstaller"
    if (Find-Iscc) { Ok "Inno Setup ($(Find-Iscc))" }
    elseif ($Installer -or -not $Check) { [void](Install-Winget "JRSoftware.InnoSetup" "Inno Setup 6") }
    else { Missing "Inno Setup 6  (setup.cmd -Installer)" }
    if ($uv -and $Installer -and -not $Check) {
        & $uv sync --frozen --group build
        if ($LASTEXITCODE) { throw "uv sync --group build failed" }
        Ok "PyInstaller group installed - build with: scripts\build.cmd"
    }
}

# ---- 3. CANable firmware -----------------------------------------------------------------------------------
if ($Firmware -or $Check) {
    Step "CANable firmware: dfu-util (flash), Arm GNU toolchain + make (build from source)"
    $dfu = Find-DfuUtil
    if ($dfu) { Ok "dfu-util ($dfu)" }
    elseif ($Check) { Missing "dfu-util  (setup.cmd -Firmware unpacks it into .tools\dfu-util)" }
    else {
        $dest = Join-Path $Root ".tools\dfu-util"
        $tmp = Join-Path $env:TEMP "dfu-util-0.11-binaries.tar.xz"
        Write-Host "  downloading dfu-util 0.11 (dfu-util.sourceforge.net) ..."
        Invoke-WebRequest -Uri "https://dfu-util.sourceforge.net/releases/dfu-util-0.11-binaries.tar.xz" -OutFile $tmp -UseBasicParsing
        New-Item -ItemType Directory -Force $dest | Out-Null
        tar -xf $tmp -C $dest                # Windows 10+ tar (bsdtar) reads .tar.xz
        Remove-Item $tmp
        $dfu = Find-DfuUtil
        if ($dfu) { Ok "dfu-util unpacked ($dfu)" } else { Missing "dfu-util - unpack failed; see docs\CANABLE_FIRMWARE.md" }
    }
    Write-Host "  note: flashing also needs the WinUSB driver for 'STM32 BOOTLOADER' once per machine (Zadig, see docs)."
    if (Find-ArmGcc) { Ok "arm-none-eabi-gcc ($(Find-ArmGcc))" }
    elseif ($Firmware -and -not $Check) { [void](Install-Winget "Arm.GnuArmEmbeddedToolchain" "Arm GNU toolchain") }
    else { Missing "arm-none-eabi-gcc  (only to build the firmware from source)" }
    if (Find-Make) { Ok "make ($(Find-Make))" }
    elseif ($Firmware -and -not $Check) { [void](Install-Winget "ezwinports.make" "GNU make") }
    else { Missing "make  (only to build the firmware from source)" }
    if (Find-GitUsrBin) { Ok "Git for Windows Unix tools ($(Find-GitUsrBin))" }
    else { Missing "Git for Windows (sh / sed / mkdir for the firmware Makefile): https://git-scm.com/download/win" }
}

Write-Host ""
Write-Host "Next: scripts\run.cmd (start the app) | scripts\build.cmd (installer) | scripts\flash_canable.cmd (firmware)"
