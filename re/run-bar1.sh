#!/bin/sh
# Time BAR1 reads at each init stage. Run with: sudo sh run-bar1.sh
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
pkill -f tools/capture.py 2>/dev/null
timeout 60 python3 -I tools/bar1_test.py 2>&1 | tee notes/bar1-test.txt
chown "${SUDO_USER:-root}" notes/bar1-test.txt
