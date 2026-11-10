#!/bin/sh
# AD9398 fixes for YCbCr 4:2:2 input (HDMI camera on, 1080p50).
# Run with: sudo sh run-rx-fix.sh [ad9398_mode] [shadow_log]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
timeout 180 python3 -I tools/rx_fix_test.py "${1:-10}" ${2:+"$2"} 2>&1 | tee notes/rx-fix.txt
chown -R "${SUDO_USER:-root}" notes/rx-fix.txt notes/csc-frames 2>/dev/null
