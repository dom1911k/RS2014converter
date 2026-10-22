@echo off
rem Runs make_cdlc.py with the Python environment created by setup.bat.
rem Usage:  make_cdlc.bat song.flac --title "Song" --artist "Band" [--tab bass.gp5]
rem    or:  make_cdlc.bat "C:\Songs\My Song"      (a folder with song.yaml)
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Setup has not been run yet. Double-click setup.bat first.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0make_cdlc.py" %*
