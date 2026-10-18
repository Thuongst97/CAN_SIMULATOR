<#
.SYNOPSIS
    Build the CANable 2.0 slcan firmware from firmware\canable2_stm32\source.

.DESCRIPTION
    scripts\build_firmware.cmd                       version string "b158aa7" (the upstream commit of the source)
    scripts\build_firmware.cmd -Version b158aa7-mine for a modified build (shows in tools\canable_info.py / AutoSim)
    scripts\build_firmware.cmd -Clean                remove the previous build first

    Needs arm-none-eabi-gcc and make (scripts\setup.cmd -Firmware) and Git for Windows (its sh, sed, mkdir and rm are
    put on PATH for make, as the upstream Makefile expects a Unix shell).
    Output: firmware\canable2_stm32\source\build\canable2-<version>.bin - flash with scripts\flash_canable.cmd -Built
#>
param(
    [string]$Version = "b158aa7",
    [string]$Remote = "github.com/normaldotcom/canable2-fw.git",
    [switch]$Clean
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_tools.ps1")
$Src = Join-Path (Split-Path $PSScriptRoot -Parent) "firmware\canable2_stm32\source"

$gcc = Find-ArmGcc
$make = Find-Make
$usr = Find-GitUsrBin
$missing = @()
if (-not $gcc) { $missing += "arm-none-eabi-gcc (Arm GNU toolchain)" }
if (-not $make) { $missing += "make" }
if (-not $usr) { $missing += "Git for Windows (sh/sed/mkdir)" }
if ($missing) {
    Write-Host ("Missing: " + ($missing -join ", ") + ".  Run: scripts\setup.cmd -Firmware") -ForegroundColor Red
    exit 2
}

# make + the Unix tools of Git for Windows + the Arm toolchain, for this process only
$env:Path = (Split-Path $gcc -Parent) + ";" + (Split-Path $make -Parent) + ";" + $usr + ";" + $env:Path
Push-Location $Src
try {
    if ($Clean -and (Test-Path build)) { Remove-Item -Recurse -Force build }
    Write-Host "Building canable2 firmware $Version with $(& $gcc -dumpversion) ..."
    & $make "GIT_VERSION=$Version" "GIT_REMOTE=$Remote" -j4
    if ($LASTEXITCODE) { throw "make failed (exit $LASTEXITCODE)" }
    $bin = Join-Path $Src "build\canable2-$Version.bin"
    if (-not (Test-Path $bin)) { throw "build finished but $bin is missing" }
    $hash = (Get-FileHash $bin -Algorithm SHA256).Hash.ToLower()
    Write-Host ""
    Write-Host "Built: $bin  ($((Get-Item $bin).Length) bytes, sha256 $hash)" -ForegroundColor Green
    Write-Host "Flash it: scripts\flash_canable.cmd -Built"
} finally {
    Pop-Location
}
