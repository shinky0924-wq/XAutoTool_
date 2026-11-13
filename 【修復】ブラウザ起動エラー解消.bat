@echo off
title Browser Repair Tool (Playwright / Chrome / Edge)
cd /d "%~dp0"

echo ========================================================
echo   Browser Launch Error Repair Tool
echo   Reinstalling Playwright Chromium and clearing locks...
echo ========================================================
echo.

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
)

if not defined PY (
    echo [ERROR] Python was not found.
    pause
    exit /b 1
)

echo [1/3] Clearing stale browser profile locks...
for /d /r "browser_profiles" %%d in (*) do (
    if exist "%%d\SingletonLock" del /f /q "%%d\SingletonLock" 2>nul
    if exist "%%d\SingletonCookie" del /f /q "%%d\SingletonCookie" 2>nul
    if exist "%%d\SingletonSocket" del /f /q "%%d\SingletonSocket" 2>nul
    if exist "%%d\lockfile" del /f /q "%%d\lockfile" 2>nul
)

echo [2/3] Reinstalling Playwright Chromium (--force)...
"%PY%" -m playwright install --force chromium

echo [3/3] Completed!
echo.
echo ========================================================
echo   Repair finished. Please re-run the tool launcher.
echo ========================================================
pause
