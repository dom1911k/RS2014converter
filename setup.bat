@echo off
rem One-time setup (and safe to re-run, e.g. after an update). See README.md.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
