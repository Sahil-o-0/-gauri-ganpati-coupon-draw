@echo off
rem Starts the Lucky Draw software. Keep this window open while people use it.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe ( echo Run setup.bat first. & pause & exit /b 1 )
.venv\Scripts\python -m luckydraw
pause
