@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Chrome Custom Profile Launcher (chrome_launcher)

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
)

if defined PY (
    chcp 65001 >nul
    set PYTHONIOENCODING=utf-8
    set PYTHONUTF8=1
    if exist "chrome_launcher.py" (
        "%PY%" chrome_launcher.py %*
        pause
        exit /b 0
    )
)

echo ========================================================
echo  [ERROR] Python or chrome_launcher.py was not found.
echo  On another PC without Python, launch x_auto_dm_gui.exe
echo  and click the [Chrome Profile Launcher] button in GUI.
echo ========================================================
pause
exit /b 1
