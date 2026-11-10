#!/bin/sh
# PAL probe 2: decoder standard x FPGA mode code. Run with: sudo sh run-pal-probe2.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/pal-probe2.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 240 python3 -I tools/pal_probe2.py
	pw_restore
} 2>&1 | tee $OUT
for f in $(sed -n 's/.*saved notes\/pal\/\(p2-[0-9a-f-]*\)\.raw.*/\1/p' $OUT); do
	python3 -I tools/raw2png.py notes/pal/$f.raw notes/pal/$f.png 720 340 1 plain
done
chown -R "${SUDO_USER:-root}" $OUT notes/pal 2>/dev/null
