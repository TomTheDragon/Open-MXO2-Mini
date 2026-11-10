#!/bin/sh
# SD input through the driver (480p/576p/480i/576i). Laptop e.g.: xrandr --output HDMI-1 --mode 720x576
# Run with: sudo sh run-v4l2-sd-test.sh [module parameters, e.g. vin_code=0x13]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/v4l2-sd-test.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	load_driver "$@"
	sleep 2                 # monitor: detect the format, load its receiver table
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
	# one device for all inputs: the Input control (0 Auto, 1 HDMI, 2 Composite, 3 S-Video, 4 Component) picks HDMI
	[ -n "$VDEV" ] && v4l2-ctl -d "$VDEV" -c input=1
	CARD=$(awk '/MXO2/ {print $1; exit}' /proc/asound/cards)
	echo "# detection (params: $*)"
	grep -E '^(mode|locked|pixel|audio)' $D/rx
	v4l2-ctl -d "$VDEV" --get-fmt-video --get-parm | grep -E 'Width|Field|Colorspace|Frames per'
	echo "# pixel aspect (576 lines: 4:3 = 54/59, 16:9 = 81/118; 480: 11/10, 33/40; HD 1/1)"
	v4l2-ctl -d "$VDEV" --get-cropcap | grep -i aspect
	W=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *\([0-9]*\)\/.*/\1/p')
	H=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *[0-9]*\/\([0-9]*\).*/\1/p')
	echo "# stream 100 frames + record audio meanwhile"
	arecord -D hw:$CARD,0 -f S16_LE -r 48000 -c 2 -d 3 notes/sd-audio.wav >/dev/null 2>&1 &
	APID=$!
	timeout 30 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-count=100 2>&1 | tr -d '<' | grep fps | tail -2
	wait $APID
	grep -E '^(input|frames|fps|ts_timeouts|dma_timeouts|no_buffer|audio)' $D/status
	echo "# one frame ${W}x${H}"
	timeout 10 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-skip=5 --stream-count=1 --stream-to=notes/sd-frame.raw \
		--verbose 2>&1 | grep -i -E 'field' | head -1
	python3 -I tools/raw2png.py notes/sd-frame.raw notes/sd-frame.png "$W" "$H" 1 plain
	python3 -I -c "
import sys
raw = open('notes/sd-frame.raw', 'rb').read(); W, H = $W, $H; R = W * 2
rows = [raw[i * R:(i + 1) * R:4] for i in range(H)]
d = lambda a, b: sum(abs(x - y) for x, y in zip(a, b)) / len(a)
d1 = sum(d(rows[i], rows[i + 1]) for i in range(4, H - 4)) / (H - 8)
d2 = sum(d(rows[i], rows[i + 2]) for i in range(4, H - 4)) / (H - 8)
print('comb ratio %.3f (interlaced woven correctly < 1, wrong field order > 1; progressive ~0.7)' % (d1 / d2))"
	echo "# audio"
	python3 -I tools/wav_check.py notes/sd-audio.wav | grep -E 'ch[01]|tone|jumps|verdict|silence'
	echo "# v4l2-compliance"
	v4l2-compliance -d "$VDEV" -s 2>&1 | grep -E 'fail|Total'
	unload_driver
	dmesg | grep mxo2 | grep -E 'input|mode|AD9398' | tail -5
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/sd-frame.raw notes/sd-frame.png notes/sd-audio.wav 2>/dev/null
