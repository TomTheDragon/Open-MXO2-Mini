#!/bin/sh
# Read-only I2C probe of the box chips. Run with: sudo sh run-i2c.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
python3 -I tools/i2c.py --scan 2>&1 | tee notes/i2c-probe.txt
chown "${SUDO_USER:-root}" notes/i2c-probe.txt
