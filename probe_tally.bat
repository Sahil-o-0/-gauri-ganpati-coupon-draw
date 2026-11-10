@echo off
rem Checks the connection to Tally and shows how the bills are read. Run this ON the Tally PC.
rem Example with options:  probe_tally.bat --days 30 --company "My Company"
cd /d "%~dp0"
.venv\Scripts\python tools\probe_tally.py %*
pause
