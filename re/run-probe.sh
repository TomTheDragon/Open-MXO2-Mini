#!/bin/sh
# Read-only hardware probe of the MXO2 card. Run with: sudo sh run-probe.sh
cd "$(dirname "$0")" || exit 1
python3 -I tools/probe.py > notes/probe.txt 2>&1
lspci -vvv -s 02:00.0 >> notes/probe.txt 2>&1
chown "${SUDO_USER:-root}" notes/probe.txt
echo "done: $(pwd)/notes/probe.txt"
