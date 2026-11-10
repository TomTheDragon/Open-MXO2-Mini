#!/bin/sh
# Kernel driver test, receiver set up by the driver itself (HDMI source on).
# Run with: sudo sh run-driver-capture.sh [rx_mode]
cd "$(dirname "$0")" || exit 1
OUT=notes/driver-capture.txt
. ./mxo2-env.sh
D=/sys/kernel/debug/mxo2/$MXO2_DEV
mxo2_need_unlocked "Writing the driver's debugfs files (capture, rx, int_enable)"
frames() { grep frames_onboard $D/status | awk '{print $2}'; }
{
	rmmod mxo2 2>/dev/null
	echo "# insmod (rx_mode=${1:-10}), start"
	mxo2_insmod ../driver/mxo2.ko rx_mode=${1:-10} || exit 1
	echo start > $D/capture || { dmesg | tail -5; rmmod mxo2; exit 1; }
	sleep 4
	echo "## status after 4 s"
	cat $D/status
	echo "## rx"
	cat $D/rx
	dd if=$D/frame of=notes/driver-frame.raw bs=1M 2>&1 | tail -1
	echo "## load AD9398 mode 0 (outputs three-stated) for 2 s: frames should stop"
	a=$(frames); echo 0 > $D/rx; sleep 2; b=$(frames)
	echo "frames in those 2 s: $((b - a))"
	cat $D/rx
	echo "## back to mode ${1:-10} for 3 s: frames should resume"
	echo ${1:-10} > $D/rx; sleep 1; a=$(frames); sleep 2; b=$(frames)
	echo "frames in the last 2 s: $((b - a))"
	cat $D/status
	echo stop > $D/capture
	rmmod mxo2
	echo "# dmesg"
	dmesg | grep mxo2 | tail -8
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT notes/driver-frame.raw 2>/dev/null
