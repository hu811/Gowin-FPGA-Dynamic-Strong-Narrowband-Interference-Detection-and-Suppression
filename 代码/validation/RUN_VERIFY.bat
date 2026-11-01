@echo off
call "%~dp0..\python_env.cmd"
if errorlevel 1 exit /b 2
"%FPGA_PY%" -B "%~dp0verify_report.py"
set "FPGA_VERIFY_RC=%ERRORLEVEL%"
pause
exit /b %FPGA_VERIFY_RC%
