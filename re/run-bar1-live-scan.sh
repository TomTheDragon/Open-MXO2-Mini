#!/bin/sh
# Look for live frame data in BAR1 while capturing (HDMI source on, moving picture).
# Run with: sudo sh run-bar1-live-scan.sh [ad9398_mode] [shadow_log]
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
timeout 180 python3 -I tools/bar1_live_scan.py "${1:-10}" ${2:+"$2"} 2>&1 | tee notes/bar1-live-scan.txt
chown -R "${SUDO_USER:-root}" notes/bar1-live-scan.txt notes/bar1-frames 2>/dev/null
