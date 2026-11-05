#!/bin/sh
# Which InterruptEnable0 value makes the card raise interrupts? (HDMI source on)
# MSI first; only if MSI never fires, a short INTx test on the shared line (IRQ 16, also USB).
# Run with: sudo sh run-irq-test.sh [ad9398_mode]
cd "$(dirname "$0")" || exit 1
OUT=notes/irq-test.txt
D=/sys/kernel/debug/mxo2/0000:02:00.0
VALUES="00900000 80900000 40900000 20900000 10900000 f0900000 f0dd5030"
try_values() {
	for v in $VALUES; do
		echo $v > $D/int_enable
		sleep 1
		echo "enable $v: $(grep '^irq' $D/status)"
	done
}
total_irqs() { grep '^irq' $D/status | sed 's/.*, \([0-9]*\) handled.*/\1/'; }
{
	rmmod mxo2 2>/dev/null
	echo "# receiver setup"
	timeout 30 python3 -I tools/rx_setup.py "${1:-10}"
	echo "# MSI"
	insmod ../driver/mxo2.ko use_irq=1 || exit 1
	echo start > $D/capture
	lspci -vv -s 02:00.0 | grep -E 'Control:|MSI:|Address:|Data:'
	try_values
	cat $D/status
	MSI_IRQS=$(total_irqs)
	echo stop > $D/capture
	rmmod mxo2
	echo "# INTx (only if MSI saw nothing): MSI handled $MSI_IRQS"
	if [ "$MSI_IRQS" = 0 ]; then
		insmod ../driver/mxo2.ko use_irq=2 int_enable=0x00900000 || exit 1
		echo start > $D/capture
		VALUES="00900000 80900000 f0dd5030"
		try_values
		cat $D/status
		echo stop > $D/capture
		rmmod mxo2
		grep -E '^ *16:' /proc/interrupts
	else
		echo "skipped: MSI works"
	fi
	echo "# dmesg"
	dmesg | grep -i -E 'mxo2|irq 16|nobody cared|disabling' | tail -12
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT
