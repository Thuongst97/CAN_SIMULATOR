@echo off
rem Runs flash_canable.ps1 with any arguments (works without changing the PowerShell execution policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0flash_canable.ps1" %*
exit /b %errorlevel%
