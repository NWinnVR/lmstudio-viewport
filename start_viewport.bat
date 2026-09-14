@echo off
rem ─────────────────────────────────────────────────────────────
rem  LM Viewport — START (double-click)
rem   · if already serving :18022 -> just opens the browser (no duplicate)
rem   · else starts the WATCHDOG (windowless) which launches the dashboard,
rem     keeps it alive, and logs any crash to viewport_crash.log
rem   · waits for the port to actually come up, THEN opens the browser
rem   · to STOP: run stop_viewport.bat
rem ─────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

rem pick a python (watchdog uses sys.executable for the dashboard itself)
set "VPY=pythonw"
where pythonw >nul 2>nul || set "VPY=python"

rem already serving? -> just open the browser, done
netstat -ano | findstr "LISTENING" | findstr ":18022 " >nul
if %errorlevel%==0 (
  start "" "http://127.0.0.1:18022"
  exit /b 0
)

rem not running -> start the watchdog (windowless, keeps it alive)
start "" "%VPY%" viewport_watchdog.py

rem wait for the port (up to ~25s) before opening the browser
set /a n=0
:wait
netstat -ano | findstr "LISTENING" | findstr ":18022 " >nul
if %errorlevel%==0 goto open
set /a n+=1
if %n% geq 25 goto fail
timeout /t 1 /nobreak >nul
goto wait

:open
start "" "http://127.0.0.1:18022"
echo LM Viewport is up on http://127.0.0.1:18022  (watchdog active)
exit /b 0

:fail
echo.
echo  Viewport did NOT come up on :18022 in time.
echo  Check viewport_watchdog.log and viewport_crash.log for the reason.
echo.
pause
exit /b 1
