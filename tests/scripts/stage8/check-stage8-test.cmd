@echo off
setlocal
set "script_dir=%~dp0"
cd /d "%script_dir%..\..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%script_dir%check-stage8-test.ps1"
exit /b %ERRORLEVEL%
