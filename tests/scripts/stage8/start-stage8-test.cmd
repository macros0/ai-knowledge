@echo off
setlocal
cd /d "%~dp0..\..\.."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-stage8-test.ps1"
set "exit_code=%ERRORLEVEL%"

if not "%exit_code%"=="0" (
    echo.
    echo Failed to start the Stage 8 test contour. Code: %exit_code%
    pause
)

exit /b %exit_code%
