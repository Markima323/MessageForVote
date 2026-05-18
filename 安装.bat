@echo off
rem StarRailVote reconstructed — 一键安装/升级依赖
rem 在 reconstructed/.venv 创建 Python 虚拟环境（自动找最高可用版本），
rem 安装 reconstructed/requirements.txt 列出的所有依赖，
rem 并用 Playwright 下载 Chromium 浏览器。
rem
rem 支持 Python 3.13 / 3.12 / 3.11（按此顺序尝试，找到就用）。

chcp 65001 >nul
pushd "%~dp0" 2>nul
if errorlevel 1 (
    echo [ERROR] 无法切换到脚本所在目录: %~dp0
    set "EXITCODE=1"
    goto :end
)

set "VENV=reconstructed\.venv"
set "PY=%VENV%\Scripts\python.exe"
set "REQ=reconstructed\requirements.txt"

set "EXITCODE=0"

echo [INFO] 工作目录: %CD%
echo [INFO] 检查依赖清单: %CD%\%REQ%

if not exist "%REQ%" (
    echo.
    echo [ERROR] 没找到依赖清单: %CD%\%REQ%
    echo [HINT]  当前目录下应该有一个 reconstructed 文件夹，
    echo         其中包含 requirements.txt。
    echo         请确认文件夹结构完整，或者把整个文件夹放到
    echo         "纯英文路径"下（避免路径含中文/特殊字符）再试。
    dir /b
    set "EXITCODE=1"
    goto :end
)

if not exist "%VENV%" (
    rem 找一个可用的 Python 版本：3.13 优先，依次降级到 3.11
    set "PYVER="

    py -3.13 --version >nul 2>&1
    if not errorlevel 1 set "PYVER=-3.13"

    if not defined PYVER (
        py -3.12 --version >nul 2>&1
        if not errorlevel 1 set "PYVER=-3.12"
    )

    if not defined PYVER (
        py -3.11 --version >nul 2>&1
        if not errorlevel 1 set "PYVER=-3.11"
    )

    if not defined PYVER (
        echo.
        echo [ERROR] 没找到 Python 3.11 / 3.12 / 3.13
        echo 请去 https://www.python.org/downloads/ 安装其中一个版本。
        echo 验证命令: py -3.11 --version
        set "EXITCODE=1"
        goto :end
    )

    echo [INFO] 用 Python %PYVER% 创建 venv 在 %CD%\%VENV%
    py %PYVER% -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] 创建 venv 失败
        set "EXITCODE=1"
        goto :end
    )
) else (
    echo [INFO] 复用现有 venv: %CD%\%VENV%
)

echo.
echo [INFO] 升级 pip ...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [WARN] pip 升级失败，尝试继续。
)

echo.
echo [INFO] 安装/升级依赖（按 reconstructed\requirements.txt） ...
"%PY%" -m pip install -r "%REQ%"
if errorlevel 1 (
    echo [ERROR] 依赖安装失败
    set "EXITCODE=1"
    goto :end
)

echo.
echo [INFO] 安装 Playwright Chromium 浏览器 ^(已存在则跳过^) ...
"%PY%" -m playwright install chromium
if errorlevel 1 (
    echo [WARN] Playwright Chromium 安装失败 ^(若你机器上已有 Chrome 也能用^)
)

echo.
echo [INFO] 安装 patchright (反检测版) Chromium ^(已存在则跳过^) ...
"%PY%" -m patchright install chromium
if errorlevel 1 (
    echo [WARN] patchright Chromium 安装失败 ^(脚本会回退到原版 Playwright^)
)

echo.
echo ================================================
echo  [DONE] 安装完成。现在可以双击 start.bat 启动。
echo ================================================

popd 2>nul

:end
echo.
echo [脚本结束] 按任意键关闭窗口...
pause >nul
exit /b %EXITCODE%
