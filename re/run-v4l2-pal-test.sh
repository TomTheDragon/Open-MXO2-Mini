#!/bin/sh
# PAL composite through the driver: autodetect (288p expected from a console), forced 576i weave, 256 wide,
# then an NTSC source check is up to the user. PAL source on composite. Run with: sudo sh run-v4l2-pal-test.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/v4l2-pal-test.txt
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

# $1 = name [$2 = width to set]: stream 100 frames, save one -> notes/pal/v4l2-$1.raw/png
grab() {
	[ -n "$2" ] && v4l2-ctl -d "$VDEV" --set-fmt-video=width="$2"
	v4l2-ctl -d "$VDEV" --get-fmt-video --get-parm | grep -E 'Width|Field|Frames per'
	v4l2-ctl -d "$VDEV" --get-cropcap | grep -i aspect
	W=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *\([0-9]*\)\/.*/\1/p')
	H=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *[0-9]*\/\([0-9]*\).*/\1/p')
	timeout 30 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-count=100 2>&1 | tr -d '<' | grep fps | tail -2
	grep -E '^(input|frames|fps|ts_timeouts|dma_timeouts|no_buffer)' $D/status
	timeout 10 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-skip=5 --stream-count=1 \
		--stream-to=notes/pal/v4l2-$1.raw >/dev/null 2>&1
	python3 -I tools/raw2png.py notes/pal/v4l2-$1.raw notes/pal/v4l2-$1.png "$W" "$H" 1 plain
	python3 -I -c "
raw = open('notes/pal/v4l2-$1.raw', 'rb').read(); W, H = $W, $H; R = W * 2
y = [sum(raw[r * R:(r + 1) * R:2]) / W for r in range(H)]
print('mean luma rows 0-3: %s  last 4: %s' % (' '.join('%.0f' % v for v in y[:4]), ' '.join('%.0f' % v for v in y[-4:])))"
}

{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	echo "### A: input=1, scan auto (PAL console expected: 720x576 line-doubled, Field None @ 50)"
	load_driver input=1 "$@"
	sleep 3
	find_vdev
	grep -E '^(input|locked)' $D/rx
	grab 288p
	grab 256 256
	echo "### v4l2-compliance (composite)"
	v4l2-compliance -d "$VDEV" -s 2>&1 | grep -E 'fail|Total'
	unload_driver
	echo "### B: input=1 composite_interlaced=1 (576i weave path)"
	load_driver input=1 composite_interlaced=1 "$@"
	sleep 3
	find_vdev
	grab 576i
	unload_driver
	dmesg | grep mxo2 | grep -E 'input|ADV7181|composite|failed' | tail -8
	pw_restore
} 2>&1 | tee $OUT
chown -R "${SUDO_USER:-root}" $OUT notes/pal 2>/dev/null
