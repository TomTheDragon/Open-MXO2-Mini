#!/bin/sh
# Composite 240p at 256x240 / 320x240 / 720x240 (scaled retro widths). 240p console with colour bars connected.
# Run with: sudo sh run-v4l2-retro-test.sh [extra module params, e.g. retro_left=40]
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/v4l2-retro-test.txt
D=/sys/kernel/debug/mxo2/0000:02:00.0
{
	trap pw_restore EXIT INT TERM
	load_driver input=1 "$@"
	sleep 2
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -q "MXO2 Mini Composite" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
	grep -E '^input' $D/rx
	echo "# frame sizes"
	v4l2-ctl -d "$VDEV" --list-framesizes=YUYV
	for W in 256 320 720; do
		echo "### ${W}x240"
		v4l2-ctl -d "$VDEV" --set-fmt-video=width=$W,height=240,pixelformat=YUYV
		v4l2-ctl -d "$VDEV" --get-fmt-video --get-parm | grep -E 'Width|Bytes per|Size Image|Frames per'
		v4l2-ctl -d "$VDEV" --get-cropcap | grep -i aspect
		timeout 30 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-count=120 2>&1 | tr -d '<' | grep fps | tail -1
		grep -E '^(frames_copied|ts_timeouts|dma_timeouts|no_buffer|dma_us_max)' $D/status
		timeout 10 v4l2-ctl -d "$VDEV" --stream-mmap=4 --stream-skip=5 --stream-count=1 \
			--stream-to=notes/retro-$W.raw >/dev/null 2>&1
		ls -l notes/retro-$W.raw | awk '{print "frame bytes", $5}'
		python3 -I tools/raw2png.py notes/retro-$W.raw notes/retro-$W.png $W 240 1 plain
	done
	echo "### v4l2-compliance at 256x240"
	v4l2-ctl -d "$VDEV" --set-fmt-video=width=256,height=240
	v4l2-compliance -d "$VDEV" -s 2>&1 | grep -E 'fail|Total'
	unload_driver
	dmesg | grep mxo2 | grep -E 'composite|failed|BUG|WARN' | tail -5
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/retro-* 2>/dev/null
echo "note: test driver unloaded; sudo modprobe mxo2 loads the installed one (sudo sh ../driver/install.sh to update it)"
