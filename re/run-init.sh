#!/bin/sh
# Write Windows register defaults, then retry SRI. Run with: sudo sh run-init.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
python3 -I tools/init.py 2>&1 | tee notes/init-test.txt
chown "${SUDO_USER:-root}" notes/init-test.txt
