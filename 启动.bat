@echo off
setlocal enabledelayedexpansion
title Sort2 System
cd /d "%~dp0"

rem ============================================================
rem  Full-session log written next to this script, so even a
rem  flash-exit leaves behind a readable record.
rem ============================================================
set "LOGFILE=%~dp0sort2_launch.log"

rem Testing hook: SORT2_AUTO=1 skips every pause (unattended).
set "WAIT=pause"
if /i "%SORT2_AUTO%"=="1" set "WAIT="

call :begin_log
call :echo [env] == Starting Sort2, log file %LOGFILE% ==

set "PY="
set "PYDIR=%LOCALAPPDATA%\Programs\Python\Python313"
where py >nul 2>nul && set "PY=py"

rem ============================================================
rem  Step 1: ensure a real Python is available
rem ============================================================
if defined PY goto :ready
if exist "%PYDIR%\python.exe" goto :dir_ready

call :echo [env] No Python found, downloading official installer ...

set "PYURL=https://mirrors.huaweicloud.com/python/3.13.14/python-3.13.14-amd64.exe"
set "PYSETUP=%TEMP%\pysetup-3.13.14.exe"

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYSETUP%' -UseBasicParsing; exit 0 } catch { Write-Host $_; exit 1 }"
if errorlevel 1 goto :install_failed

"%PYSETUP%" /quiet InstallAllUsers=0 Include_pip=1 Include_launcher=1 PrependPath=1 TargetDir="%PYDIR%"
if errorlevel 1 goto :install_failed
del "%PYSETUP%" >nul 2>nul

if exist "%PYDIR%\python.exe" goto :dir_ready
where py >nul 2>nul && set "PY=py"
if defined PY goto :ready
goto :install_failed

:dir_ready
set "PATH=%PYDIR%;%PYDIR%\Scripts;%PATH%"
set "PY=python"

:ready
call :echo [env] Python ready: !PY!
"%PY%" --version
"%PY%" -m pip --version

rem ============================================================
rem  Step 2: set Tsinghua pip mirror (non-fatal)
rem ============================================================
"%PY%" -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>nul
"%PY%" -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>nul

rem ============================================================
rem  Step 3: install required dependencies
rem ============================================================
call :echo [env] Installing dependencies ...
"%PY%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 goto :deps_failed

rem ============================================================
rem  Step 4: start backend if port 1145 is not already listening
rem ============================================================
cd /d "%~dp0backend"

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 1145 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto :open

call :echo [run] Starting backend ...
start "Sort2Backend" cmd /k "title Sort2Backend & %PY% main.py --no-browser"

set /a tries=0
:wait
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 1145 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto :open
set /a tries+=1
if !tries! GEQ 30 goto :timeout
goto :wait

rem ============================================================
rem  Step 5: open UI as an app window
rem ============================================================
:open
call :echo [run] Opening UI ...
"%PY%" main.py --open-browser
call :echo [done] Sort2 is ready.
%WAIT%
goto :eof

:timeout
call :echo [run] Backend start timed out (30s). See backend console log.
%WAIT%
exit /b 1

:install_failed
call :echo [env] Python auto-install FAILED. Install Python 3.10+ manually then rerun: https://www.python.org/downloads/
%WAIT%
exit /b 1

:deps_failed
call :echo [env] Dependency install FAILED. Copy the pip error above.
%WAIT%
exit /b 1

rem ============================================================
rem  Subroutine: :echo prints one line to screen AND logfile
rem ============================================================
:begin_log
call :echo  ============================================
call :echo  Sort2 launcher started
call :echo  %date% %time%
goto :eof

:echo
echo(%1
>> "%LOGFILE%" echo %date% %time%  %1
goto :eof