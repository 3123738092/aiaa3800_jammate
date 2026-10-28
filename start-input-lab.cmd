@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\start_input_lab.py"
if errorlevel 1 pause
