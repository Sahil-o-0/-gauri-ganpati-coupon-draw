@echo off
rem Restores the last hourly backup. Close the Lucky Draw window first.
cd /d "%~dp0"
.venv\Scripts\python -m luckydraw.restore
pause
