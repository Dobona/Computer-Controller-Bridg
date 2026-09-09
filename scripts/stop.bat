@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

rem ---------------------------------------------------------------
rem  Computer Controller - one-click stop (graceful, watchdog fallback)
rem ---------------------------------------------------------------

if not exist status.json (
  echo [INFO] Service is not running - status.json not found.
  exit /b 0
)

set "PID="
if exist status.json for /f "usebackq tokens=2 delims=:, " %%p in (`findstr /i "pid" status.json`) do set "PID=%%p"
if not defined PID (
  echo [ERROR] Could not read PID from status.json.
  exit /b 1
)

tasklist /FI "PID eq %PID%" 2>nul | findstr /i "%PID%" >nul
if errorlevel 1 (
  echo [INFO] Process %PID% is not running; clearing stale status.json.
  del status.json >nul 2>nul
  del logs\watchdog-state-*.json >nul 2>nul
  del logs\watchdog-state-*.released >nul 2>nul
  exit /b 0
)

echo [1/3] Sending stop request to service (PID %PID%)...
if not exist logs mkdir logs
type nul > logs\stop.request

echo [2/3] Waiting for graceful exit (up to 25 seconds)...
set /a TRIES=0
:waitloop
tasklist /FI "PID eq %PID%" 2>nul | findstr /i "%PID%" >nul
if errorlevel 1 goto stopped
set /a TRIES+=1
if %TRIES% geq 25 goto force
ping -n 2 127.0.0.1 >nul
goto waitloop

:force
echo [WARN] Service did not exit in time; forcing termination (watchdog will release keys if needed).
taskkill /F /PID %PID% >nul 2>nul
ping -n 2 127.0.0.1 >nul

:stopped
if exist logs\stop.request del logs\stop.request >nul 2>nul
if exist status.json del status.json >nul 2>nul
del logs\watchdog-state-*.json >nul 2>nul
del logs\watchdog-state-*.released >nul 2>nul
echo [3/3] Service stopped. Status file cleared.
exit /b 0
