@echo off
setlocal
set "portable=%~dp0tmp\fluidsynth\fluidsynth-v2.6.1-win10-x64-cpp11\bin\fluidsynth.exe"
if not defined JAMMATE_FLUIDSYNTH if exist "%portable%" set "JAMMATE_FLUIDSYNTH=%portable%"
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\start_input_lab.py" --port 4188 %*
if errorlevel 1 pause
