@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Sort2 System
cd /d "%~dp0"

set "APP_URL=http://127.0.0.1:1145"

rem ============================================================
rem  Step 1: ensure Python + mirror + deps
rem ============================================================
set "PY="
where python 2>nul >nul && set "PY=python"
if not defined PY (where py 2>nul >nul && set "PY=py -3")
if not defined PY (
    echo [env] Python not found. Installing Python 3.13 via winget...
    where winget 2>nul >nul
    if errorlevel 1 (
        echo [env] No Python and no winget. Please install Python 3.10+ from:
        echo        https://www.python.org/downloads/
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.13 --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [env] Python auto-install failed, please install manually and retry.
        pause
        exit /b 1
    )
    where python 2>nul >nul && set "PY=python"
    if not defined PY (
        for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
            if exist "%%D\python.exe" set "PY=%%D\python.exe"
        )
    )
)
if not defined PY (
    echo [env] Unable to locate Python. Please install it manually and retry.
    pause
    exit /b 1
)

rem set pip mirror to Tsinghua
if "!PY!"=="python" (
    python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
    python -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn
) else (
    "!PY!" -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
    "!PY!" -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn
)

rem install deps quietly if missing
if "!PY!"=="python" (
    python -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple 2>nul
) else (
    "!PY!" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple 2>nul
)

rem ============================================================
rem  Step 2: start backend if not listening
rem ============================================================
cd /d "%~dp0backend"
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 1145 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto :open

echo Starting backend service...
start "Sort2Backend" cmd /k "title Sort2Backend & python main.py --no-browser"

set /a tries=0
:wait
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 1145 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto :open
set /a tries+=1
if !tries! GEQ 30 (
    echo Backend start timed out. Check the backend console log.
    pause
    exit /b 1
)
goto :wait

:open
rem ============================================================
rem  Step 3: open UI as an app window
rem ============================================================
start msedge.exe --app=%APP_URL%
echo Sort2 window opened.
endlocal