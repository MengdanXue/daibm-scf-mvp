@echo off
setlocal
cd /d "%~dp0"
if not defined MVP_PORT set "MVP_PORT=8010"
set "DEMO_URL=http://127.0.0.1:%MVP_PORT%"

where docker >nul 2>nul
if errorlevel 1 goto no_docker

docker info >nul 2>nul
if errorlevel 1 goto docker_stopped

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ops\init-env.ps1"
if errorlevel 1 exit /b 1
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%~dp0.env") do set "%%A=%%B"
echo [DAIBM-SCF] Building and starting PostgreSQL and the demo...
docker compose up --build -d
if errorlevel 1 exit /b 1

echo [DAIBM-SCF] Waiting for the verified health endpoint...
set /a HEALTH_ATTEMPT=0

:wait_health
powershell -NoProfile -Command "try { $payload = Invoke-RestMethod -Uri '%DEMO_URL%/api/health' -TimeoutSec 2; if (($payload.status -eq 'ok') -and ($payload.database.backend -eq 'postgresql') -and ($payload.database.reachable -eq $true) -and ($payload.ledger.valid -eq $true) -and ($payload.research_core.status -eq 'ready')) { exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 goto ready
set /a HEALTH_ATTEMPT+=1
if %HEALTH_ATTEMPT% GEQ 60 goto startup_timeout
powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>nul
goto wait_health

:startup_timeout
echo [DAIBM-SCF] Startup timed out. Showing diagnostic logs.
powershell -NoProfile -Command "$messages = Get-Content -Raw -Encoding utf8 'launcher-messages.json' | ConvertFrom-Json; Write-Host ('[DAIBM-SCF] ' + $messages.startup_timeout)"
docker compose logs --tail 100
exit /b 1

:no_docker
echo [DAIBM-SCF] Docker Desktop is not installed or docker.exe is not in PATH.
powershell -NoProfile -Command "$messages = Get-Content -Raw -Encoding utf8 'launcher-messages.json' | ConvertFrom-Json; Write-Host ('[DAIBM-SCF] ' + $messages.no_docker)"
exit /b 1

:docker_stopped
echo [DAIBM-SCF] Start Docker Desktop and wait until it is ready.
powershell -NoProfile -Command "$messages = Get-Content -Raw -Encoding utf8 'launcher-messages.json' | ConvertFrom-Json; Write-Host ('[DAIBM-SCF] ' + $messages.docker_stopped)"
exit /b 1

:ready
echo [DAIBM-SCF] PostgreSQL and the application are healthy.
start "" "%DEMO_URL%"
echo [DAIBM-SCF] Opened %DEMO_URL%
echo [DAIBM-SCF] Demo accounts use DAIBM_DEMO_PASSWORD from .env: %DAIBM_DEMO_PASSWORD%
endlocal
