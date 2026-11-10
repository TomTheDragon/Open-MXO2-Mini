#!/bin/sh
# PAL composite probe (PAL source on the composite input). Run with: sudo sh run-pal-probe.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/pal-probe.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 120 python3 -I tools/pal_probe.py
	pw_restore
} 2>&1 | tee $OUT
for f in field frame; do
	L=$(sed -n "s/.*$f.raw (720x\([0-9]*\)).*/\1/p" $OUT)
	[ -n "$L" ] && python3 -I tools/raw2png.py notes/pal/$f.raw notes/pal/$f.png 720 "$L" 1 plain
done
chown -R "${SUDO_USER:-root}" $OUT notes/pal 2>/dev/null
