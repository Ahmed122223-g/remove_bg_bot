@echo off
setlocal
cd /d "%~dp0"
title Telegram Background Removal Bot

:: 1. Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ and add it to your system PATH.
    echo.
    pause
    exit /b 1
)

:: 2. Activate virtual environment if present
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

:: 3. Run the Python verification and bot launcher
python run.py

if errorlevel 1 (
    echo.
    echo [ERROR] Bot exited with an error.
    pause
)
