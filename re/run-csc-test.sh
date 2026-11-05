#!/bin/sh
# AD9398 colour converter variants, one captured frame each (HDMI camera on).
# Run with: sudo sh run-csc-test.sh [ad9398_mode] [shadow_log]
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
timeout 120 python3 -I tools/csc_test.py "${1:-10}" ${2:+"$2"} 2>&1 | tee notes/csc-test.txt
chown -R "${SUDO_USER:-root}" notes/csc-test.txt notes/csc-frames 2>/dev/null
