@echo off
rem Runs run.ps1 with any arguments (works without changing the PowerShell execution policy).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %errorlevel%
