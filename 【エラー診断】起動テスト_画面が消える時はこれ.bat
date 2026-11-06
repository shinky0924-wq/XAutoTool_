@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title X Auto DM Diagnostics

set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY (
    if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PY=%LocalAppData%\Programs\Python\Python313\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY=%LocalAppData%\Programs\Python\Python311\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
    if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe"
    if exist "C:\Python311\python.exe" set "PY=C:\Python311\python.exe"
)

if not defined PY (
    echo ========================================================
    echo  [ERROR] Python is not installed.
    echo  Opening Microsoft Store...
    echo ========================================================
    start ms-windows-store://pdp/?productid=9NCVDN91XZQP
    pause
    exit /b 1
)

"%PY%" diagnose.py
pause
