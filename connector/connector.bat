@echo off
title Lucky Draw - Tally connector
cd /d "%~dp0"
echo Keep this window open (you can minimise it). It sends the Tally bills to the lucky-draw website.
echo Tally must be open with the company loaded.
echo.
:loop
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0connector.ps1"
echo.
echo The connector stopped. Restarting in 10 seconds... (close this window to stop)
timeout /t 10 >nul
goto loop
