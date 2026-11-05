#!/bin/sh
# Watch MXO2 status registers for 2 minutes. Run with: sudo sh run-watch.sh
# While it runs: plug in / unplug an HDMI source, then an analog source.
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
python3 -I tools/watch.py 120 | tee notes/watch.txt
chown "${SUDO_USER:-root}" notes/watch.txt
