@echo off
setlocal
cd /d "%~dp0"
REM Hands-on AX example runner (Windows).
REM   run_book_runner.bat               first run creates .venv_runner and installs packages
REM   run_book_runner.bat --port 9000   use another port

set "VPY=.venv_runner\Scripts\python.exe"
if exist "%VPY%" goto have_venv
echo [1/3] Finding Python 3.11 or newer
set "PYEXE="
py -3.12 -c "import sys" >nul 2>&1 && set "PYEXE=py -3.12"
if not defined PYEXE py -3.13 -c "import sys" >nul 2>&1 && set "PYEXE=py -3.13"
if not defined PYEXE py -3.11 -c "import sys" >nul 2>&1 && set "PYEXE=py -3.11"
if not defined PYEXE python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYEXE=python"
if not defined PYEXE (
  echo     Python 3.11 or newer not found. Install Python 3.12 from python.org and run this file again.
  goto end
)
echo [2/3] Creating virtual environment .venv_runner with %PYEXE%
%PYEXE% -m venv .venv_runner
if not exist "%VPY%" (
  echo     Could not create .venv_runner
  goto end
)

:have_venv
if exist ".venv_runner\installed.txt" fc /b requirements.txt .venv_runner\installed.txt >nul 2>&1 && goto run
echo [3/3] Installing packages - first run only, takes a few minutes
"%VPY%" -m pip install -q --upgrade pip
"%VPY%" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo     Package install failed. Check the messages above.
  goto end
)
copy /y requirements.txt .venv_runner\installed.txt >nul

:run
echo.
echo Starting the example runner. If no browser opens, go to http://127.0.0.1:8765
echo Press Ctrl+C in this window to stop.
set "PYTHONUTF8=1"
"%VPY%" runner\server.py %*

:end
pause
