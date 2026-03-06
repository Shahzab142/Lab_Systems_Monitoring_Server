@echo off
title Lab Guardian Pro - Live Server
color 0A

echo ========================================================
echo       LAB GUARDIAN PRO - MONITORING SERVER (LIVE)
echo ========================================================
echo.

:: 1. Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    pause
    exit /b
)

:: 2. Install Dependencies (Fast check)
echo [*] Checking dependencies...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b
)
echo [OK] Dependencies ready.

:: 3. Start Server
echo.
echo [*] Starting Server on 0.0.0.0:5050...
echo [*] Live Access URL: http://localhost:5050
echo [*] Lan Access URL: http://<YOUR_PC_IP>:5050
echo.
echo [NOTE] Keep this window OPEN.
echo.

python wsgi.py

pause
