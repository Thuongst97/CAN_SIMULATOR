@echo off
rem Runs android_bridge.ps1 with any arguments (works without changing the PowerShell execution policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0android_bridge.ps1" %*
exit /b %errorlevel%
