#!/bin/sh
# PAL probe 6: field-parity indicator for 288p. PAL console in 50 Hz on composite.
# Run with: sudo sh run-pal-probe6.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/pal-probe6.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 120 python3 -I tools/pal_probe6.py
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT 2>/dev/null
