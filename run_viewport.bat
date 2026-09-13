@echo off
rem ─────────────────────────────────────────────────────────────
rem  LM Studio Telemetry Viewport — double-click launcher (Windows)
rem
rem  · starts the viewport WINDOWLESS (pythonw, no console left open)
rem  · if it's already running, just opens the browser (no duplicate server)
rem  · to STOP it: click the "close" button inside the viewport
rem  · needs: Python 3.8+ on PATH (and LM Studio installed). psutil is optional.
rem ─────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

rem windowless python (no console window). falls back to python.exe if missing.
set "VPY=pythonw"
where pythonw >nul 2>nul || set "VPY=python"

rem already serving? -> just open the browser, done
netstat -ano | findstr "LISTENING" | findstr ":18022 " >nul
if %errorlevel%==0 (
  start "" "http://127.0.0.1:18022"
  exit /b 0
)

rem not running -> launch it hidden (pythonw = no console window at all)
start "" "%VPY%" dashboard.py

rem give the server a moment to bind the port, then open the browser
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:18022"
exit /b 0
