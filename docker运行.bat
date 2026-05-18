@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem 一键启动 FlareSolverr 容器 (过 Cloudflare 5s 盾用)
rem  1) Docker Desktop 没开 -> 自动唤起 + 等 daemon 就绪
rem  2) flaresolverr 容器没有 -> docker run 拉镜像并创建容器
rem  3) flaresolverr 容器在 -> 没跑就 docker start，跑着就提示已就绪

set "DD=C:\Program Files\Docker\Docker\Docker Desktop.exe"
set "CONTAINER=flaresolverr"
set "IMAGE=ghcr.io/flaresolverr/flaresolverr:latest"
set "PORT=8191"

rem ---- docker 命令在不在 PATH ----
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 没找到 docker 命令。请先安装 Docker Desktop:
    echo   https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)

rem ---- daemon 在不在跑 ----
echo [INFO] 检查 Docker daemon 状态 ...
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready

echo [INFO] Docker daemon 未启动，正在唤起 Docker Desktop ...
if not exist "%DD%" (
    echo [WARN] 找不到 Docker Desktop 默认路径: %DD%
    echo        请手动启动 Docker Desktop 后重新运行本脚本。
    pause
    exit /b 1
)
start "" "%DD%"

set /a TRIES=0
:wait_docker
timeout /t 2 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a TRIES+=1
if !TRIES! geq 60 (
    echo.
    echo [ERROR] 等了 120s Docker daemon 还没起来，退出
    echo [HINT]  打开 Docker Desktop 看任务栏图标是不是稳定 ^(不再闪烁^)，再重试本脚本
    pause
    exit /b 1
)
<nul set /p "=."
goto wait_docker

:docker_ready
echo.
echo [OK] Docker daemon 就绪

rem ---- 容器是否已经存在 ----
set "EXISTING="
for /f "delims=" %%i in ('docker ps -a --filter "name=^%CONTAINER%$" --format "{{.Names}}"') do set "EXISTING=%%i"

if not defined EXISTING goto create_container

rem 容器存在 — 看是否在跑
set "RUNNING="
for /f "delims=" %%i in ('docker ps --filter "name=^%CONTAINER%$" --format "{{.Names}}"') do set "RUNNING=%%i"

if defined RUNNING (
    echo [OK] 容器 %CONTAINER% 已在运行
    goto done
)

echo [INFO] 容器 %CONTAINER% 已存在但未运行，正在启动 ...
docker start %CONTAINER%
if errorlevel 1 (
    echo [ERROR] docker start 失败
    pause
    exit /b 1
)
echo [OK] 容器 %CONTAINER% 已启动
goto done

:create_container
echo [INFO] 容器 %CONTAINER% 不存在，准备拉镜像并创建 ^(首次较慢^) ...
docker run -d --name=%CONTAINER% --restart=unless-stopped -p %PORT%:%PORT% %IMAGE%
if errorlevel 1 (
    echo [ERROR] docker run 失败 ^(镜像下载失败 / 端口 %PORT% 被占用 / 其它^)
    pause
    exit /b 1
)
echo [OK] 容器 %CONTAINER% 已创建并启动

:done
echo.
echo ================================================
echo  FlareSolverr 监听: http://localhost:%PORT%
echo  在 GUI 的 "FlareSolverr URL" 框填写该地址
echo ================================================
echo.
pause
exit /b 0
