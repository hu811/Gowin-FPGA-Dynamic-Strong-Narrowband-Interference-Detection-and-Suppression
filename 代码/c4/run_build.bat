@echo off
setlocal
cd /d "%~dp0"
if not defined GW_SH set "GW_SH=D:\Gowin\Gowin_V1.9.11.03_Education_x64\IDE\bin\gw_sh.exe"
if not exist "%GW_SH%" (
 echo Set GW_SH to the Gowin Education gw_sh.exe full path, then run again.
 pause
 exit /b 2
)
"%GW_SH%" "%~dp0build.tcl"
set "TASK_EXIT=%ERRORLEVEL%"
echo Build exit: %TASK_EXIT%
pause
exit /b %TASK_EXIT%
