#!/bin/sh
# AD9398 output timing sweep with known colours (HDMI source on).
# Run with: sudo sh run-csc-timing.sh [ad9398_mode] [shadow_log]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
timeout 180 python3 -I tools/csc_timing.py "${1:-10}" ${2:+"$2"} 2>&1 | tee notes/csc-timing.txt
chown -R "${SUDO_USER:-root}" notes/csc-timing.txt notes/csc-frames 2>/dev/null
