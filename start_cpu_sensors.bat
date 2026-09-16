@echo off
rem ─────────────────────────────────────────────────────────────────────────
rem  LM Viewport — CPU POWER SENSOR (double-click)
rem
rem   · self-elevates (UAC prompt) — REQUIRED so LibreHardwareMonitor can read
rem     the CPU power MSRs. Without admin, the "CPU Package" power sensor is
rem     simply absent and the viewport falls back to the flat cpu_w estimate.
rem   · launches LibreHardwareMonitor (detached, lives in the tray afterwards)
rem   · waits for its sensor web server, then tells YOU to click
rem        Options  >  Remote Web Server  >  Run
rem     (one click; the menu starts unchecked so it's always a single click)
rem   · proves it works by printing the live CPU package watts
rem
rem   To STOP the sensor: right-click the LibreHardwareMonitor tray icon > Exit.
rem ─────────────────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

rem ---- self-elevate (only if not already admin) ----
net session >nul 2>&1
if %errorlevel%==0 goto elevated
echo Requesting admin rights (needed to read CPU power sensors)...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b 0

:elevated
rem pick a python
set "PY=python"
where python >nul 2>nul || set "PY=python3"
where %PY% >nul 2>nul || (echo Python not found on PATH & pause & exit /b 1)

%PY% cpu_sensors.py all
set rc=%errorlevel%
echo.
if %rc%==0 (echo Done — LHM is running; close this window whenever you like.)
if %rc% neq 0 (echo Finished with a warning — see the messages above.)
pause
exit /b %rc%
