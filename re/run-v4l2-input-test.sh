#!/bin/sh
# One analog input through the driver: auto format, 256 wide, forced interlaced, v4l2-compliance.
# Source on that input. Run with: sudo sh run-v4l2-input-test.sh svideo|component|composite [tag]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
case "$1" in
svideo) NAME="S-Video"; IDX=2 ;;
component) NAME="Component"; IDX=3 ;;
composite) NAME="Composite"; IDX=1 ;;
*) echo "usage: sudo sh run-v4l2-input-test.sh svideo|component|composite [tag]"; exit 1 ;;
esac
TAG=$1${2:+-$2}
OUT=notes/v4l2-$TAG-test.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV
mkdir -p notes/inputs

find_vdev() {
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
	# one device for all inputs: the Input control (0 Auto, 1 HDMI, 2 Composite, 3 S-Video, 4 Component) picks the tested input
	[ -n "$VDEV" ] && v4l2-ctl -d "$VDEV" -c input=$(($IDX + 1))
}

# $1 = name [$2 = width]: 100 frames -> fps, jitter, first frame PNG
stream() {
	[ -n "$2" ] && v4l2-ctl -d "$VDEV" --set-fmt-video=width="$2"
	v4l2-ctl -d "$VDEV" --get-fmt-video --get-parm | grep -E 'Width|Field|Colorspace|Frames per'
	v4l2-ctl -d "$VDEV" --get-cropcap | grep -i aspect
	W=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *\([0-9]*\)\/.*/\1/p')
	H=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *[0-9]*\/\([0-9]*\).*/\1/p')
	F=notes/inputs/$TAG-$1.raw
	timeout 30 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-skip=5 --stream-count=100 --stream-to=$F 2>&1 |
		tr -d '<' | grep fps | tail -1
	grep -E '^(input|ts_timeouts|dma_timeouts|no_buffer)' $D/status
	python3 -I tools/wobble.py $F "$W" "$H" | grep -v -E 'vertical  :|horizontal:'
	head -c $((W * H * 2)) $F > $F.1
	python3 -I tools/raw2png.py $F.1 notes/inputs/$TAG-$1.png "$W" "$H" 1 plain
	python3 -I -c "
d = open('$F.1', 'rb').read(); W, H = $W, $H
ys = d[0::2]; cb = d[1::4]; cr = d[3::4]
print('levels: Y min %d max %d mean %.0f | Cb %d..%d | Cr %d..%d' % (min(ys), max(ys), sum(ys) / len(ys),
      min(cb), max(cb), min(cr), max(cr)))"
	rm -f $F $F.1
}

{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	echo "### $NAME: input=$IDX, scan auto"
	load_driver input=$IDX
	sleep 2
	find_vdev
	[ -n "$VDEV" ] || { echo "ERROR: no '$NAME' video device"; ls /sys/class/video4linux/; exit 1; }
	echo "device $VDEV"
	stream native
	grep -E '^input' $D/rx
	stream 256 256
	echo "### v4l2-compliance ($NAME)"
	v4l2-compliance -d "$VDEV" -s 2>&1 | grep -E 'fail|Total'
	unload_driver
	echo "### $NAME: input=$IDX composite_interlaced=1"
	load_driver input=$IDX composite_interlaced=1
	sleep 2
	find_vdev
	stream il
	unload_driver
	dmesg | grep mxo2 | grep -E 'ADV7181|input|registered|failed' | tail -12
	pw_restore
} 2>&1 | tee $OUT
chown -R "${SUDO_USER:-root}" $OUT notes/inputs 2>/dev/null
