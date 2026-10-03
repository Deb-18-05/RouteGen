@echo off
setlocal

cd /d "%~dp0"
if errorlevel 1 goto :error

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.9 or newer and try again.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the RouteGen virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :error
)

if not exist "data" mkdir data
if errorlevel 1 goto :error
if not exist "output" mkdir output
if errorlevel 1 goto :error

echo Installing RouteGen dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo RouteGen setup completed.
echo Activate the environment with .venv\Scripts\Activate.ps1
exit /b 0

:error
echo RouteGen setup failed.
exit /b 1