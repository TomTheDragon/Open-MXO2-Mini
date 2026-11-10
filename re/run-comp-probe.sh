#!/bin/sh
# Component probe 1: SD component (480i / 576i) via ADV7181 tables 2 / 5, VinControlAnalog bit 16 on/off.
# Component source on the Y/Pb/Pr inputs. Run with: sudo sh run-comp-probe.sh
cd "$(dirname "$0")" || exit 1
. ./reload-driver.sh
OUT=notes/comp-probe.txt
mkdir -p notes/comp
{
	trap pw_restore EXIT; trap 'exit 130' INT TERM
	unload_driver
	timeout 180 python3 -I tools/comp_probe.py
	pw_restore
} 2>&1 | tee $OUT
sed -n 's/.*saved notes\/comp\/\(c1-[0-9a-z-]*\)\.raw (720x\([0-9]*\)).*/\1 \2/p' $OUT | while read -r f h; do
	python3 -I tools/raw2png.py notes/comp/$f.raw notes/comp/$f.png 720 "$h" 1 plain
done
chown -R "${SUDO_USER:-root}" $OUT notes/comp 2>/dev/null
