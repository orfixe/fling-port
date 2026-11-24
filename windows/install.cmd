@echo off
rem SPDX-License-Identifier: MIT
rem Copyright (c) 2026 orfixe and the Fling port contributors
rem Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
rem Double-click to install Fling for Windows (no admin rights needed).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
