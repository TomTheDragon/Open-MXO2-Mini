#!/bin/sh
# Interrupt status bits per frame / per DMA (HDMI source on). Run with: sudo sh run-int-probe.sh
cd "$(dirname "$0")" || exit 1
OUT=notes/int-probe.txt
rmmod mxo2 2>/dev/null
old=$(cat /proc/sys/vm/nr_hugepages)
echo $((old + 3)) > /proc/sys/vm/nr_hugepages
{
	echo "# lspci"
	lspci -vv -s 02:00.0 | grep -i -E 'interrupt|msi|irq|LnkSta|LnkCap|^0'
	echo "# probe"
	timeout 60 python3 -I tools/int_probe.py "${1:-10}" ${2:+"$2"}
	echo "# /proc/interrupts line 11"
	grep -E '^ *11:' /proc/interrupts
} 2>&1 | tee $OUT
echo "$old" > /proc/sys/vm/nr_hugepages
chown "${SUDO_USER:-root}" $OUT
