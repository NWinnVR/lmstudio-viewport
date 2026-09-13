@echo off
rem Double-click this to back up your exact viewport (code + telemetry data)
rem to the PRIVATE repo. It only pushes when there's a change.
cd /d "%~dp0"
python backup_to_private.py
echo.
pause
