@echo off
chcp 65001 >nul

set PROJECT_DIR=D:\PycharmProjects\feed-health-monitor

cd /d "%PROJECT_DIR%"

REM ── Пошук інтерпретатора venv ──────────────────────
set VENV_PYTHON=
if exist "%PROJECT_DIR%\.venv\Scripts\python.exe" set VENV_PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe
if exist "%PROJECT_DIR%\venv\Scripts\python.exe"  set VENV_PYTHON=%PROJECT_DIR%\venv\Scripts\python.exe

if "%VENV_PYTHON%"=="" (
    echo [ERROR] venv not found: expected .venv\Scripts\python.exe or venv\Scripts\python.exe
    pause
    exit /b 1
)
REM ───────────────────────────────────────────────────

if not exist "logs" mkdir "logs"

REM ── Дата через PowerShell ──────────────────────────
set STAMP=
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"`) do set STAMP=%%I
if "%STAMP%"=="" set STAMP=nodate
REM ───────────────────────────────────────────────────

set LOGFILE=logs\collect_%STAMP%.log

echo. >> "%LOGFILE%"
echo ===== RUN %DATE% %TIME% ===== >> "%LOGFILE%"
echo Interpreter: %VENV_PYTHON% >> "%LOGFILE%"

"%VENV_PYTHON%" manage.py collect >> "%LOGFILE%" 2>&1

echo ===== EXIT CODE %ERRORLEVEL% ===== >> "%LOGFILE%"