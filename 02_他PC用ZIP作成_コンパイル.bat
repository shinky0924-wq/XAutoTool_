@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

pushd "%~dp0" >nul 2>&1
if errorlevel 1 cd /d "%~dp0"
title Build Package for Other PCs

echo ======================================================================
echo   X Auto DM Tool Pro - Build EXE ^& ZIP Package for Other PCs
echo ======================================================================
echo Working directory: "%CD%"
echo.

set "PY="
set "PY_ARG="

if exist ".venv\Scripts\python.exe" call :TryPy ".venv\Scripts\python.exe" ""
if not defined PY if exist "venv\Scripts\python.exe" call :TryPy "venv\Scripts\python.exe" ""

if not defined PY if exist "%LocalAppData%\Programs\Python\Python313\python.exe" call :TryPy "%LocalAppData%\Programs\Python\Python313\python.exe" ""
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" call :TryPy "%LocalAppData%\Programs\Python\Python312\python.exe" ""
if not defined PY if exist "%LocalAppData%\Programs\Python\Python311\python.exe" call :TryPy "%LocalAppData%\Programs\Python\Python311\python.exe" ""
if not defined PY if exist "%LocalAppData%\Programs\Python\Python310\python.exe" call :TryPy "%LocalAppData%\Programs\Python\Python310\python.exe" ""
if not defined PY if exist "C:\Python313\python.exe" call :TryPy "C:\Python313\python.exe" ""
if not defined PY if exist "C:\Python312\python.exe" call :TryPy "C:\Python312\python.exe" ""
if not defined PY if exist "C:\Python311\python.exe" call :TryPy "C:\Python311\python.exe" ""
if not defined PY if exist "%ProgramFiles%\Python313\python.exe" call :TryPy "%ProgramFiles%\Python313\python.exe" ""
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" call :TryPy "%ProgramFiles%\Python312\python.exe" ""

if not defined PY call :TryPy "py" "-3.12"
if not defined PY call :TryPy "py" "-3.11"
if not defined PY call :TryPy "py" "-3"
if not defined PY call :TryPy "py" ""
if not defined PY call :TryPy "python" ""

if defined PY goto :FoundPy

echo [ERROR] A working Python 3 environment with tkinter was not found.
echo Opening Microsoft Store to install Python 3.12...
start ms-windows-store://pdp/?productid=9NCVDN91XZQP
popd >nul 2>&1
pause
exit /b 1

:FoundPy
echo Using Python: %PY% %PY_ARG%
"%PY%" %PY_ARG% -c "import sys; print('Python Version:', sys.version.split()[0], '| Path:', sys.executable)"
echo.

"%PY%" %PY_ARG% build_dist_package.py
set "BUILD_RC=%errorlevel%"
echo.
if "%BUILD_RC%"=="0" goto :BuildDone

echo ======================================================================
echo  [ERROR] Build failed with exit code %BUILD_RC%.
echo  Please check the error messages above ^(antivirus, Excel/folder lock,
echo  or missing pip packages^) and try again.
echo ======================================================================
popd >nul 2>&1
pause
exit /b %BUILD_RC%

:BuildDone
popd >nul 2>&1
pause
exit /b 0

:TryPy
set "_CAND=%~1"
set "_CARG=%~2"
if not "%_CARG%"=="" goto :TryPyArg
"%_CAND%" -c "import sys, tkinter; sys.exit(0)" >nul 2>&1
if %errorlevel%==0 set "PY=%_CAND%"
exit /b 0

:TryPyArg
"%_CAND%" %_CARG% -c "import sys, tkinter; sys.exit(0)" >nul 2>&1
if %errorlevel%==0 (
    set "PY=%_CAND%"
    set "PY_ARG=%_CARG%"
)
exit /b 0
