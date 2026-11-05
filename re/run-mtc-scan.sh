#!/bin/sh
# Read-only BAR1 scan over MemoryTargetControl values. Run with: sudo sh run-mtc-scan.sh
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
timeout 60 python3 -I tools/mtc_scan.py 2>&1 | tee notes/mtc-scan.txt
chown "${SUDO_USER:-root}" notes/mtc-scan.txt
