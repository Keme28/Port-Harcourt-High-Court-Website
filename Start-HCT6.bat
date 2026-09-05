@echo off
setlocal
title High Court 6 File Portal

:: Ensure script runs relative to this folder
cd /d "%~dp0"

:: Launch the self-healing PowerShell startup script with bypass policy
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-HCT6.ps1"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ========================================================
    echo  Startup encountered an issue. See details above.
    echo ========================================================
    pause
    exit /b %ERRORLEVEL%
)

exit /b 0
