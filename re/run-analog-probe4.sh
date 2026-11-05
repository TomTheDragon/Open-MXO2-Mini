#!/bin/sh
# Analog probe 4: Windows composite register values (WDMA Control2 0x01680101). Run with: sudo sh run-analog-probe4.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/analog-probe4.txt
{
	trap pw_restore EXIT INT TERM
	unload_driver
	timeout 120 python3 -I tools/analog_probe4.py "$@"
	pw_restore
} 2>&1 | tee $OUT
for f in field4-0 field4-1 frame4; do
	L=$(sed -n "s/.*$f.raw (720x\([0-9]*\).*/\1/p" $OUT)
	[ -n "$L" ] && python3 -I tools/raw2png.py notes/analog/$f.raw notes/analog/$f.png 720 "$L" 1 plain
done
chown -R "${SUDO_USER:-root}" $OUT notes/analog 2>/dev/null
echo "note: driver unloaded; sudo modprobe mxo2 to load the installed one again"
