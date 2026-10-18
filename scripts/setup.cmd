@echo off
rem Runs setup.ps1 with any arguments (works without changing the PowerShell execution policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
exit /b %errorlevel%
