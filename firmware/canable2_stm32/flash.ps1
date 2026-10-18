<#
.SYNOPSIS
    Flash the CANable 2.0 (STM32G431) slcan firmware with dfu-util, after an optional backup.

.DESCRIPTION
    1. Put ONE board in boot mode: hold BOOT while plugging it in (Device Manager shows "STM32 BOOTLOADER").
    2. Run:   pwsh -File firmware\canable2_stm32\flash.ps1
    The script checks the binary against bin\SHA256SUMS, saves the current flash to backup\ (unless -NoBackup),
    writes the firmware at 0x08000000 and starts it. Needs dfu-util on PATH and the WinUSB driver for
    "STM32 BOOTLOADER" (Zadig). Full guide: docs\CANABLE_FIRMWARE.md

.PARAMETER Bin
    Firmware image to flash (default: bin\canable2-b158aa7.bin, the official slcan + CAN FD build).

.PARAMETER NoBackup
    Skip reading the current flash into backup\ first.
#>
param(
    [string]$Bin = (Join-Path $PSScriptRoot "bin\canable2-b158aa7.bin"),
    [switch]$NoBackup
)
$ErrorActionPreference = "Stop"
$Dfu = Get-Command dfu-util -ErrorAction SilentlyContinue
$tools = Join-Path $PSScriptRoot "..\..\scripts\_tools.ps1"       # also finds the project-local .tools\dfu-util
if (-not $Dfu -and (Test-Path $tools)) {
    . $tools
    $found = Find-DfuUtil
    if ($found) { $Dfu = Get-Command $found }
}
if (-not $Dfu) {
    Write-Host "dfu-util not found. Run scripts\setup.cmd -Firmware (puts it in .tools\) or use the web updater:" -ForegroundColor Red
    Write-Host "  https://canable.io/updater/canable2.html  (Chrome / Edge)"
    exit 2
}
if (-not (Test-Path $Bin)) { Write-Host "Firmware image not found: $Bin" -ForegroundColor Red; exit 2 }

# integrity check for the images listed in bin\SHA256SUMS
$Sums = Join-Path $PSScriptRoot "bin\SHA256SUMS"
$Name = Split-Path $Bin -Leaf
if (Test-Path $Sums) {
    $line = Get-Content $Sums | Where-Object { $_ -match [regex]::Escape($Name) } | Select-Object -First 1
    if ($line) {
        $expected = ($line -split '\s+')[0].ToLower()
        $actual = (Get-FileHash $Bin -Algorithm SHA256).Hash.ToLower()
        if ($expected -ne $actual) { Write-Host "SHA-256 mismatch for $Name - file damaged?" -ForegroundColor Red; exit 3 }
        Write-Host "SHA-256 OK: $Name"
    }
}

# exactly one board in boot mode
$list = & $Dfu.Source -l 2>&1 | Out-String
$count = ([regex]::Matches($list, "0483:df11")).Count
if ($count -eq 0) {
    Write-Host "No board in boot mode (USB 0483:DF11)." -ForegroundColor Yellow
    Write-Host "Hold BOOT while plugging the CANable in; if Device Manager shows 'STM32 BOOTLOADER' but dfu-util"
    Write-Host "does not, install the WinUSB driver for it with Zadig (https://zadig.akeo.ie/)."
    exit 4
}
if (-not ($list -match "Internal Flash")) { Write-Host $list }

if (-not $NoBackup) {
    $dir = Join-Path $PSScriptRoot "backup"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $out = Join-Path $dir ("canable2-backup-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".bin")
    Write-Host "Backing up the current firmware (128 KB) to $out ..."
    & $Dfu.Source -d 0483:df11 -a 0 -s 0x08000000:131072 -U $out
    if ($LASTEXITCODE -ne 0) { Write-Host "Backup failed (read protection?) - rerun with -NoBackup to flash anyway." -ForegroundColor Yellow; exit 5 }
}

Write-Host "Flashing $Name ..."
& $Dfu.Source -w -d 0483:df11 -c 1 -i 0 -a 0 -s 0x08000000:leave -D $Bin
# dfu-util sometimes reports an error on the final status request after ':leave' although the write succeeded
Write-Host ""
Write-Host "Done (dfu-util exit code $LASTEXITCODE). Unplug and replug the board WITHOUT holding BOOT, then check:"
Write-Host "  uv run python tools/canable_info.py"
