@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title chrome_launcher.exe Builder

echo ========================================================
echo   chrome_launcher.exe Standalone EXE Builder
echo ========================================================
echo.

if not exist "chrome_launcher.py" (
    echo [ERROR] chrome_launcher.py not found in this folder.
    pause
    exit /b 1
)

set "PY="
py --version >nul 2>&1 && set "PY=py"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PY=%LocalAppData%\Programs\Python\Python314\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PY=%LocalAppData%\Programs\Python\Python313\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY=%LocalAppData%\Programs\Python\Python311\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
    if exist "C:\Python313\python.exe" set "PY=C:\Python313\python.exe"
    if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
    if exist "C:\Python311\python.exe" set "PY=C:\Python311\python.exe"
    if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
    if exist "%ProgramFiles%\Python311\python.exe" set "PY=%ProgramFiles%\Python311\python.exe"
)

if not defined PY (
    echo [ERROR] Python was not found on this PC.
    echo Please run the setup batch file or install Python 3.12 first.
    pause
    exit /b 1
)

echo Using Python: %PY%
"%PY%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo [1/2] Installing PyInstaller...
    "%PY%" -m pip install pyinstaller --quiet
) else (
    echo [1/2] PyInstaller is already installed.
)

echo [2/2] Building chrome_launcher.exe...
"%PY%" -m PyInstaller --onefile --console --noconfirm --clean --name chrome_launcher --distpath "." --workpath "%TEMP%\chrome_launcher_build" --specpath "%TEMP%" chrome_launcher.py

echo.
if exist "chrome_launcher.exe" (
    echo ========================================================
    echo   [OK] chrome_launcher.exe has been created!
    echo   You can double-click chrome_launcher.exe to run it.
    echo ========================================================
) else (
    echo [ERROR] Failed to create chrome_launcher.exe.
)
pause
