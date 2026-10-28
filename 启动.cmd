@echo off
rem ---------------------------------------------------------------
rem Iterator Pet - source (non-packaged) launcher.
rem Double-click this file. It runs the SAME entry point as the
rem packaged exe: `iterator_pet.launcher`, so the first run still
rem auto-detects Rain World, writes config.install and extracts the
rem sprite cache. Afterwards it starts without the dev panel.
rem
rem Keep this file ASCII-only: cmd.exe parses batch bytes in the
rem console codepage, so Chinese comments get executed as commands.
rem
rem   --setup            force re-detection / re-extraction
rem   RAINWORLD_DIR=...  point at the game folder without detection
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%CD%\src"

where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw.exe -m iterator_pet.launcher %*
) else (
  where python.exe >nul 2>nul
  if %errorlevel%==0 (
    start "" python.exe -m iterator_pet.launcher %*
  ) else (
    echo Python was not found on PATH.
    echo Install Python 3.12+ and run:  pip install -r requirements.txt
    pause
  )
)
endlocal
