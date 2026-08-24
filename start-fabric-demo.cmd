@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "DOCKER_HOST="
set "DOCKER_CONTEXT=desktop-linux"
set "COMPOSE_FILE="
set "COMPOSE_PROJECT_NAME="
if not defined MVP_PORT set "MVP_PORT=8010"
set "DEMO_URL=http://127.0.0.1:%MVP_PORT%"

where docker >nul 2>nul || goto no_docker
docker --context desktop-linux info >nul 2>nul || goto docker_stopped
if /i "%~1"=="/clean" goto confirm_clean
if not "%~1"=="" goto usage

:start
powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.fabric_start_base)"
docker --context desktop-linux compose -f "%~dp0docker-compose.yml" --project-name daibm-scf-mvp up --build -d
if errorlevel 1 exit /b 1

powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.fabric_start_network)"
docker --context desktop-linux compose -f "%~dp0advanced\fabric\network\docker-compose.fabric.yml" --project-name daibm-fabric-demo up --build -d
if errorlevel 1 exit /b 1

powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.fabric_deploy)"
docker --context desktop-linux compose -f "%~dp0advanced\fabric\network\docker-compose.fabric.yml" --project-name daibm-fabric-demo exec -T cli bash /network/deploy-chaincode.sh
if errorlevel 1 exit /b 1

powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.fabric_gateway_check)"
docker --context desktop-linux compose -f "%~dp0advanced\fabric\network\docker-compose.fabric.yml" --project-name daibm-fabric-demo exec -T gateway node -e "fetch('http://127.0.0.1:8090/health').then(r=>{if(!r.ok)process.exit(1);return r.json()}).then(x=>{if(x.fabric!=='ready')process.exit(1)}).catch(()=>process.exit(1))"
if errorlevel 1 exit /b 1
start "" "%DEMO_URL%"
powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.fabric_ready + ' %DEMO_URL%')"
exit /b 0

:confirm_clean
powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.fabric_reset_warning)"
powershell -NoProfile -Command "$answer = Read-Host 'Type exactly RESET FABRIC'; if ($answer -ceq 'RESET FABRIC') { exit 0 }; exit 1"
if errorlevel 1 goto clean_cancelled
docker --context desktop-linux compose -f "%~dp0advanced\fabric\network\docker-compose.fabric.yml" --project-name daibm-fabric-demo down --remove-orphans
if errorlevel 1 exit /b 1
powershell -NoProfile -Command "$root = [IO.Path]::GetFullPath('%~dp0'); $target = [IO.Path]::GetFullPath((Join-Path $root 'output\fabric')); if (-not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { exit 1 }; if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }"
if errorlevel 1 exit /b 1
goto start

:usage
echo Usage: start-fabric-demo.cmd [/clean]
exit /b 2

:clean_cancelled
powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.fabric_reset_cancelled)"
exit /b 2

:no_docker
powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.no_docker)"
exit /b 1

:docker_stopped
powershell -NoProfile -Command "$m = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $m.docker_context_unavailable)"
exit /b 1
