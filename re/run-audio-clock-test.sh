#!/bin/sh
# Which clock does the card's audio engine use for a 44.1 kHz source? Laptop: continuous tone at
# R=44100 (python | aplay one-liner in run-audio-rate-test.sh). Run with: sudo sh run-audio-clock-test.sh
# Configs: A = card always 48 kHz (blocks of 48000/fps, ALSA 48000) with AudioPllCtl 2 (Windows value);
#          B/C/D = card follows the source (blocks of 44100/fps, ALSA 44100) with AudioPllCtl 0 / 1 / 3.
# Right answer: tone at 997 Hz, gapless, all blocks delivered.
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/audio-clock-test.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV
run() {  # $1 = label, rest = module parameters
	L=$1; shift
	echo "######## $L: $*"
	load_driver "$@"
	sleep 1
	CARD=$(awk '/MXO2/ {print $1; exit}' /proc/asound/cards)
	grep -E '^audio' $D/rx
	RATE=$(arecord -D hw:$CARD,0 --dump-hw-params -d 1 /dev/null 2>&1 | sed -n 's/^RATE: *//p')
	echo "offered rate: $RATE"
	timeout 10 arecord -D hw:$CARD,0 -f S16_LE -r "$RATE" -c 2 -d 3 notes/audio-clock-$L.wav 2>&1 | grep -v '^Recording'
	grep -E '^audio' $D/status
	python3 -I tools/wav_check.py notes/audio-clock-$L.wav | grep -E 'ch[01]:|tone|jumps|verdict'
}
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	run A audio_clock=48000 audio_pll=2
	run B audio_clock=0 audio_pll=0
	run C audio_clock=0 audio_pll=1
	run D audio_clock=0 audio_pll=3
	unload_driver
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/audio-clock-*.wav 2>/dev/null
