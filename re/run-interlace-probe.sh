#!/bin/sh
# Interlaced input probe. Source: 1080i50 or 1080i60 on HDMI (e.g. laptop: xrandr --output HDMI-1
# --mode 1920x1080i, or a camera set to 1080i). Run with: sudo sh run-interlace-probe.sh [ad9398_mode]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/interlace-probe.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 300 python3 -I tools/interlace_probe.py "${1:-5}"
	pw_restore
} 2>&1 | tee $OUT
chown -R "${SUDO_USER:-root}" $OUT notes/interlace 2>/dev/null
