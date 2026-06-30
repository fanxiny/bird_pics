@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 1) Prefer the preconfigured .venv if present
if exist ".venv\Scripts\python.exe" (
    echo [*] Using .venv
    echo [*] Local: http://127.0.0.1:5000
    echo [*] Phone: http://192.168.110.35:5000  (your LAN IP, same WiFi)
    echo.
    ".venv\Scripts\python.exe" app.py
    goto :end
)

REM 2) Otherwise fall back to system Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo Please install Python 3.8+ from https://www.python.org/
    echo Remember to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [*] Installing dependencies...
python -m pip install -r requirements.txt
echo [*] Local: http://127.0.0.1:5000
echo.
python app.py

:end
pause
