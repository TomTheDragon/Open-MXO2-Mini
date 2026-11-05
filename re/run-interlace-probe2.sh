#!/bin/sh
# Interlace probe 2: line pitch (hardware weave) and field parity. 1080i source, ideally a static scene.
# Run with: sudo sh run-interlace-probe2.sh [ad9398_mode]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/interlace-probe2.txt
{
	trap pw_restore EXIT INT TERM
	unload_driver
	timeout 120 python3 -I tools/interlace_probe2.py "${1:-5}"
	pw_restore
} 2>&1 | tee $OUT
chown -R "${SUDO_USER:-root}" $OUT notes/interlace 2>/dev/null
echo "note: driver unloaded; sudo modprobe mxo2 to load the installed one again"
