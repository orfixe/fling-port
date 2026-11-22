@echo off
rem Adds Fling to the Start menu and starts it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-shortcuts.ps1" %*
pause
