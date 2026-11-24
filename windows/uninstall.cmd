@echo off
rem SPDX-License-Identifier: MIT
rem Copyright (c) 2026 orfixe and the Fling port contributors
rem Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
rem Double-click to remove Fling for Windows.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall %*
echo.
pause
