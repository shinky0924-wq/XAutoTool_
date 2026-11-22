@echo off
setlocal
title Chrome Custom Profile Launcher (chrome_launcher)
set "BASE_DIR=%~dp0"
pushd "%BASE_DIR%" >nul 2>&1 || cd /d "%BASE_DIR%"
set "X_AUTO_DM_BASE_DIR=%BASE_DIR%"
if exist "%BASE_DIR%_internal\ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%BASE_DIR%_internal\ms-playwright"

set "TARGET_PY="
if exist "%BASE_DIR%chrome_launcher.py" set "TARGET_PY=%BASE_DIR%chrome_launcher.py"
if not defined TARGET_PY if exist "%BASE_DIR%_app\chrome_launcher.py" set "TARGET_PY=%BASE_DIR%_app\chrome_launcher.py"

if not defined TARGET_PY goto :TryExeFallback

call :FindPythonCli
if %errorlevel%==0 goto :RunPyCli

:TryExeFallback
if not exist "%BASE_DIR%x_auto_dm_gui.exe" goto :NoLauncher
reg query "HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy" /v VerifiedAndReputablePolicyState 2>nul | find "0x1" >nul
if %errorlevel%==0 goto :BlockedBySac
set "PROF_ARG=%~1"
if not "%PROF_ARG%"=="" goto :LaunchExeProfile
echo ========================================================
echo  Chrome Custom Profile Launcher (EXE Mode)
echo ========================================================
set /p "PROF_ARG=Enter profile / account ID (e.g. wqwq1320): "
if "%PROF_ARG%"=="" goto :NoProfileEntered
:LaunchExeProfile
echo Launching Chrome profile "%PROF_ARG%" via x_auto_dm_gui.exe...
echo Please log in to X in Chrome, then close the Chrome window.
start /wait "" /D "%BASE_DIR%" "%BASE_DIR%x_auto_dm_gui.exe" --cli-profile "%PROF_ARG%"
if errorlevel 1 goto :ExeFailed
echo Done. Profile saved in browser_profiles\%PROF_ARG%
popd >nul 2>&1
pause
exit /b 0

:RunPyCli
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
if "%PY_ARG%"=="" goto :RunPyCliNoArg
"%PY%" %PY_ARG% "%TARGET_PY%" %*
goto :AfterPyCli
:RunPyCliNoArg
"%PY%" "%TARGET_PY%" %*
:AfterPyCli
popd >nul 2>&1
pause
exit /b 0

:BlockedBySac
echo ========================================================
echo  [INFO] Smart App Control blocked EXE, and Python is not installed.
echo  Run tool launcher once to install signed Python 3.12,
echo  or launch from GUI after Python setup.
echo ========================================================
popd >nul 2>&1
pause
exit /b 1

:NoProfileEntered
echo [ERROR] Profile name was not entered.
popd >nul 2>&1
pause
exit /b 1

:ExeFailed
echo ========================================================
echo  [ERROR] Failed to launch via x_auto_dm_gui.exe.
echo  Please check Chrome installation or launch from GUI.
echo ========================================================
popd >nul 2>&1
pause
exit /b 1

:NoLauncher
echo ========================================================
echo  [ERROR] Python or chrome_launcher.py was not found.
echo  On another PC without Python, launch x_auto_dm_gui.exe
echo  and click the [Chrome Profile Launcher] button in GUI.
echo ========================================================
popd >nul 2>&1
pause
exit /b 1

:FindPythonCli
set "PY="
set "PY_ARG="
if exist "%BASE_DIR%.venv\Scripts\python.exe" call :TryPyCli "%BASE_DIR%.venv\Scripts\python.exe" ""
if not defined PY if exist "%BASE_DIR%venv\Scripts\python.exe" call :TryPyCli "%BASE_DIR%venv\Scripts\python.exe" ""
if not defined PY if exist "%LocalAppData%\Programs\Python\Python314\python.exe" call :TryPyCli "%LocalAppData%\Programs\Python\Python314\python.exe" ""
if not defined PY if exist "%LocalAppData%\Programs\Python\Python313\python.exe" call :TryPyCli "%LocalAppData%\Programs\Python\Python313\python.exe" ""
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" call :TryPyCli "%LocalAppData%\Programs\Python\Python312\python.exe" ""
if not defined PY if exist "%LocalAppData%\Programs\Python\Python311\python.exe" call :TryPyCli "%LocalAppData%\Programs\Python\Python311\python.exe" ""
if not defined PY if exist "%LocalAppData%\Programs\Python\Python310\python.exe" call :TryPyCli "%LocalAppData%\Programs\Python\Python310\python.exe" ""
if not defined PY if exist "C:\Python313\python.exe" call :TryPyCli "C:\Python313\python.exe" ""
if not defined PY if exist "C:\Python312\python.exe" call :TryPyCli "C:\Python312\python.exe" ""
if not defined PY if exist "C:\Python311\python.exe" call :TryPyCli "C:\Python311\python.exe" ""
if not defined PY if exist "%ProgramFiles%\Python313\python.exe" call :TryPyCli "%ProgramFiles%\Python313\python.exe" ""
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" call :TryPyCli "%ProgramFiles%\Python312\python.exe" ""
if not defined PY call :TryPyCli "py" "-3.14"
if not defined PY call :TryPyCli "py" "-3.13"
if not defined PY call :TryPyCli "py" "-3.12"
if not defined PY call :TryPyCli "py" "-3.11"
if not defined PY call :TryPyCli "py" "-3"
if not defined PY call :TryPyCli "py" ""
if not defined PY call :TryPyCli "python" ""
if not defined PY exit /b 1
exit /b 0

:TryPyCli
set "_CAND=%~1"
set "_CARG=%~2"
if not "%_CARG%"=="" goto :TryPyCliArg
"%_CAND%" -c "import sys; sys.exit(0)" >nul 2>&1
if %errorlevel%==0 set "PY=%_CAND%"
exit /b 0
:TryPyCliArg
"%_CAND%" %_CARG% -c "import sys; sys.exit(0)" >nul 2>&1
if %errorlevel%==0 goto :TryPyCliSetArg
exit /b 0
:TryPyCliSetArg
set "PY=%_CAND%"
set "PY_ARG=%_CARG%"
exit /b 0
