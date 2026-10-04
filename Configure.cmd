@echo off
setlocal
title YYEngine Credential Setup
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\configure.ps1"
set "setup_result=%errorlevel%"
echo.
if not "%setup_result%"=="0" echo Setup stopped with an error. See the message above; you can rerun this launcher.
echo Press any key to close this window.
pause >nul
exit /b %setup_result%
