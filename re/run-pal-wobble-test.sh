#!/bin/sh
# Progressive (240p/288p) jitter: decoder settings at register level, then the driver's V4L2 stream.
# Source (PAL 50 Hz, or the console switched to 60 Hz = PAL 60) on composite.
# Run with: sudo sh run-pal-wobble-test.sh [name]   (name tags the output, e.g. pal50 / pal60)
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
TAG=${1:-pal50}
OUT=notes/pal-wobble-$TAG.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV

find_vdev() {
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
	# one device for all inputs: the Input control (0 Auto, 1 HDMI, 2 Composite, 3 S-Video, 4 Component) picks Composite
	[ -n "$VDEV" ] && v4l2-ctl -d "$VDEV" -c input=2
}

# $1 = name: stream 100 frames into one file, jitter analysis, PNG of the first frame
stream() {
	W=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *\([0-9]*\)\/.*/\1/p')
	H=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *[0-9]*\/\([0-9]*\).*/\1/p')
	echo "# $1: ${W}x$H"
	F=notes/pal/wobble-$TAG-$1.raw
	timeout 30 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-skip=5 --stream-count=100 --stream-to=$F 2>&1 |
		tr -d '<' | grep fps | tail -1
	grep -E '^(ts_timeouts|dma_timeouts|no_buffer)' $D/status
	python3 -I tools/wobble.py $F "$W" "$H"
	head -c $((W * H * 2)) $F > $F.1
	python3 -I tools/raw2png.py $F.1 notes/pal/wobble-$TAG-$1.png "$W" "$H" 1 plain
	rm -f $F.1
}

{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	echo "### register level: packed fields per decoder setting"
	timeout 180 python3 -I tools/pal_probe4.py
	echo "### driver: progressive (auto scan)"
	load_driver input=1
	sleep 3
	find_vdev
	grep -E '^input' $D/rx
	stream prog
	unload_driver
	echo "### driver: interlaced (forced)"
	load_driver input=1 composite_interlaced=1
	sleep 3
	find_vdev
	stream il
	unload_driver
	dmesg | grep mxo2 | grep -E 'ADV7181|composite' | tail -6
	pw_restore
} 2>&1 | tee $OUT
chown -R "${SUDO_USER:-root}" $OUT notes/pal 2>/dev/null
