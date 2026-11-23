@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
".venv\Scripts\python.exe" "scripts\start_input_lab.py" --port 4221 --no-browser
if errorlevel 1 exit /b 1
if /i not "%~1"=="--no-browser" start "" "http://127.0.0.1:4221/jam.html"
