@echo off
setlocal
call "%~dp0..\python_env.cmd"
if errorlevel 1 exit /b 2
"%FPGA_PY%" -B "%~dp0run_paired.py" --check
if errorlevel 1 pause
endlocal
