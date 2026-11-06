@echo off
chcp 65001 >nul
title ブラウザ起動エラー修復ツール (Playwright / Chrome / Edge)
cd /d "%~dp0"

echo ========================================================
echo   【ブラウザ起動エラー (spawn UNKNOWN) 修復ツール】
echo   Playwright Chromiumブラウザの再インストールと
echo   残存プロファイルロックの解除を実行します。
echo ========================================================
echo.

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
    echo [エラー] Pythonが見つかりません。
    pause
    exit /b 1
)

echo [1/3] 残存ブラウザプロファイルのロックを解除中...
for /d /r "browser_profiles" %%d in (*) do (
    if exist "%%d\SingletonLock" del /f /q "%%d\SingletonLock" 2>nul
    if exist "%%d\SingletonCookie" del /f /q "%%d\SingletonCookie" 2>nul
    if exist "%%d\SingletonSocket" del /f /q "%%d\SingletonSocket" 2>nul
    if exist "%%d\lockfile" del /f /q "%%d\lockfile" 2>nul
)

echo [2/3] Playwright Chromium を強制再インストール中 (--force)...
"%PY%" -m playwright install --force chromium

echo [3/3] 完了確認テストを実行中...
echo.
echo ========================================================
echo   修復が完了しました！
echo   再度「01_このPCでツール起動.bat」を実行してください。
echo   （Google Chrome または Edge がインストールされていれば自動で連携されます）
echo ========================================================
pause
