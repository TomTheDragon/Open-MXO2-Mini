#!/bin/sh
# Output probe 2: colours on HDMI OUT (RGB / YCbCr) and the analog OUT (encoder NTSC modes 0-2), NTSC composite picture.
# NTSC composite source playing on the composite input; TV on HDMI OUT and/or composite OUT.
# The probe stops at each step and asks what the TV shows - type the answer and press Enter.
# Run with: sudo sh run-vout-probe2.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/vout-probe2.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout --foreground 600 python3 -I tools/vout_probe2.py
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT 2>/dev/null
