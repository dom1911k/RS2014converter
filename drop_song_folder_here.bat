@echo off
rem Drag a song folder (with song.yaml) onto this file in Explorer to make its CDLC.
if "%~1"=="" (
  echo Drag a song folder onto this file, or run:  make_cdlc.bat "C:\Songs\My Song"
  pause
  exit /b 1
)
call "%~dp0make_cdlc.bat" "%~1"
echo.
pause
