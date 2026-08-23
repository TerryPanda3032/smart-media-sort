@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Sort2 System
cd /d "%~dp0"

set "APP_URL=http://127.0.0.1:1145"
set "PY="

rem ============================================================
rem  Step 1: detect Python (flat flow, no nested if-blocks)
rem ============================================================
echo [env] == Checking Python environment ...

rem Prefer the Windows launcher 'py', then fall back to 'python'
where py >nul 2>nul && set "PY=py"
if defined PY goto :found
where python >nul 2>nul && set "PY=python"
if defined PY goto :found

rem ------------------------------------------------------------
rem  No Python found -> try to auto-install via winget
rem ------------------------------------------------------------
echo [env] Python not found on this machine.
where winget >nul 2>nul
if errorlevel 1 goto :no_installer

echo [env] Installing Python 3.13 via winget ...
winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :install_failed

set "PY=py"
goto :found

:no_installer
echo [env] No Python and no winget are available.
echo        Please install Python 3.10+ manually, then run this script again:
echo        https://www.python.org/downloads/
echo.
echo        Closing window...
timeout /t 5 /nobreak >nul
pause
exit /b 1

:install_failed
echo [env] Python auto-install failed. Please install Python 3.10+ manually and retry.
echo        https://www.python.org/downloads/
echo.
pause
exit /b 1

:found
echo [env] Python found: !PY!
"%PY%" --version

rem ============================================================
rem  Step 2: set Tsinghua pip mirror (non-fatal if denied)
rem ============================================================
"%PY%" -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>nul
"%PY%" -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>nul

rem ============================================================
rem  Step 3: install required dependencies (visible output)
rem ============================================================
echo [env] Installing dependencies ...
"%PY%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto :deps_failed

rem ============================================================
rem  Step 4: start backend if port is not already listening
rem ============================================================
echo [run] == Starting application ...
cd /d "%~dp0backend"

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 1145 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto :open

echo [run] Backend not running, starting it ...
start "Sort2Backend" cmd /k "title Sort2Backend & python main.py --no-browser"

set /a tries=0
:wait
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 1145 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto :open
set /a tries+=1
if !tries! GEQ 30 (
    echo [run] Backend start timed out. Check the backend console log.
    pause
    exit /b 1
)
goto :wait

rem ============================================================
rem  Step 5: open UI as an app window
rem ============================================================
:open
echo [run] Opening Sort2 window ...
start msedge.exe --app=%APP_URL%
echo [done] Sort2 is ready.
endlocal
pause
goto :eof

:deps_failed
echo [env] Dependency install failed. Check the log above, fix it, then retry.
pause
exit /b 1
