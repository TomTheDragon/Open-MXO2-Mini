#!/bin/sh
# Read-only full register dump of the box chips. Run with: sudo sh run-i2c-dump.sh
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
python3 -I tools/i2c_dump.py 2>&1 | tee notes/i2c-dump.txt
chown "${SUDO_USER:-root}" notes/i2c-dump.txt
