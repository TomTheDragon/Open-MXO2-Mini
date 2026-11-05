#!/bin/sh
# Analog probe 3: GPIO sweep (composite source connected). Run with: sudo sh run-analog-probe3.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/analog-probe3.txt
{
	trap pw_restore EXIT INT TERM
	unload_driver
	timeout 120 python3 -I tools/analog_probe3.py
	pw_restore
} 2>&1 | tee $OUT
L=$(sed -n 's/.*frame3.raw (720x\([0-9]*\)).*/\1/p' $OUT)
[ -n "$L" ] && python3 -I tools/raw2png.py notes/analog/frame3.raw notes/analog/frame3.png 720 "$L" 1 plain
chown -R "${SUDO_USER:-root}" $OUT notes/analog 2>/dev/null
echo "note: driver unloaded; sudo modprobe mxo2 to load the installed one again"
