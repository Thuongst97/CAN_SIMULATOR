<#
.SYNOPSIS
    Start AutoSim Studio from the sources (scripts\run.cmd). Run scripts\setup.cmd once first.
#>
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_tools.ps1")
Set-Location (Split-Path $PSScriptRoot -Parent)
$uv = Find-Uv
if (-not $uv) { Write-Host "uv not found - run scripts\setup.cmd first." -ForegroundColor Red; exit 2 }
& $uv run --frozen autosim
