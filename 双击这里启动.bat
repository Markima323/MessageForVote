@echo off
rem StarRailVote reconstructed — launcher (root level)
rem 调用 reconstructed/.venv 跑 reconstructed/vote_reconstructed.py
rem 出错时窗口保留，方便看错误信息

chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0reconstructed\.venv\Scripts\python.exe"
set "SCRIPT=%~dp0reconstructed\vote_reconstructed.py"

if not exist "%PY%" (
    echo [ERROR] 没找到 venv: %PY%
    echo 请先双击 install.bat 安装依赖。
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [ERROR] 没找到主脚本: %SCRIPT%
    pause
    exit /b 1
)

"%PY%" "%SCRIPT%"
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo Program exited with errorlevel %RC%
    pause
)
exit /b %RC%
