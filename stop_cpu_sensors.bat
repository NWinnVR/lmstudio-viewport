@echo off
rem ─────────────────────────────────────────────────────────────────────────
rem  LM Viewport — STOP the CPU power sensor (LibreHardwareMonitor)
rem   Closes the LHM window / tray process. The viewport keeps running —
rem   it just falls back to the flat cpu_w estimate until you start LHM
rem   again (start_cpu_sensors.bat).
rem ─────────────────────────────────────────────────────────────────────────
taskkill /IM LibreHardwareMonitor.exe /F 2>nul
if %errorlevel%==0 (
  echo LibreHardwareMonitor stopped.
) else (
  echo LibreHardwareMonitor is not running.
)
endlocal
exit /b 0
