#!/bin/sh
# PAL probe 3: decoder autodetect mask + Windows PAL FPGA values. Run with: sudo sh run-pal-probe3.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/pal-probe3.txt
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 240 python3 -I tools/pal_probe3.py
	pw_restore
} 2>&1 | tee $OUT
sed -n 's/.*saved notes\/pal\/\(p3-[a-z0-9-]*\)\.raw (720x\([0-9]*\)).*/\1 \2/p' $OUT | while read -r f h; do
	python3 -I tools/raw2png.py notes/pal/$f.raw notes/pal/$f.png 720 "$h" 1 plain
done
chown -R "${SUDO_USER:-root}" $OUT notes/pal 2>/dev/null
