@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [DAIBM-SCF] Creating local Python environment...
  python -m venv .venv || exit /b 1
)

echo [DAIBM-SCF] Installing verified dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt || exit /b 1

echo [DAIBM-SCF] Starting the demo server...
start "DAIBM-SCF Server" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:8000"

echo [DAIBM-SCF] The browser is open. Close the server window after the demonstration.
endlocal
