#!/bin/sh
# Output probe 1: HDMI OUT + composite OUT with an NTSC composite picture, chips and FPGA set up as Windows does.
# NTSC composite source playing on the composite input; TV on HDMI OUT and/or composite OUT.
# The probe stops at each step and asks what the TV shows - type the answer and press Enter.
# Run with: sudo sh run-vout-probe.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/vout-probe.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout --foreground 600 python3 -I tools/vout_probe.py
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT 2>/dev/null
