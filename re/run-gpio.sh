#!/bin/sh
# GPIO experiment. Run with: sudo sh run-gpio.sh  (keep an HDMI source plugged in)
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
python3 -I tools/gpio.py 2>&1 | tee notes/gpio-test.txt
chown "${SUDO_USER:-root}" notes/gpio-test.txt
