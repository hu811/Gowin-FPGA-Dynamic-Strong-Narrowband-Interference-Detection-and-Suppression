@echo off
setlocal
cd /d "%~dp0"
if defined PYTHON312 (
 "%PYTHON312%" -m venv "%~dp0.venv"
) else (
 py -3.12 -m venv "%~dp0.venv"
)
if errorlevel 1 goto fail
"%~dp0.venv\Scripts\python.exe" -c "import sys; assert sys.version_info[:2]==(3,12)"
if errorlevel 1 goto fail
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements-lock.txt"
if errorlevel 1 goto fail
echo Python environment ready. No serial connection or firmware operation was performed.
pause
exit /b 0
:fail
echo Setup failed. Install Python 3.12 x64, or set PYTHON312 to its python.exe path.
pause
exit /b 2
