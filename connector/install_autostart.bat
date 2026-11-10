@echo off
rem Starts the connector by itself every time this Windows user logs in.
schtasks /Create /TN "LuckyDrawConnector" /TR "\"%~dp0connector.bat\"" /SC ONLOGON /F
if errorlevel 1 (echo Could not create the task. Try again by right-clicking this file and choosing Run as administrator.) else (echo Done. The connector will start when you log in.)
pause
