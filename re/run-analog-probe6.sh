#!/bin/sh
# Analog probe 6: ADV7181 level / filter sweep on colour bars. Run with: sudo sh run-analog-probe6.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/analog-probe6.txt
{
	trap pw_restore EXIT INT TERM
	unload_driver
	timeout 300 python3 -I tools/analog_probe6.py "$@"
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT 2>/dev/null
echo "note: driver unloaded; sudo modprobe mxo2 to load the installed one again"
