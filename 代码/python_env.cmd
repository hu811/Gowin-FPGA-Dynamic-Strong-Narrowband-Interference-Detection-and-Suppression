@echo off
set "FPGA_PY=%~dp0.venv\Scripts\python.exe"
if defined FPGA_PYTHON set "FPGA_PY=%FPGA_PYTHON%"
if not exist "%FPGA_PY%" set "FPGA_PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%FPGA_PY%" (
 echo Run SETUP_PYTHON.bat with Python 3.12 installed, or set FPGA_PYTHON.
 exit /b 2
)
exit /b 0
