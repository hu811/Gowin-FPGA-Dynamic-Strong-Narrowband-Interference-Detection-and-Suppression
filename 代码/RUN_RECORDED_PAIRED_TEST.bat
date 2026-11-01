@echo off
setlocal
call "%~dp0python_env.cmd"
if errorlevel 1 exit /b 2
"%FPGA_PY%" -B "%~dp0recorded_experiment\run_paired.py" %*
if errorlevel 1 pause
endlocal
