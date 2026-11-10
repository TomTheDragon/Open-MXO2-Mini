#!/bin/sh
# First PCI DMA test: on-board frame → host RAM (HDMI source on). Run with: sudo sh run-pcidma.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh     # unloads mxo2 even while in use (PipeWire restarted on exit)
unload_driver
old=$(cat /proc/sys/vm/nr_hugepages)
echo $((old + 3)) > /proc/sys/vm/nr_hugepages
echo "huge pages: $(grep HugePages_Free /proc/meminfo)" | tee notes/pcidma.txt
timeout 60 python3 -I tools/pcidma_test.py "${1:-10}" ${2:+"$2"} 2>&1 | tee -a notes/pcidma.txt
echo "$old" > /proc/sys/vm/nr_hugepages
dmesg | tail -5 | sed 's/^/dmesg: /' | tee -a notes/pcidma.txt
chown "${SUDO_USER:-root}" notes/pcidma.txt notes/pcidma-frame.raw 2>/dev/null
