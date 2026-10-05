@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\studio.ps1" -Action Open
if errorlevel 1 pause
