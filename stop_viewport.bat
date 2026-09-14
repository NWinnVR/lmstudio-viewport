@echo off
rem ─────────────────────────────────────────────────────────────
rem  LM Viewport — STOP (double-click)
rem   Kills the WATCHDOG FIRST (so it can't relaunch), then the dashboard.
rem   Finds the dashboard by viewport.pid first, then falls back to
rem   "whoever owns :18022". Also works if the pid files are missing.
rem   For a manual kill, the dashboard pid is in viewport.pid
rem   (e.g.  Get-Process -Id <contents of viewport.pid>).
rem ─────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

rem 1) stop the watchdog (by pid file, then by command-line match)
set "WP="
if exist watchdog.pid set /p WP=<watchdog.pid
if defined WP taskkill /F /T /PID %WP% >nul 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'viewport_watchdog' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }" 2>nul
del /q watchdog.pid 2>nul

rem 2) stop the dashboard: pid file first, then owner of :18022
set "VP="
if exist viewport.pid set /p VP=<viewport.pid
if defined VP (
  taskkill /F /T /PID %VP% >nul 2>nul
  goto done
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":18022 "') do (
  taskkill /F /T /PID %%a >nul 2>nul
)
:done
del /q viewport.pid 2>nul
echo LM Viewport stopped (watchdog + dashboard).
exit /b 0
