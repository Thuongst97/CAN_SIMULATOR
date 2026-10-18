<#
.SYNOPSIS
    Flash a CANable 2.0 (board in boot mode: hold BOOT while plugging it in).

.DESCRIPTION
    scripts\flash_canable.cmd                 official image firmware\canable2_stm32\bin\canable2-b158aa7.bin
    scripts\flash_canable.cmd -Built          the newest image built by scripts\build_firmware.cmd
    scripts\flash_canable.cmd -Bin <file>     any image
    scripts\flash_canable.cmd -NoBackup       skip saving the current firmware to firmware\canable2_stm32\backup\

    Front-end for firmware\canable2_stm32\flash.ps1 (checksum, backup, dfu-util write). Afterwards it lists the
    adapters with tools\canable_info.py. Guide: docs\CANABLE_FIRMWARE.md
#>
param(
    [string]$Bin,
    [switch]$Built,
    [switch]$NoBackup
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_tools.ps1")
$Root = Split-Path $PSScriptRoot -Parent
$Fw = Join-Path $Root "firmware\canable2_stm32"

if ($Built) {
    $img = Get-ChildItem (Join-Path $Fw "source\build\canable2-*.bin") -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $img) { Write-Host "No built image - run scripts\build_firmware.cmd first." -ForegroundColor Red; exit 2 }
    $Bin = $img.FullName
}
$flashArgs = @{}
if ($Bin) { $flashArgs["Bin"] = $Bin }
if ($NoBackup) { $flashArgs["NoBackup"] = $true }
& (Join-Path $Fw "flash.ps1") @flashArgs
$code = $LASTEXITCODE
if ($code -eq 0) {
    Write-Host ""
    Read-Host "Unplug the board, plug it back in WITHOUT holding BOOT, then press Enter" | Out-Null
    $uv = Find-Uv
    if ($uv) { & $uv run --frozen python (Join-Path $Root "tools\canable_info.py") }
}
exit $code
