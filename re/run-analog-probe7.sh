#!/bin/sh
# Analog probe 7: capture with decoder overrides (default 04=cc 17=a2 2c=ce) -> 240p field + frame PNGs.
# Run with: sudo sh run-analog-probe7.sh [REG=VAL ...]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/analog-probe7.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 120 python3 -I tools/analog_probe7.py "$@"
	pw_restore
} 2>&1 | tee $OUT
python3 -I tools/raw2png.py notes/analog/p7-field.raw notes/analog/p7-field.png 720 243 1 plain
python3 -I tools/raw2png.py notes/analog/p7-frame.raw notes/analog/p7-frame.png 720 486 1 plain
chown -R "${SUDO_USER:-root}" $OUT notes/analog 2>/dev/null
