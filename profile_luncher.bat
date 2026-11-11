@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Chrome カスタムプロファイル起動ツール (profile_luncher)

if exist "profile_luncher.exe" (
    profile_luncher.exe %*
    pause
    exit /b 0
)
if exist "profile_launcher.exe" (
    profile_launcher.exe %*
    pause
    exit /b 0
)
if exist "x_auto_dm_gui.exe" (
    x_auto_dm_gui.exe --cli-profile %*
    pause
    exit /b 0
)

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
    echo  [ERROR] Python が見つかりませんでした。
    echo ========================================================
    pause
    exit /b 1
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if exist "profile_luncher.py" (
    "%PY%" profile_luncher.py %*
) else if exist "profile_launcher.py" (
    "%PY%" profile_launcher.py %*
)
pause
