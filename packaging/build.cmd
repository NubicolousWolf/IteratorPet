@echo off
rem Wrapper only -- keep this file ASCII. The real build lives in build.py
rem (cmd.exe mangles UTF-8 batch files: Chinese comments/exe names get split
rem  and executed as commands).
setlocal
cd /d "%~dp0\.."
python packaging\build.py %*
set RC=%errorlevel%
endlocal & exit /b %RC%
