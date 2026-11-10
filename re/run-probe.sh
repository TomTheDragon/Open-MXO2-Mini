#!/bin/sh
# Read-only hardware probe of the MXO2 card. Run with: sudo sh run-probe.sh
cd "$(dirname "$0")" || exit 1
. ./mxo2-env.sh
python3 -I tools/probe.py > notes/probe.txt 2>&1
lspci -vvv -s "$MXO2_DEV" >> notes/probe.txt 2>&1
chown "${SUDO_USER:-root}" notes/probe.txt
echo "done: $(pwd)/notes/probe.txt"
