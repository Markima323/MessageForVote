@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 把 reconstructed\vote_reconstructed.py 打包成单文件 exe
rem 输出: dist\StarRailVote.exe
rem 依赖：先双击过 安装.bat（保证 reconstructed\.venv 存在并装好依赖）
rem 注意：接收方电脑需要装 Chrome / Edge，本脚本不打包 Chromium 浏览器

set "VENV=reconstructed\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "ENTRY=reconstructed\vote_reconstructed.py"
set "ICON=vote\256x256.ico"

if not exist "%PY%" (
    echo [ERROR] 没找到 venv: %PY%
    echo 请先双击 安装.bat 安装依赖。
    pause
    exit /b 1
)

if not exist "%ENTRY%" (
    echo [ERROR] 没找到主脚本: %ENTRY%
    pause
    exit /b 1
)

if not exist "%ICON%" (
    echo [WARN] 没找到图标 %ICON%，将不嵌入图标
    set "ICON="
)

echo [INFO] 检查/安装 pyinstaller ...
"%PY%" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    "%PY%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] pyinstaller 安装失败
        pause
        exit /b 1
    )
)

echo.
echo [INFO] 开始打包 ...
echo.

if defined ICON (
    "%PY%" -m PyInstaller ^
        --noconfirm --clean ^
        --onefile --windowed ^
        --name "StarRailVote" ^
        --icon "%ICON%" ^
        --add-data "reconstructed\candidates.json;." ^
        --add-data "reconstructed\config.yaml;." ^
        --collect-all playwright ^
        --collect-all playwright_stealth ^
        --collect-all patchright ^
        "%ENTRY%"
) else (
    "%PY%" -m PyInstaller ^
        --noconfirm --clean ^
        --onefile --windowed ^
        --name "StarRailVote" ^
        --add-data "reconstructed\candidates.json;." ^
        --add-data "reconstructed\config.yaml;." ^
        --collect-all playwright ^
        --collect-all playwright_stealth ^
        --collect-all patchright ^
        "%ENTRY%"
)

if errorlevel 1 (
    echo.
    echo [ERROR] 打包失败，请看上面的报错
    pause
    exit /b 1
)

echo.
echo ================================================
echo  [DONE] 打包完成: %CD%\dist\StarRailVote.exe
echo ================================================
echo.
echo 提示:
echo   1. exe 同目录会自动生成 config.yaml 和 candidates.json
echo   2. 接收方电脑需安装 Chrome 或 Edge
echo   3. 若使用 Playwright 自带 chromium 需在目标机执行: playwright install chromium
echo.
pause
