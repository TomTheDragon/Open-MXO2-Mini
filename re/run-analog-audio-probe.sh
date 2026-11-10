#!/bin/sh
# Analog audio probe: composite video + analog audio connected to the MXO2's analog audio inputs
# (ideally a steady test tone, e.g. 1 kHz from a phone; game audio works too).
# Run with: sudo sh run-analog-audio-probe.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/analog-audio-probe.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 180 python3 -I tools/analog_audio_probe.py
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/analog-audio-*.wav 2>/dev/null
