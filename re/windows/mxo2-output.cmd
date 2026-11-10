@echo off
rem MXO2 outputs: dump the Matrox driver's saved FPGA register values (mxo2-regs.wds) and box chip register
rem values (mxo2-chips.wds: HDMI transmitter AD9889B, analog encoder ADV7341, ...) with the local kernel
rem debugger, once per step, plus a note on what the TV shows. Results: output\*.txt
rem Needs the same one-time setup as mxo2-regs.cmd (Debugging Tools, bcdedit /debug on), see README.txt.
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
bcdedit /enum {current} | findstr /i /r "^debug *Yes" >nul
if errorlevel 1 (
  echo Kernel debugging is not enabled. Run mxo2-regs.cmd once, it sets it up.
  pause
  exit /b 1
)

if not exist output mkdir output
set SYM=srv*C:\symbols*https://msdl.microsoft.com/download/symbols

echo Connect a TV or monitor to the MXO2's HDMI OUT, and if you can a second one (or the same TV's
echo other input) to the analog outputs (composite / component OUT). For every step, look at both
echo and type what they show.
call :step 1-idle "Close all Matrox software. HDMI source connected and playing."
call :step 2-hdmi-preview "Open the Matrox capture software, HDMI input, live picture shown (not recording)."
call :step 3-composite-preview "Same software, switch to the COMPOSITE input with a composite source playing."
call :step 4-playback "If you have it: play a clip from the Matrox software / an editing app to the MXO2 output. Otherwise just press a key and type skip."

echo.
echo All steps done. Copy the folder "%~dp0output" to re\notes\win\ on Linux.
pause
exit /b 0

:step
echo.
echo ===== Step %~1 =====
echo %~2
pause
echo Reading driver memory...
"%KD%" -kl -y "%SYM%" -logo "output\%~1.txt" -c "$$<%~dp0mxo2-diag.wds;$$><%~dp0mxo2-regs.wds;$$><%~dp0mxo2-chips.wds;q" >nul 2>&1
findstr /c:"RESULT:" "output\%~1.txt"
set NOTE=
set /p NOTE=What do the HDMI OUT and the analog OUT show right now?
echo NOTE: %NOTE%>> "output\%~1.txt"
exit /b 0
