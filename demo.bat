@echo off
rem Try the software without Tally: starts a pretend Tally with 400 demo bills, and the software on its own demo data.
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe ( echo Run setup.bat first. & pause & exit /b 1 )
start "Pretend Tally" .venv\Scripts\python tools\mock_tally.py --demo 400
set LUCKYDRAW_DATA=%~dp0demo-data
set LUCKYDRAW_BACKUP=%~dp0demo-data\backup
set LUCKYDRAW_PORT=8081
echo Open http://localhost:8081 (this is demo data, not your real data).
.venv\Scripts\python -m luckydraw
pause
