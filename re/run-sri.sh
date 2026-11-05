#!/bin/sh
# Dump the SRI register space of the MXO2. Run with: sudo sh run-sri.sh
cd "$(dirname "$0")" || exit 1
rmmod mxo2 2>/dev/null
python3 -I tools/sri.py > notes/sri-dump.txt 2>&1
chown "${SUDO_USER:-root}" notes/sri-dump.txt
echo "done: $(pwd)/notes/sri-dump.txt"
