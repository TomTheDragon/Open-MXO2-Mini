#!/bin/sh
# HDMI capture attempt (HDMI source must be plugged in). Run with:
#   sudo sh run-capture.sh [ad9398_mode] [vincontrol]     (defaults 10 and 0xd02 = HDMI selected)
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
timeout 180 python3 -I tools/capture.py "${1:-10}" "${2:-0xd02}" 2>&1 | tee notes/capture.txt
chown "${SUDO_USER:-root}" notes/capture.txt notes/frame-*.raw 2>/dev/null
