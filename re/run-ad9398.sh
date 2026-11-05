#!/bin/sh
# Configure the AD9398 HDMI receiver like Windows and watch it. Run with: sudo sh run-ad9398.sh [mode]
# mode: 7/8 = 720p, 5/6 = 1080i, 9/10/11 = 1080p, 1/2 = 480, 3/4 = 576 (default 7)
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
python3 -I tools/ad9398.py "${1:-7}" 60 2>&1 | tee notes/ad9398-test.txt
chown "${SUDO_USER:-root}" notes/ad9398-test.txt
