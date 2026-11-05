#!/bin/sh
# Load the experimental mxo2 module, dump its registers, then unload it.
# Run with: sudo sh run-load-test.sh (from re/)
# Output goes to notes/load-test.txt
cd "$(dirname "$0")" || exit 1
OUT=notes/load-test.txt
DEV=0000:02:00.0
{
	echo "# insmod"
	insmod ../driver/mxo2.ko 2>&1
	sleep 1
	echo "# dmesg"
	dmesg | grep -i -E 'mxo2|02:00.0' | tail -20
	echo "# regs"
	cat /sys/kernel/debug/mxo2/$DEV/regs 2>&1
	echo "# bar1 first 256 bytes"
	dd if=/sys/kernel/debug/mxo2/$DEV/bar1 bs=256 count=1 2>/dev/null | od -A x -t x4
	echo "# lspci"
	lspci -k -s 02:00.0
	echo "# rmmod"
	rmmod mxo2 2>&1
	dmesg | tail -3
} > "$OUT" 2>&1
chown "${SUDO_USER:-root}" "$OUT"
echo "done: $(realpath "$OUT")"
