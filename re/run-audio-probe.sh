#!/bin/sh
# First audio capture probe (HDMI source with sound on; best: laptop playing a 1 kHz tone over HDMI).
# Run with: sudo sh run-audio-probe.sh [ad9398_mode]
cd "$(dirname "$0")" || exit 1
OUT=notes/audio-probe.txt
rmmod mxo2 2>/dev/null
timeout 60 python3 -I tools/audio_probe.py "${1:-10}" ${2:+"$2"} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/audio-raw.bin 2>/dev/null
echo "note: the installed driver was unloaded; sudo modprobe mxo2 to load it again"
