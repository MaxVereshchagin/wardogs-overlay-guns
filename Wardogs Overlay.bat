@echo off
rem Only ASCII in this file: cmd misreads non-ASCII lines in .bat files.
rem Wardogs overlay - console version with a menu (search on/off, update database, settings, log, restart, exit).
chcp 65001 >nul
title Wardogs overlay
cd /d "%~dp0app"
"%~dp0python\python.exe" -I "%~dp0app\wardogs_overlay.py" --menu
if errorlevel 1 pause
