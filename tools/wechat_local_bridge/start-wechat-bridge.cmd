@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" goto usage
if "%~2"=="" goto usage
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if errorlevel 1 (
        python -c "import sys,struct; sys.exit(sys.version_info < (3,11) or struct.calcsize('P') != 8)" || goto python_required
        python -m venv .venv || exit /b 1
    ) else (
        py -3 -c "import sys,struct; sys.exit(sys.version_info < (3,11) or struct.calcsize('P') != 8)" || goto python_required
        py -3 -m venv .venv || exit /b 1
    )
)
if not exist ".venv\bridge-v2-ready" (
    ".venv\Scripts\python.exe" -m pip install -r tools\wechat_local_bridge\requirements.txt || exit /b 1
    type nul > ".venv\bridge-v2-ready"
)
set "setup_mode=--preview"
if "%~3"=="--connect" set "setup_mode="
".venv\Scripts\python.exe" -m tools.wechat_local_bridge.cli setup-scoped --base-url "%~1" --platform-id "%~2" %setup_mode%
exit /b %errorlevel%

:python_required
echo 64-bit Python 3.11 or later is required.
exit /b 1

:usage
echo Copy the launch command from the current channel in the customer service dashboard.
exit /b 2
