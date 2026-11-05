@echo off
rem MXO2: dump the Matrox driver's saved register values (incl. write-only ones) with the
rem local kernel debugger, once per step (idle / preview / recording). Results: regs\*.txt
setlocal enableextensions
net session >nul 2>&1
if errorlevel 1 (
  echo Asking for administrator rights...
  powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
  exit /b
)
cd /d "%~dp0"

set KD=
for %%p in (
  "%ProgramFiles(x86)%\Windows Kits\8.1\Debuggers\x64\kd.exe"
  "%ProgramFiles%\Windows Kits\8.1\Debuggers\x64\kd.exe"
  "%ProgramFiles(x86)%\Windows Kits\10\Debuggers\x64\kd.exe"
  "%ProgramFiles%\Windows Kits\10\Debuggers\x64\kd.exe"
  "%ProgramFiles%\Debugging Tools for Windows (x64)\kd.exe"
) do if exist %%p set "KD=%%~p"
if not defined KD (
  echo kd.exe not found. Install "Debugging Tools for Windows" first, see README.txt.
  pause
  exit /b 1
)
echo Using %KD%

bcdedit /enum {current} | findstr /i /r "^debug *Yes" >nul
if errorlevel 1 (
  echo.
  echo Kernel debugging is not enabled in the boot settings yet.
  choice /m "Enable it now (bcdedit /debug on)? A reboot is needed afterwards"
  if errorlevel 2 exit /b 1
  bcdedit /debug on
  echo.
  echo Done. Reboot Windows, then run mxo2-regs.cmd again.
  pause
  exit /b 0
)

if not exist regs mkdir regs
set SYM=srv*C:\symbols*https://msdl.microsoft.com/download/symbols

call :step 1-idle "Close all Matrox software. HDMI source on and playing."
call :step 2-preview "Open the Matrox capture software with the HDMI live picture shown (not recording)."
call :step 3-recording "Start recording and keep it running until this step is done."
call :step 4-stopped "Stop recording (software may stay open)."

echo.
echo All steps done. Copy the folder "%~dp0regs" to re\notes\win\ on Linux.
echo When you no longer need it: run "bcdedit /debug off" as administrator and reboot.
pause
exit /b 0

:step
echo.
echo ===== Step %~1 =====
echo %~2
pause
echo Reading driver memory (the first run downloads symbols and can take a few minutes)...
"%KD%" -kl -y "%SYM%" -logo "regs\%~1.txt" -c "$$<%~dp0mxo2-diag.wds;$$><%~dp0mxo2-regs.wds;q" >nul 2>&1
findstr /i /c:"RESULT:" /c:"couldn't" /c:"unable to" /c:"error" /c:"MVKAVBUS " "regs\%~1.txt"
findstr /c:"RESULT: done" "regs\%~1.txt" >nul
if not errorlevel 1 exit /b 0
echo.
echo Step %~1 did not work. Copy the folder "%~dp0regs" to re\notes\win\ on Linux.
pause
exit 1
