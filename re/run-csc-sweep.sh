#!/bin/sh
# Bit-map the receiver → memory path with the AD9398 CSC as constant-colour generator (HDMI source on).
# Run with: sudo sh run-csc-sweep.sh [ad9398_mode] [shadow_log]
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
timeout 120 python3 -I tools/csc_sweep.py "${1:-10}" ${2:+"$2"} 2>&1 | tee notes/csc-sweep.txt
chown "${SUDO_USER:-root}" notes/csc-sweep.txt
