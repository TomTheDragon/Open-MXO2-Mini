#!/bin/sh
# V4L2 device test (HDMI source on). Needs v4l-utils: sudo dnf install v4l-utils, or apt install v4l-utils
# Run with: sudo sh run-v4l2-test.sh
cd "$(dirname "$0")" || exit 1
OUT=notes/v4l2-test.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV
command -v v4l2-ctl >/dev/null || { echo "v4l2-ctl missing: sudo dnf install v4l-utils, or apt install v4l-utils"; exit 1; }
{
	. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
	unload_driver
	modprobe -a videodev videobuf2-v4l2 videobuf2-dma-sg || exit 1
	mxo2_insmod ../driver/mxo2.ko || exit 1
	sleep 1
	DEV=
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini" "$n/name" 2>/dev/null && DEV=/dev/$(basename "$n")
	done
	# one device for all inputs: the Input control (0 Auto, 1 HDMI, 2 Composite, 3 S-Video, 4 Component) picks HDMI
	[ -n "$DEV" ] && v4l2-ctl -d "$DEV" -c input=1
	echo "# device: $DEV"
	[ -n "$DEV" ] || { dmesg | tail -10; unload_driver; exit 1; }
	echo "# v4l2-ctl --all"
	v4l2-ctl -d "$DEV" --all
	echo "# stream 300 frames (no saving)"
	v4l2-ctl -d "$DEV" --stream-mmap=4 --stream-count=300 2>&1 | tr -d '<' | grep -v '^$' | tail -5
	echo "# debugfs status"
	cat $D/status
	echo "# one frame to notes/v4l2-frame.raw (after 10 skipped)"
	v4l2-ctl -d "$DEV" --stream-mmap=4 --stream-skip=10 --stream-count=1 --stream-to=notes/v4l2-frame.raw 2>&1 | tail -2
	echo "# read() interface: one frame"
	dd if="$DEV" of=/dev/null bs=4147200 count=1 2>&1 | tail -1
	echo "# v4l2-compliance (with streaming)"
	if command -v v4l2-compliance >/dev/null; then
		v4l2-compliance -d "$DEV" -s 2>&1 | grep -E 'fail|FAIL|warn|Total|test VIDIOC_(G|S|TRY)_FMT|Streaming' | head -40
	fi
	unload_driver
	echo "# dmesg"
	dmesg | grep -i -E 'mxo2|videobuf|v4l2' | tail -12
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/v4l2-frame.raw 2>/dev/null
