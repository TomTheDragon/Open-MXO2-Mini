#!/bin/sh
# Watch HDMI/analog receiver status while plugging sources. Run with: sudo sh run-i2c-watch.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
python3 -I tools/i2c_watch.py 90 2>&1 | tee notes/i2c-watch.txt
chown "${SUDO_USER:-root}" notes/i2c-watch.txt
