#!/bin/sh
# Analog probe 2: FPGA settings sweep (composite source connected). Run with: sudo sh run-analog-probe2.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/analog-probe2.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 120 python3 -I tools/analog_probe2.py
	pw_restore
} 2>&1 | tee $OUT
L=$(sed -n 's/.*frame2.raw (720x\([0-9]*\)).*/\1/p' $OUT)
[ -n "$L" ] && python3 -I tools/raw2png.py notes/analog/frame2.raw notes/analog/frame2.png 720 "$L" 1 plain
chown -R "${SUDO_USER:-root}" $OUT notes/analog 2>/dev/null
