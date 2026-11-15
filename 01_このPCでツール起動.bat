@echo off
setlocal
pushd "%~dp0" >nul 2>&1
if errorlevel 1 cd /d "%~dp0"
title X Auto DM Launcher

REM 0. If standalone EXE exists in current dir and SAC is not enforced
if not exist "%~dp0x_auto_dm_gui.exe" goto :CheckPyw
reg query "HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy" /v VerifiedAndReputablePolicyState 2>nul | find "0x1" >nul
if %errorlevel%==0 goto :CheckPyw
start "" /D "%~dp0" "%~dp0x_auto_dm_gui.exe"
if not errorlevel 1 goto :DoneOk

:CheckPyw
set "PY="
set "PY_ARG="
set "PYW="
set "PYW_ARG="

if exist "%~dp0.venv\Scripts\pythonw.exe" call :TryPyw "%~dp0.venv\Scripts\pythonw.exe" ""
if not defined PYW if exist "%~dp0venv\Scripts\pythonw.exe" call :TryPyw "%~dp0venv\Scripts\pythonw.exe" ""
if not defined PYW if exist "%LocalAppData%\Programs\Python\Python313\pythonw.exe" call :TryPyw "%LocalAppData%\Programs\Python\Python313\pythonw.exe" ""
if not defined PYW if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" call :TryPyw "%LocalAppData%\Programs\Python\Python312\pythonw.exe" ""
if not defined PYW if exist "%LocalAppData%\Programs\Python\Python311\pythonw.exe" call :TryPyw "%LocalAppData%\Programs\Python\Python311\pythonw.exe" ""
if not defined PYW if exist "%LocalAppData%\Programs\Python\Python310\pythonw.exe" call :TryPyw "%LocalAppData%\Programs\Python\Python310\pythonw.exe" ""
if not defined PYW if exist "C:\Python313\pythonw.exe" call :TryPyw "C:\Python313\pythonw.exe" ""
if not defined PYW if exist "C:\Python312\pythonw.exe" call :TryPyw "C:\Python312\pythonw.exe" ""
if not defined PYW if exist "C:\Python311\pythonw.exe" call :TryPyw "C:\Python311\pythonw.exe" ""
if not defined PYW if exist "%ProgramFiles%\Python313\pythonw.exe" call :TryPyw "%ProgramFiles%\Python313\pythonw.exe" ""
if not defined PYW if exist "%ProgramFiles%\Python312\pythonw.exe" call :TryPyw "%ProgramFiles%\Python312\pythonw.exe" ""
if not defined PYW if exist "%LocalAppData%\Microsoft\WindowsApps\pythonw3.13.exe" call :TryPyw "%LocalAppData%\Microsoft\WindowsApps\pythonw3.13.exe" ""
if not defined PYW if exist "%LocalAppData%\Microsoft\WindowsApps\pythonw3.12.exe" call :TryPyw "%LocalAppData%\Microsoft\WindowsApps\pythonw3.12.exe" ""
if not defined PYW if exist "%LocalAppData%\Microsoft\WindowsApps\pythonw3.11.exe" call :TryPyw "%LocalAppData%\Microsoft\WindowsApps\pythonw3.11.exe" ""
if not defined PYW call :TryPyw "pyw" "-3.13"
if not defined PYW call :TryPyw "pyw" "-3.12"
if not defined PYW call :TryPyw "pyw" "-3.11"
if not defined PYW call :TryPyw "pyw" "-3"
if not defined PYW call :TryPyw "pyw" ""
if not defined PYW call :TryPyw "pythonw" ""
if defined PYW goto :LaunchGui

REM Resolve pythonw.exe via working python / py if needed
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" call :TryPyResolve "%LocalAppData%\Programs\Python\Python312\python.exe" ""
if not defined PYW call :TryPyResolve "py" "-3.12"
if not defined PYW call :TryPyResolve "py" "-3"
if not defined PYW call :TryPyResolve "python" ""
if defined PYW goto :LaunchGui

echo ========================================================
echo  [ERROR] Python 3.12 was not found on your system!
echo  Opening Microsoft Store to install Python 3.12...
echo ========================================================
start ms-windows-store://pdp/?productid=9NCVDN91XZQP
popd >nul 2>&1
pause
exit /b 1

:LaunchGui
if exist "%~dp0_internal\ms-playwright" set "PLAYWRIGHT_BROWSERS_PATH=%~dp0_internal\ms-playwright"
set "X_AUTO_DM_BASE_DIR=%~dp0"
if exist "%~dp0run_launcher.pyw" goto :LaunchPywFile
start "" /D "%~dp0" "%PYW%" %PYW_ARG% "%~dp0gui_launcher.py"
goto :DoneOk

:LaunchPywFile
start "" /D "%~dp0" "%PYW%" %PYW_ARG% "%~dp0run_launcher.pyw"

:DoneOk
popd >nul 2>&1
exit /b 0

:TryPyw
set "_CAND=%~1"
set "_CARG=%~2"
if not "%_CARG%"=="" goto :TryPywArg
"%_CAND%" -c "import sys, tkinter; sys.exit(0)" >nul 2>&1
if %errorlevel%==0 set "PYW=%_CAND%"
exit /b 0
:TryPywArg
"%_CAND%" %_CARG% -c "import sys, tkinter; sys.exit(0)" >nul 2>&1
if not %errorlevel%==0 exit /b 0
set "PYW=%_CAND%"
set "PYW_ARG=%_CARG%"
exit /b 0

:TryPyResolve
set "_RCAND=%~1"
set "_RCARG=%~2"
set "_TMPF=%TEMP%\x_auto_dm_pyw_%RANDOM%.txt"
if "%_RCARG%"=="" goto :ResolveNoArg
"%_RCAND%" %_RCARG% -c "import os,re,sys,tkinter; bx=getattr(sys,'_base_executable','') or ''; dirs=[os.path.dirname(sys.executable), os.path.dirname(bx) if bx else '', sys.exec_prefix or '', getattr(sys,'base_exec_prefix',''), os.path.join(os.environ.get('LOCALAPPDATA',''),'Microsoft','WindowsApps')]; names=['pythonw.exe','pythonw3.13.exe','pythonw3.12.exe','pythonw3.11.exe','pythonw3.10.exe']; sw=re.sub(r'python(\d*(?:\.\d+)?)\.exe$',r'pythonw\1.exe',sys.executable,flags=re.I); hits=([sw] if sw.lower()!=sys.executable.lower() and os.path.lexists(sw) else [])+[os.path.join(d,n) for d in dirs if d for n in names if os.path.lexists(os.path.join(d,n))]; print(hits[0] if hits else sys.executable)" > "%_TMPF%" 2>nul
goto :ReadTmpPyw
:ResolveNoArg
"%_RCAND%" -c "import os,re,sys,tkinter; bx=getattr(sys,'_base_executable','') or ''; dirs=[os.path.dirname(sys.executable), os.path.dirname(bx) if bx else '', sys.exec_prefix or '', getattr(sys,'base_exec_prefix',''), os.path.join(os.environ.get('LOCALAPPDATA',''),'Microsoft','WindowsApps')]; names=['pythonw.exe','pythonw3.13.exe','pythonw3.12.exe','pythonw3.11.exe','pythonw3.10.exe']; sw=re.sub(r'python(\d*(?:\.\d+)?)\.exe$',r'pythonw\1.exe',sys.executable,flags=re.I); hits=([sw] if sw.lower()!=sys.executable.lower() and os.path.lexists(sw) else [])+[os.path.join(d,n) for d in dirs if d for n in names if os.path.lexists(os.path.join(d,n))]; print(hits[0] if hits else sys.executable)" > "%_TMPF%" 2>nul
:ReadTmpPyw
if not exist "%_TMPF%" exit /b 0
set "_RES="
set /p _RES=<"%_TMPF%"
del /f /q "%_TMPF%" >nul 2>&1
if "%_RES%"=="" exit /b 0
set "PYW=%_RES%"
set "PYW_ARG="
exit /b 0
