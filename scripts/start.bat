@echo off
setlocal EnableExtensions
cd /d "%~dp0.."

rem ---------------------------------------------------------------
rem  Computer Controller - one-click start (normal privileges)
rem  Usage: start.bat            (normal)
rem         start.bat admin      (elevate and start with admin rights)
rem ---------------------------------------------------------------

set "PYEXE=.venv\Scripts\python.exe"
set "STATUS=status.json"
set "MARKER=.venv\.deps_installed"

rem ---- admin mode: relaunch elevated if needed ----
if /i "%~1"=="admin" (
  net session >nul 2>&1
  if errorlevel 1 (
    fsutil dirty query %systemdrive% >nul 2>&1
    if errorlevel 1 (
      echo Requesting administrator privileges...
      powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'admin' -Verb RunAs"
      exit /b 0
    )
  )
  echo [INFO] Running with administrator privileges.
)

rem ---- python check ----
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found. Please install Python 3.11+ and add it to PATH.
  echo         Download: https://www.python.org/downloads/
  pause
  exit /b 1
)

rem ---- create virtual environment on first run ----
if not exist "%PYEXE%" (
  echo [1/4] Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
  )
)

rem ---- install dependencies on first run ----
if not exist "%MARKER%" (
  echo [2/4] Installing dependencies, first run only, may take a few minutes...
  "%PYEXE%" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] Failed to install dependencies. Check your network and retry.
    pause
    exit /b 1
  )
  type nul > "%MARKER%"
)

rem ---- create config.json from the example template on first run ----
if not exist config.json (
  if exist config.example.json (
    echo [INFO] Creating config.json from config.example.json (no API key included).
    copy /Y config.example.json config.json >nul
  )
)

rem ---- already running? ----
set "RUNNING_PID="
if exist "%STATUS%" for /f "usebackq tokens=2 delims=:, " %%p in (`findstr /i "pid" "%STATUS%"`) do set "RUNNING_PID=%%p"
if defined RUNNING_PID (
  tasklist /FI "PID eq %RUNNING_PID%" 2>nul | findstr /i "%RUNNING_PID%" >nul
  if not errorlevel 1 (
    echo [INFO] Service is already running - PID %RUNNING_PID%. See tray icon.
    exit /b 0
  )
  del "%STATUS%" >nul 2>nul
  del logs\watchdog-state-*.json >nul 2>nul
  del logs\watchdog-state-*.released >nul 2>nul
)

rem ---- start service in background ----
if not exist logs mkdir logs
echo [3/4] Starting service in background...
start "Computer Controller" /min .venv\Scripts\python.exe src\main.py

rem ---- wait for status.json (ready signal) ----
set /a TRIES=0
:waitloop
if exist "%STATUS%" goto running
set /a TRIES+=1
if %TRIES% geq 30 goto fail
ping -n 2 127.0.0.1 >nul
goto waitloop

:running
echo [4/4] Service started. Tray icon should be visible; see logs\server.log.
exit /b 0

:fail
echo [ERROR] Service did not report ready within 30 seconds. See logs\server.log.
pause
exit /b 1
