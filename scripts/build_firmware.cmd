@echo off
rem Runs build_firmware.ps1 with any arguments (works without changing the PowerShell execution policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_firmware.ps1" %*
exit /b %errorlevel%
