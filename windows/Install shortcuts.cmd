@echo off
rem SPDX-License-Identifier: MIT
rem Copyright (c) 2026 orfixe and the Fling port contributors
rem Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
rem Adds Fling to the Start menu and starts it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-shortcuts.ps1" %*
pause
