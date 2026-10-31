@echo off
rem Only ASCII in this file: cmd misreads non-ASCII lines in .bat files.
rem Wardogs overlay - console version with a menu (search on/off, update database, settings, log, restart, exit).
chcp 65001 >nul
title Wardogs overlay
if not exist "%~dp0python\python.exe" goto notextracted
if not exist "%~dp0app\wardogs_overlay.py" goto notextracted
cd /d "%~dp0app"
"%~dp0python\python.exe" -I "%~dp0app\wardogs_overlay.py" --menu
if errorlevel 1 pause
exit /b

:notextracted
echo.
echo   ERROR: folders "app" and "python" were not found next to this file.
echo.
echo   The program does not start from inside the .zip archive.
echo   1. Right click WardogsOverlay.zip - "Extract All..."
echo   2. Open the extracted WardogsOverlay folder
echo   3. Run "Wardogs Overlay.bat" from there
echo.
pause
exit /b 1
