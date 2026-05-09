@echo off
rem List current-round candidates and save to candidates.json
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0list_candidates.py" %*
echo.
pause
