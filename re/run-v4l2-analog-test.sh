#!/bin/sh
# Composite input through the driver: 240p (auto) and forced 480i weave, input switching, v4l2-compliance.
# Composite source with colour bars connected. Run with: sudo sh run-v4l2-analog-test.sh [extra module params]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/v4l2-analog-test.txt
D=/sys/kernel/debug/mxo2/0000:02:00.0

find_vdev() {
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -q "MXO2 Mini Composite" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
}

# $1 = name: stream 100 frames, save one frame -> notes/analog-$1.raw/png, bar levels, comb ratio
grab() {
	v4l2-ctl -d "$VDEV" --get-input --get-fmt-video --get-parm | grep -E 'Input|Width|Field|Colorspace|Frames per'
	v4l2-ctl -d "$VDEV" --get-cropcap | grep -i aspect
	W=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *\([0-9]*\)\/.*/\1/p')
	H=$(v4l2-ctl -d "$VDEV" --get-fmt-video | sed -n 's/.*Width\/Height *: *[0-9]*\/\([0-9]*\).*/\1/p')
	timeout 30 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-count=100 2>&1 | tr -d '<' | grep fps | tail -2
	grep -E '^(input|frames|fps|ts_timeouts|dma_timeouts|no_buffer)' $D/status
	timeout 10 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-skip=5 --stream-count=1 \
		--stream-to=notes/analog-$1.raw --verbose 2>&1 | grep -i -E 'field' | head -1
	python3 -I tools/raw2png.py notes/analog-$1.raw notes/analog-$1.png "$W" "$H" 1 plain
	python3 -I -c "
raw = open('notes/analog-$1.raw', 'rb').read(); W, H = $W, $H; R = W * 2
step = 1 if H <= 288 else 2          # woven frame: sample one field's rows
bars = [('wht', 90), ('yel', 182), ('cyn', 275), ('grn', 362), ('mag', 452), ('red', 545), ('blu', 637)]
out = []
for n, cx in bars:
    x = cx & ~1
    rs = range(H // 8, H * 5 // 8, step * 3)
    y = sum(raw[r * R + 2 * x] for r in rs) / len(rs)
    u = sum(raw[r * R + 2 * x + 1] for r in rs) / len(rs)
    v = sum(raw[r * R + 2 * x + 3] for r in rs) / len(rs)
    out.append('%s %.0f %.0f/%.0f' % (n, y, u, v))
print('bars (probe 7: wht 153 yel 139 69/137 cyn 139 146/87 grn 62 blu 17 182/131): ' + '  '.join(out))
rows = [raw[i * R:(i + 1) * R:4] for i in range(H)]
d = lambda a, b: sum(abs(p - q) for p, q in zip(a, b)) / len(a)
d1 = sum(d(rows[i], rows[i + 1]) for i in range(4, H - 4)) / (H - 8)
d2 = sum(d(rows[i], rows[i + 2]) for i in range(4, H - 4)) / (H - 8)
print('comb ratio %.3f (woven fields ok < 1, swapped > 1; progressive ~0.7)' % (d1 / d2))"
}

{
	trap pw_restore EXIT INT TERM
	echo "### A: input=1, scan auto (240p console expected: 720x240 @ 60)"
	load_driver input=1 "$@"
	sleep 2
	find_vdev
	grep -E '^(input|locked)' $D/rx
	grab 240p
	echo "### device switching: stream the HDMI device briefly, then composite again"
	HDEV=
	for n in /sys/class/video4linux/video*; do
		grep -q "MXO2 Mini HDMI" "$n/name" 2>/dev/null && HDEV=/dev/$(basename "$n")
	done
	timeout 10 v4l2-ctl -d "$HDEV" --stream-mmap=4 --stream-count=10 2>&1 | tr -d '<' | grep -c fps
	grep -E '^input' $D/rx
	timeout 10 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-count=30 2>&1 | tr -d '<' | grep fps | tail -1
	grep -E '^input' $D/rx
	echo "### v4l2-compliance (composite)"
	v4l2-compliance -d "$VDEV" -s 2>&1 | grep -E 'fail|Total'
	unload_driver
	echo "### B: input=1 composite_interlaced=1 (480i weave path; with a 240p source both fields match)"
	load_driver input=1 composite_interlaced=1 "$@"
	sleep 2
	find_vdev
	grab 480i
	unload_driver
	dmesg | grep mxo2 | grep -E 'input|ADV7181|composite|failed' | tail -8
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/analog-240p.* notes/analog-480i.* 2>/dev/null
echo "note: test driver unloaded; sudo modprobe mxo2 loads the installed one (sudo sh ../driver/install.sh to update it)"
