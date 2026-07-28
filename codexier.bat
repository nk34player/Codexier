@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" codexier\main.py %*
) else (
    python codexier\main.py %*
)
set "CODEXIER_EXIT=%ERRORLEVEL%"

if "%CODEXIER_EXIT%"=="130" (
    endlocal & exit /b 130
)

endlocal & exit /b %CODEXIER_EXIT%
