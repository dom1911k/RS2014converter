@echo off
rem Rebuilds the .psarc from a project file without re-running the analysis,
rem e.g. after you fixed the arrangement XML in EOF.
rem Usage:  build_psarc.bat "C:\Songs\My Song\output\Band - Song\project\Band_Song.rs2dlc"
if "%~1"=="" (
  echo Usage: build_psarc.bat path\to\project.rs2dlc
  exit /b 1
)
if not exist "%~dp0packager\publish\RsPackager.exe" (
  echo The packager is not built yet. Run setup.bat first.
  exit /b 1
)
"%~dp0packager\publish\RsPackager.exe" build "%~1" --out "%~dp1.." --auto-volume
