#!/bin/sh
# Audio probe 2: gapless block chaining. Laptop on HDMI playing 997 Hz on the left channel only:
#   speaker-test -D hdmi -t sine -f 997 -c 2 -s 1
# Run with: sudo sh run-audio-probe2.sh [ad9398_mode]
cd "$(dirname "$0")" || exit 1
OUT=notes/audio-probe2.txt
rmmod mxo2 2>/dev/null
timeout 60 python3 -I tools/audio_probe2.py "${1:-10}" ${2:+"$2"} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/audio-raw2-*.bin 2>/dev/null
echo "note: the installed driver was unloaded; sudo modprobe mxo2 to load it again"
