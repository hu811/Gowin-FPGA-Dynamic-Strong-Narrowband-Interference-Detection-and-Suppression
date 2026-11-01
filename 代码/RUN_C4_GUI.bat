@echo off
setlocal
call "%~dp0python_env.cmd"
if errorlevel 1 exit /b 2
"%FPGA_PY%" -B "%~dp0comparison_ui\compare_app.py" --release-root "%~dp0." --capacity 4 %*
if errorlevel 1 pause
endlocal
