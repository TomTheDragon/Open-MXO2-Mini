#!/bin/sh
# 1080i through the driver (V4L2 + audio). Source on 1080i50/60. Run with: sudo sh run-v4l2-interlace-test.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/v4l2-interlace-test.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	load_driver
	sleep 2                 # monitor: detect 1080i, load the 1080i receiver table
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
	# one device for all inputs: the Input control (0 Auto, 1 HDMI, 2 Composite, 3 S-Video, 4 Component) picks HDMI
	[ -n "$VDEV" ] && v4l2-ctl -d "$VDEV" -c input=1
	CARD=$(awk '/MXO2/ {print $1; exit}' /proc/asound/cards)
	echo "# detection"
	grep -E '^(mode|locked|audio)' $D/rx
	v4l2-ctl -d "$VDEV" --get-fmt-video --get-parm | grep -E 'Width|Field|Frames per'
	echo "# stream 100 frames + record audio meanwhile"
	arecord -D hw:$CARD,0 -f S16_LE -r 48000 -c 2 -d 4 notes/interlace-audio.wav >/dev/null 2>&1 &
	APID=$!
	v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-count=100 2>&1 | tr -d '<' | grep fps | tail -2
	wait $APID
	grep -E '^(input|running|frames|fps|ts_timeouts|dma_timeouts|no_buffer|audio)' $D/status
	echo "# one frame (field flag + comb ratio: woven correctly ~0.7-0.9, wrong order > 1)"
	v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-skip=5 --stream-count=1 --stream-to=notes/interlace-v4l2.raw \
		--verbose 2>&1 | grep -i -E 'field' | head -2
	python3 -I -c "
import sys; sys.path.insert(0, 'tools')
import interlace_probe3 as p
d1, d2 = p.comb(open('notes/interlace-v4l2.raw', 'rb').read())
print('comb ratio %.3f' % (d1 / d2))"
	python3 -I tools/raw2png.py notes/interlace-v4l2.raw notes/interlace-v4l2.png 1920 1080 2 plain
	echo "# audio during streaming"
	python3 -I tools/wav_check.py notes/interlace-audio.wav | grep -E 'ch[01]|tone|jumps|verdict|silence'
	echo "# v4l2-compliance"
	v4l2-compliance -d "$VDEV" -s 2>&1 | grep -E 'fail|Total'
	unload_driver
	dmesg | grep mxo2 | grep -E 'input|mode|AD9398' | tail -6
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/interlace-v4l2.raw notes/interlace-v4l2.png notes/interlace-audio.wav 2>/dev/null
