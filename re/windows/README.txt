MXO2 register capture helper (Windows 7)
========================================

What it does
  Reads the 4 KiB register block of the MXO2 card while the Matrox driver
  runs. It always does 32-bit reads at the right address. It checks the first
  values (BoardStatus = 0501xxxx, GaiaVersion = 0000000F), so you know at once
  that the dump is correct. Then it takes snapshots: idle, preview,
  recording, stopped and closed.

You need
  - RW-Everything (free, rweverything.com). Install it, or unpack the
    portable zip next to this script. The script finds Rw.exe by itself
    (otherwise it asks for the path). Close the RW-Everything window first.
  - The Matrox software and an HDMI source connected to the box.

Steps
  1. Copy this "windows" folder to the Windows machine (USB stick is fine).
  2. Double-click mxo2-capture.cmd and answer "Yes" to the admin prompt.
  3. Follow the prompts. Press Enter for each step. Type "skip" to leave a
     step out. Adding a note (e.g. "1080p30") helps.
  4. When it is done, an Explorer window opens on
       mxo2-dumps\<date-time>\
     Copy that whole folder back to Linux, into re/notes/win/.

If something goes wrong
  The script stops with a red message, and the folder still holds raw\*.txt
  with everything RW-Everything printed. Bring that folder back too, so the
  parser can be fixed.

Options (from a PowerShell prompt)
  .\mxo2-capture.ps1 -RwPath D:\tools\Rw.exe -Bar0 0xFAFFE000 -Chunk 64


MXO2 register values helper (mxo2-regs.cmd, kernel debugger)
=============================================================

What it does
  The capture helper above cannot see write-only registers (they read 0).
  The Matrox driver keeps a copy of every value it writes in its own memory.
  mxo2-regs.cmd reads those copies with Microsoft's kernel debugger (kd.exe,
  read-only) at four steps: idle, preview, recording, stopped.

You need (one-time)
  - Internet on the Windows machine (the debugger downloads symbol files).
  - "Debugging Tools for Windows". Easiest: the "Windows SDK for Windows 8.1"
    installer (sdksetup.exe from Microsoft's SDK archive). In the feature list
    tick ONLY "Debugging Tools for Windows". (WDK 7.1 also works.)
  - Kernel debugging switched on in the boot settings. The script offers to do
    this (bcdedit /debug on); then reboot once.

Steps
  1. Double-click mxo2-regs.cmd and answer "Yes" to the admin prompt.
     First time: let it enable debugging, reboot, double-click it again.
  2. Follow the four steps; press a key for each. After each step it prints a
     "RESULT:" line ("done" is good).
  3. Copy the folder windows\regs to Linux, into re/notes/win/.
  4. Afterwards, run "bcdedit /debug off" in an admin prompt and reboot.

If a step says "Gaia object NOT found" or shows no RESULT line, bring the
regs folder back anyway - the log contains what is needed to fix the script.
mxo2-diag.wds must sit next to mxo2-regs.cmd; it logs the symbol download
and module list first. If step 1 fails, the script stops there.

MXO2 output helper (mxo2-output.cmd, kernel debugger)
=====================================================

What it does
  Same idea as mxo2-regs.cmd, for the video OUTPUTS: at each step it saves the
  FPGA register values and the box chips' register values (HDMI transmitter
  AD9889B, analog encoder ADV7341, decoder, HDMI receiver) as the Matrox driver
  set them, and asks what the TV shows on the HDMI OUT and analog OUT.

You need
  - The one-time setup of mxo2-regs.cmd (Debugging Tools, kernel debugging on).
    Run mxo2-regs.cmd once first if you haven't.
  - A TV/monitor on the MXO2's HDMI OUT; if possible one on composite OUT too.
  - An HDMI source and a composite source.

Steps
  1. Double-click mxo2-output.cmd, answer "Yes" to the admin prompt.
  2. Follow the four steps (idle, HDMI preview, composite preview, playback).
     After each one, type what the outputs show (e.g. "HDMI out: game
     picture 720p, composite out: black").
  3. Copy the folder windows\output to Linux, into re/notes/win/.
  If a step says "board object NOT found", bring the folder back anyway.
