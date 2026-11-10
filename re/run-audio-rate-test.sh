#!/bin/sh
# Audio sample-rate check (driver from driver/). On the laptop play a CONTINUOUS tone at a chosen rate,
# e.g. 44100 (then repeat the test with 48000):
#   R=44100; python3 -c "
#   import math,struct,sys
#   R=$R;o=sys.stdout.buffer;i=0
#   while True:
#       o.write(b''.join(struct.pack('<hh',int(20000*math.sin(2*math.pi*997*(i+k)/R)),int(10000*math.sin(2*math.pi*997*(i+k)/R))) for k in range(4800)));i+=4800
#   " | aplay -D hdmi -f S16_LE -r $R -c 2
# Run with: sudo sh run-audio-rate-test.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/audio-rate-test.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV
{
	trap pw_restore EXIT INT TERM
	load_driver
	sleep 1        # let the 500 ms monitor read the audio status
	CARD=$(awk '/MXO2/ {print $1; exit}' /proc/asound/cards)
	echo "# receiver"
	grep -E '^(locked|audio)' $D/rx
	RATE=$(amixer -c "$CARD" cget iface=PCM,name='HDMI Sample Rate' 2>/dev/null | sed -n 's/^ *: values=//p')
	echo "# ALSA control 'HDMI Sample Rate': $RATE"
	[ -n "$RATE" ] && [ "$RATE" != 0 ] || RATE=48000
	echo "# hw params offered (should be only $RATE)"
	arecord -D hw:$CARD,0 --dump-hw-params -d 1 /dev/null 2>&1 | grep -E 'RATE|FORMAT|CHANNELS'
	echo "# record 4 s at $RATE Hz"
	arecord -D hw:$CARD,0 -f S16_LE -r "$RATE" -c 2 -d 4 notes/audio-rate.wav 2>&1 | tail -1
	echo "# try a wrong rate (the driver must refuse it: arecord warns and falls back to $RATE)"
	if [ "$RATE" = 48000 ]; then W=44100; else W=48000; fi
	arecord -D hw:$CARD,0 -f S16_LE -r $W -c 2 -d 1 /dev/null 2>&1 | grep -i -E 'warning|rate|error'
	grep -E '^audio' $D/status
	echo "# analysis (tone should be ~997 Hz on both channels, L ~20000 / R ~10000 peak)"
	python3 -I tools/wav_check.py notes/audio-rate.wav
	unload_driver
	dmesg | grep -i 'mxo2.*audio' | tail -4
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/audio-rate.wav 2>/dev/null
