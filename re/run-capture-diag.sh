#!/bin/sh
# Capture diagnostics (HDMI plugged in). Run with: sudo sh run-capture-diag.sh [ad9398_mode]
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
timeout 90 python3 -I tools/capture_diag.py "${1:-10}" 2>&1 | tee notes/capture-diag.txt
chown "${SUDO_USER:-root}" notes/capture-diag.txt
