@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  Computer Controller - optional setup
rem  Creates desktop shortcuts and registers an ONLOGON scheduled task
rem  so the service starts automatically when you sign in.
rem ---------------------------------------------------------------

set "TASK_NAME=ComputerController"

echo [1/3] Creating desktop shortcuts...
powershell -NoProfile -Command ^
  "$ws=New-Object -ComObject WScript.Shell;" ^
  "$s=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Computer Controller - Start.lnk');" ^
  "$s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.Save();" ^
  "$s2=$ws.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Computer Controller - Stop.lnk');" ^
  "$s2.TargetPath='%~dp0stop.bat'; $s2.WorkingDirectory='%~dp0'; $s2.Save()"

echo [2/3] Registering auto-start at sign-in (scheduled task "%TASK_NAME%")...
schtasks /Create /F /TN "%TASK_NAME%" /TR "\"%~dp0start.bat\"" /SC ONLOGON >nul 2>nul
if errorlevel 1 (
  echo [WARN] Failed to register the scheduled task (may need administrator rights).
)

echo [3/3] Done. Desktop shortcuts created; auto-start task registered.
pause
