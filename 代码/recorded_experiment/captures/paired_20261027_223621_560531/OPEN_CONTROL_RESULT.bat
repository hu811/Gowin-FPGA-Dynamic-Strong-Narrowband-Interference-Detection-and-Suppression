@echo off
setlocal
call "%~dp0..\..\..\python_env.cmd"
if errorlevel 1 exit /b 2
"%FPGA_PY%" -B "%~dp0..\..\..\comparison_ui\compare_app.py" --release-root "%~dp0..\..\.." --capacity 4 --capture "%~dp0control"
if errorlevel 1 pause
endlocal
