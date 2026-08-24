@echo off
setlocal
cd /d "%~dp0"
if not defined MVP_PORT set "MVP_PORT=8010"
set "DEMO_URL=http://127.0.0.1:%MVP_PORT%"

powershell -NoProfile -Command "$messages = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $messages.reset_warning)"
powershell -NoProfile -Command "$confirmation = Read-Host '[DAIBM-SCF] Type exactly RESET DEMO / 请准确输入 RESET DEMO'; if ($confirmation -ceq 'RESET DEMO') { exit 0 }; exit 1"
if errorlevel 1 goto reset_cancelled

set "COMPOSE_FILE="
set "COMPOSE_PROJECT_NAME="
set "DOCKER_HOST="

call docker --context desktop-linux info >nul 2>nul
if errorlevel 1 goto docker_context_unavailable

call docker --context desktop-linux compose -f "%~dp0docker-compose.yml" --project-name daibm-scf-mvp down -v
if errorlevel 1 exit /b 1

call "%~dp0start-demo.cmd"
if errorlevel 1 exit /b 1

"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\defense_preflight.py" --base-url "%DEMO_URL%"
if errorlevel 1 goto preflight_failed

echo [DAIBM-SCF] Defense demo reset and preflight completed.
endlocal
exit /b 0

:reset_cancelled
powershell -NoProfile -Command "$messages = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $messages.reset_cancelled)"
endlocal
exit /b 1

:docker_context_unavailable
powershell -NoProfile -Command "$messages = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $messages.docker_context_unavailable)"
endlocal
exit /b 1

:preflight_failed
powershell -NoProfile -Command "$messages = ConvertFrom-Json (Get-Content -Raw -Encoding utf8 'launcher-messages.json'); Write-Host ('[DAIBM-SCF] ' + $messages.preflight_failed)"
endlocal
exit /b 1
