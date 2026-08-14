@echo off
setlocal
cd /d "%~dp0"
set "DEMO_URL=http://127.0.0.1:8010"

where docker >nul 2>nul
if errorlevel 1 (
  echo [DAIBM-SCF] Docker Desktop is not installed or docker.exe is not in PATH.
  echo [DAIBM-SCF] Docker Desktop не установлен. / 未检测到 Docker Desktop。
  exit /b 1
)

docker info >nul 2>nul
if errorlevel 1 (
  echo [DAIBM-SCF] Start Docker Desktop and wait until it is ready.
  echo [DAIBM-SCF] Запустите Docker Desktop. / 请先启动 Docker Desktop。
  exit /b 1
)

echo [DAIBM-SCF] Building and starting PostgreSQL and the demo...
docker compose up --build -d
if errorlevel 1 exit /b 1

echo [DAIBM-SCF] Waiting for the verified health endpoint...
for /L %%I in (1,1,60) do (
  powershell -NoProfile -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri '%DEMO_URL%/api/health' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>nul
  if not errorlevel 1 goto ready
  timeout /t 1 /nobreak >nul
)

echo [DAIBM-SCF] Startup timed out. Showing diagnostic logs.
echo [DAIBM-SCF] Истекло время запуска. / 启动超时，请查看日志。
docker compose logs --tail 100
exit /b 1

:ready
echo [DAIBM-SCF] PostgreSQL and the application are healthy.
start "" "%DEMO_URL%"
echo [DAIBM-SCF] Opened %DEMO_URL%
endlocal
