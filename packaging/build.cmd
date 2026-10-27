@echo off
rem 发布打包：PyInstaller onedir -> dist\五块卵石\启动.exe
rem 用绝对路径传 --paths / --add-data / --icon：--specpath 会把相对路径按 spec 目录解析，
rem 之前就因为这点静默跳过了 tray.png（--add-data 找不到文件时不报错、exit 0）。
setlocal
cd /d "%~dp0\.."
set ROOT=%CD%

python -m PyInstaller packaging\run_pet.py ^
  --name 启动 ^
  --noconfirm ^
  --noconsole ^
  --paths "%ROOT%\src" ^
  --add-data "%ROOT%\src\iterator_pet\assets\tray.png;iterator_pet\assets" ^
  --collect-all UnityPy ^
  --icon "%ROOT%\packaging\icon.ico" ^
  --distpath "%ROOT%\dist" ^
  --workpath "%ROOT%\build\pyinstaller" ^
  --specpath "%ROOT%\build"

if errorlevel 1 (
  echo [build] 失败
  exit /b 1
)
echo [build] 产物: %ROOT%\dist\五块卵石\启动.exe
endlocal
