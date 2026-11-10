#!/bin/sh
# PAL probe 5: 288p jitter vs ADV7181 PAL timing registers 0xE8-0xEA. Console in 50 Hz on composite.
# Run with: sudo sh run-pal-probe5.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/pal-probe5.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 240 python3 -I tools/pal_probe5.py
	pw_restore
} 2>&1 | tee $OUT
chown "${SUDO_USER:-root}" $OUT 2>/dev/null
