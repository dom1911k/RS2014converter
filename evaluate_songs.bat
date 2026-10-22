@echo off
rem Drag a folder that contains your song folders onto this file.
rem Each song is charted with and without its tab and scored; results (no audio) go to
rem <folder>\evaluation\evaluation_results.zip - send that zip for improving the code.
if "%~1"=="" (
  echo Drag your songs folder onto this file.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\evaluate_library.py" "%~1"
echo.
pause
