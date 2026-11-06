@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title X Auto DM Launcher

REM 1. Detect pythonw / pyw for silent GUI launch (No black CMD window stays open)
set "PYW="
where pyw >nul 2>&1 && set "PYW=pyw"
if not defined PYW where pythonw >nul 2>&1 && set "PYW=pythonw"
if not defined PYW (
    if exist "%LocalAppData%\Programs\Python\Python313\pythonw.exe" set "PYW=%LocalAppData%\Programs\Python\Python313\pythonw.exe"
    if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" set "PYW=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
    if exist "%LocalAppData%\Programs\Python\Python311\pythonw.exe" set "PYW=%LocalAppData%\Programs\Python\Python311\pythonw.exe"
    if exist "%LocalAppData%\Programs\Python\Python310\pythonw.exe" set "PYW=%LocalAppData%\Programs\Python\Python310\pythonw.exe"
    if exist "C:\Python312\pythonw.exe" set "PYW=C:\Python312\pythonw.exe"
    if exist "C:\Python311\pythonw.exe" set "PYW=C:\Python311\pythonw.exe"
)

REM 2. Fallback to python / py if pythonw is not found
if not defined PYW (
    where py >nul 2>&1 && set "PYW=py"
    if not defined PYW where python >nul 2>&1 && set "PYW=python"
    if not defined PYW (
        if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYW=%LocalAppData%\Programs\Python\Python313\python.exe"
        if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYW=%LocalAppData%\Programs\Python\Python312\python.exe"
        if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYW=%LocalAppData%\Programs\Python\Python311\python.exe"
        if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PYW=%LocalAppData%\Programs\Python\Python310\python.exe"
        if exist "C:\Python312\python.exe" set "PYW=C:\Python312\python.exe"
        if exist "C:\Python311\python.exe" set "PYW=C:\Python311\python.exe"
    )
)

if not defined PYW (
    echo ========================================================
    echo  [ERROR] Python 3.12 was not found on your system!
    echo.
    echo  Opening Microsoft Store to install Python 3.12...
    echo  Please click [Get] or [Install] in Microsoft Store.
    echo ========================================================
    start ms-windows-store://pdp/?productid=9NCVDN91XZQP
    pause
    exit /b 1
)

REM 3. Launch GUI app without CMD window and exit immediately
if exist "run_launcher.pyw" (
    start "" "%PYW%" run_launcher.pyw
) else if exist "gui_launcher.pyw" (
    start "" "%PYW%" gui_launcher.pyw
) else if exist "run_launcher.py" (
    start "" "%PYW%" run_launcher.py
) else (
    start "" "%PYW%" gui_launcher.py
)
exit /b 0

