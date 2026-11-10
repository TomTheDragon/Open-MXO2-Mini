#!/bin/sh
# Replay Windows register values, check if the video WDMA starts (HDMI source on).
# Run with: sudo sh run-wdma-replay.sh [ad9398_mode] [shadow_log]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
timeout 60 python3 -I tools/wdma_replay.py "${1:-10}" ${2:+"$2"} 2>&1 | tee notes/wdma-replay.txt
chown "${SUDO_USER:-root}" notes/wdma-replay.txt
