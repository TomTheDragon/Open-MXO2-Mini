#!/bin/sh
# PAL 288p field alignment (module param pal_field_fix 1 / 0 / -1 at runtime), 100 consecutive frames each,
# also at 256 wide; then 576i to check it is untouched.
# PAL console in 50 Hz on composite. Run with: sudo sh run-pal-field-test.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/pal-field-test.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV
P=/sys/module/mxo2/parameters

find_vdev() {
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
	# one device for all inputs: the Input control (0 Auto, 1 HDMI, 2 Composite, 3 S-Video, 4 Component) picks Composite
	[ -n "$VDEV" ] && v4l2-ctl -d "$VDEV" -c input=2
}

# $1 = name: 100 frames -> jitter, first frame PNG
stream() {
	W=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *\([0-9]*\)\/.*/\1/p')
	H=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *[0-9]*\/\([0-9]*\).*/\1/p')
	F=notes/pal/field-$1.raw
	timeout 30 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-skip=5 --stream-count=100 --stream-to=$F 2>&1 |
		tr -d '<' | grep fps | tail -1
	python3 -I tools/wobble.py $F "$W" "$H" | grep -v -E 'vertical  :|horizontal:'
	head -c $((W * H * 2)) $F > $F.1
	python3 -I tools/raw2png.py $F.1 notes/pal/field-$1.png "$W" "$H" 1 plain
	rm -f $F.1 $F
}

{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	load_driver input=1
	sleep 3
	find_vdev
	grep -E '^input' $D/rx
	for t in 1 0 -1; do
		echo "### 288p, pal_field_fix $t"
		echo $t > $P/pal_field_fix
		sleep 0.5
		stream "288p-fix$t"
	done
	echo 1 > $P/pal_field_fix
	echo "### 256x288, pal_field_fix 1"
	v4l2-ctl -d "$VDEV" --set-fmt-video=width=256
	stream 256-fix1
	unload_driver
	echo "### 576i (composite_interlaced=1, pal_field_fix not used)"
	load_driver input=1 composite_interlaced=1
	sleep 3
	find_vdev
	stream 576i
	unload_driver
	pw_restore
} 2>&1 | tee $OUT
chown -R "${SUDO_USER:-root}" $OUT notes/pal 2>/dev/null
