@echo off
rem ─────────────────────────────────────────────────────────────
rem  LM Viewport — STARTUP LAUNCHER (runs at Windows logon)
rem
rem  Portable: finds its own folder (%~dp0) and a pythonw from PATH
rem  or the Hermes venv — NO hardcoded username or drive path.
rem
rem  Starts the WATCHDOG (windowless pythonw). The watchdog itself is
rem  idempotent — it checks port :18022 + lock file and exits if another
rem  watcher already owns the port, so this launcher can safely run every
rem  logon without stacking duplicates.
rem
rem  TO TURN OFF: delete this file from:
rem     %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
rem  (and run stop_viewport.bat to stop what's currently running)
rem ─────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

rem pick a windowless python: prefer PATH pythonw, fall back to the Hermes venv
set "VPY="
where pythonw >nul 2>nul && set "VPY=pythonw"
if not defined VPY (
  if exist "%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\pythonw.exe" (
    set "VPY=%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\pythonw.exe"
  )
)
if not defined VPY (
  echo [!] No pythonw found on PATH or in the Hermes venv. Skipping viewport start.
  exit /b 1
)

start "" "%VPY%" viewport_watchdog.py
exit /b 0
