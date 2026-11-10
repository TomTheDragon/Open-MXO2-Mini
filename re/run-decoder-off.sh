#!/bin/sh
# Power down the analog decoder (as Windows does for HDMI) and re-check colours (HDMI source on).
# Run with: sudo sh run-decoder-off.sh [ad9398_mode] [shadow_log]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
timeout 120 python3 -I tools/decoder_off.py "${1:-10}" ${2:+"$2"} 2>&1 | tee notes/decoder-off.txt
chown -R "${SUDO_USER:-root}" notes/decoder-off.txt notes/csc-frames 2>/dev/null
