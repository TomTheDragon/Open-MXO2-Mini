#!/bin/sh
# ALSA audio test with the driver from driver/. Needs a CONTINUOUS stereo tone on HDMI (speaker-test is not
# continuous: it plays ~2.7 s per channel with pauses). On the laptop:
#   python3 -c "
#   import math,struct,sys
#   o=sys.stdout.buffer;i=0
#   while True:
#       o.write(b''.join(struct.pack('<hh',int(20000*math.sin(2*math.pi*997*(i+k)/48000)),int(10000*math.sin(2*math.pi*997*(i+k)/48000))) for k in range(4800)));i+=4800
#   " | aplay -D hdmi -f S16_LE -r 48000 -c 2
# Run with: sudo sh run-audio-test.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/audio-test.txt
D=/sys/kernel/debug/mxo2/0000:02:00.0
{
	trap pw_restore EXIT INT TERM   # this block is a subshell (pipe to tee)
	load_driver
	CARD=$(awk '/MXO2/ {print $1; exit}' /proc/asound/cards)
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -q "MXO2 Mini HDMI" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
	echo "# ALSA card $CARD, video $VDEV"
	cat /proc/asound/cards
	grep -E '^input' $D/status
	echo "# 1: audio only, 5 s"
	arecord -D hw:$CARD,0 -f S16_LE -r 48000 -c 2 -d 5 notes/audio-only.wav 2>&1 | tail -2
	grep -E '^(running|audio)' $D/status
	echo "## last audio block: host copy vs. card memory"
	cat $D/audio
	echo "# 2: audio + video streaming at the same time, 5 s"
	v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-count=230 > /dev/null 2>&1 &
	VPID=$!
	sleep 0.5
	arecord -D hw:$CARD,0 -f S16_LE -r 48000 -c 2 -d 5 notes/audio-video.wav 2>&1 | tail -2
	wait $VPID
	grep -E '^(running|audio|frames_copied|fps)' $D/status
	cat $D/audio
	echo "# analysis"
	python3 -I tools/wav_check.py notes/audio-only.wav notes/audio-video.wav
	unload_driver
	echo "# dmesg"
	dmesg | grep -i -E 'mxo2|snd' | tail -8
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/audio-only.wav notes/audio-video.wav 2>/dev/null
echo "note: the test driver was unloaded; sudo modprobe mxo2 loads the installed (DKMS) one again"
