@echo off
chcp 65001 >nul
title CAPS Watchdog Setup

REM ==========================================================
REM  CAPS Watchdog installer
REM  Place next to caps_sync.py and run as Administrator
REM ==========================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo [ERROR] Administrator privileges required.
    echo         Right-click this file - "Run as administrator"
    echo.
    pause
    exit /b 1
)

echo ==========================================================
echo   CAPS Watchdog Setup
echo ==========================================================
echo.

cd /d "%~dp0"

where python >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] python not found in PATH.
    pause
    exit /b 1
)

if not exist "caps_sync.py" (
    echo [ERROR] caps_sync.py not found in current folder.
    echo         Current folder: %cd%
    pause
    exit /b 1
)

echo [1/2] Registering scheduled task...
python caps_sync.py --setup-watchdog
if %errorLevel% neq 0 (
    echo.
    echo [ERROR] Registration failed
    pause
    exit /b 1
)

echo.
echo [2/2] Running watchdog once to verify...
python caps_sync.py --watchdog
echo.

echo ==========================================================
echo   DONE - Watchdog installed
echo ==========================================================
echo.
echo  - Runs every 2 minutes
echo  - Work hours (Mon-Fri 06-22): restart if stale over 5 min
echo  - Log file: logs\caps_sync.log
echo  - DB table: sync_log (WATCHDOG level)
echo.
echo  Check:  schtasks /Query /TN CAPS_Watchdog /V
echo  Remove: schtasks /Delete /TN CAPS_Watchdog /F
echo.
pause
