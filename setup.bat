@echo off
rem One-time setup: creates a private Python environment and installs what the software needs.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 ( set PY=py -3 ) else ( set PY=python )
%PY% --version
if errorlevel 1 (
  echo Python is not installed. Install Python 3.10 or newer from python.org and tick "Add Python to PATH", then run this again.
  pause
  exit /b 1
)
%PY% -m venv .venv
if errorlevel 1 ( echo Could not create the environment. & pause & exit /b 1 )
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 ( echo Install failed. Check the internet connection and try again. & pause & exit /b 1 )
echo.
echo Setup finished. Now double-click run.bat
pause
