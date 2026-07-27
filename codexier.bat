@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" codexier\main.py %*
) else (
    python codexier\main.py %*
)

echo.
echo Codexier finished. Press any key to close this window.
pause >nul
endlocal
