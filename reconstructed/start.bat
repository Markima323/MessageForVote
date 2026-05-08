@echo off
rem StarRailVote reconstructed — launcher
rem Uses the venv created at .venv/ next to this file.
rem Starts the GUI; on error the window stays open so you can read the message.

chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] venv not found at %PY%
    echo Run the following to create it:
    echo     py -3.13 -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo     .venv\Scripts\python -m playwright install chromium
    pause
    exit /b 1
)

"%PY%" "%~dp0vote_reconstructed.py"
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo Program exited with errorlevel %RC%
    pause
)
exit /b %RC%
