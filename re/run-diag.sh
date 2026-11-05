#!/bin/sh
# SRI diagnosis. Run with: sudo sh run-diag.sh
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
python3 -I tools/sri_diag.py 2>&1 | tee notes/sri-diag.txt
chown "${SUDO_USER:-root}" notes/sri-diag.txt
