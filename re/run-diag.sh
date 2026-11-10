#!/bin/sh
# SRI diagnosis. Run with: sudo sh run-diag.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
python3 -I tools/sri_diag.py 2>&1 | tee notes/sri-diag.txt
chown "${SUDO_USER:-root}" notes/sri-diag.txt
