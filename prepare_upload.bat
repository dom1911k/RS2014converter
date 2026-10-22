@echo off
rem Drag one or more song folders onto this file: each becomes a small zip (audio as MP3 + tab
rem + song.yaml) in the "upload" folder, small enough to send for testing.
if "%~1"=="" (
  echo Drag one or more song folders onto this file.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\prepare_upload.py" %*
echo.
pause
