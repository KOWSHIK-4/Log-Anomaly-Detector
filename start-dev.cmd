@echo off
REM =====================================================================
REM  Log Anomaly Detector - start the local development stack
REM
REM  Opens the dashboard at http://localhost:5173/dashboard
REM
REM  Terminal 1: FastAPI backend  -> http://127.0.0.1:8000
REM  Terminal 2: Vite dev server  -> http://localhost:5173
REM =====================================================================

setlocal
set ROOT=%~dp0

echo.
echo  ==========================================================
echo   Log Anomaly Detector - local dev
echo  ==========================================================
echo.

REM --- 1. Python venv -------------------------------------------------
set PY=%ROOT%.venv\Scripts\python.exe
if not exist "%PY%" (
  echo [1/4] Creating virtual environment...
  python -m venv "%ROOT%.venv"
  if errorlevel 1 goto :fail
) else (
  echo [1/4] Virtual environment found.
)

echo [2/4] Installing backend dependencies...
"%PY%" -m pip install -q -r "%ROOT%backend\requirements.txt"
if errorlevel 1 goto :fail

REM --- 2. Frontend dependencies ---------------------------------------
pushd "%ROOT%frontend"
if not exist "node_modules" (
  echo [3/4] Installing frontend dependencies ^(first run, be patient^)...
  call npm install
  if errorlevel 1 (popd & goto :fail)
) else (
  echo [3/4] Frontend dependencies found.
)

REM --- 3. Backend ------------------------------------------------------
echo.
echo [4/4] Starting the FastAPI backend on http://127.0.0.1:8000
start "Anomaly Detector API" cmd /k "cd /d "%ROOT%" && "%PY%" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000"

echo      Waiting for the API to answer...
set /a TRIES=0
:wait
timeout /t 1 /nobreak >nul
set /a TRIES+=1
curl -s -o nul http://127.0.0.1:8000/api/health && goto :frontend
if %TRIES% lss 30 goto :wait

echo      ! API did not start in 30s - check the API window.
goto :frontend

:frontend
REM --- 4. Frontend -----------------------------------------------------
echo      Starting the Vite dev server...
start "Anomaly Detector Dashboard" cmd /k "cd /d "%ROOT%frontend" && npm run dev"

echo.
echo  ==========================================================
echo   Dashboard : http://localhost:5173/dashboard
echo   API       : http://127.0.0.1:8000
echo   API docs  : http://127.0.0.1:8000/docs
echo.
echo   Your browser opens automatically. Both windows can be
echo   closed to stop the project.
echo  ==========================================================
echo.

start "" http://localhost:5173/dashboard
popd
endlocal
exit /b 0

:fail
echo.
echo  ERROR: setup failed. Scroll up for the failing command.
echo.
pause
exit /b 1
