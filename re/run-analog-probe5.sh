#!/bin/sh
# Analog probe 5: colours (test pattern) + 240p layout. Run with: sudo sh run-analog-probe5.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/analog-probe5.txt
{
	trap pw_restore EXIT INT TERM
	unload_driver
	timeout 300 python3 -I tools/analog_probe5.py "$@"
	pw_restore
} 2>&1 | tee $OUT
for f in colour-a colour-b; do
	L=$(sed -n "s/.*$f.raw (720x\([0-9]*\).*/\1/p" $OUT)
	[ -n "$L" ] && python3 -I tools/raw2png.py notes/analog/$f.raw notes/analog/$f.png 720 "$L" 1 plain
done
chown -R "${SUDO_USER:-root}" $OUT notes/analog 2>/dev/null
echo "note: driver unloaded; sudo modprobe mxo2 to load the installed one again"
