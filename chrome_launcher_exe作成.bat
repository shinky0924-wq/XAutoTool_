@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title chrome_launcher.exe 作成ツール

echo ========================================================
echo   chrome_launcher.exe 単体EXE ビルドツール
echo ========================================================
echo.

if not exist "chrome_launcher.py" (
    echo [ERROR] chrome_launcher.py が見つかりません。
    pause
    exit /b 1
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
    echo [ERROR] Python が見つかりませんでした。
    echo 00_初回環境構築_必要な場合.bat を実行するか Python 3.12 をインストールしてください。
    pause
    exit /b 1
)

echo [1/2] PyInstaller の確認・準備中...
"%PY%" -m pip install pyinstaller --quiet

echo [2/2] chrome_launcher.exe を生成中...
"%PY%" -m PyInstaller --onefile --console --noconfirm --clean --name chrome_launcher --distpath "%~dp0." --workpath "%TEMP%\chrome_launcher_build" --specpath "%TEMP%" chrome_launcher.py

if exist "chrome_launcher.exe" (
    echo.
    echo ========================================================
    echo   [完了] chrome_launcher.exe の作成に成功しました！
    echo   ダブルクリックして起動できます。
    echo ========================================================
) else (
    echo.
    echo [ERROR] chrome_launcher.exe の作成に失敗しました。
)
pause
