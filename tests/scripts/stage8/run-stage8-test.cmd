@echo off
setlocal
set "script_dir=%~dp0"
cd /d "%script_dir%..\..\.."

if "%~1"=="" (
    echo Usage: tests\scripts\stage8\run-stage8-test.cmd ^<python-script^> [arguments]
    echo Example: tests\scripts\stage8\run-stage8-test.cmd backend\test_scripts\stage8_manifest.py --output tests\artifacts\stage8\stage8-test-manifest.json
    exit /b 2
)

set "python_script=%~1"
shift
set "python_args=%1 %2 %3 %4 %5 %6 %7 %8 %9"
if "%~1"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%script_dir%run-stage8-test.ps1" -PythonScript "%python_script%"
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%script_dir%run-stage8-test.ps1" -PythonScript "%python_script%" -PythonArgumentLine "%python_args%"
)
exit /b %ERRORLEVEL%
