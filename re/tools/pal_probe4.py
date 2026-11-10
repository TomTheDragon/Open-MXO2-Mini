#!/usr/bin/env python3
"""PAL probe 4: field-to-field jitter of packed (progressive) fields per decoder standard setting.

The driver's 288p (PAL SNES) jumps up/down slightly, 576i looks fine. This captures 40 packed fields
(VideoWDmaControl 0x82000000) per decoder setting and measures each field's vertical / horizontal offset
against the first (tools/wobble.py), plus the Status3 values seen:
  auto        0x07 = 0x7f, 0x00 = 0x15 (driver since 2026-11-05)
  forced      0x07 = 0x00, 0x00 = 0x85 / 0x65 (Windows table: PAL / PAL 60)
  forced+mask 0x07 = 0x7f, 0x00 = 0x85 / 0x65
FPGA timing (Control2 bit 4, genlock, clocks) follows the autodetected line system (50 Hz -> Windows PAL values).
Run as root, mxo2 unloaded, source on composite:  sudo python3 -I pal_probe4.py
"""
import collections
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_audio_probe import setup, teardown  # noqa: E402
from analog_probe import DEC  # noqa: E402
from analog_probe5 import grab  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import read  # noqa: E402
from pal_probe2 import ROW, STD  # noqa: E402
from pal_probe3 import NTSC, PAL  # noqa: E402
from wobble import analyse  # noqa: E402

FIELDS = 40
ADDR = 0x100000


def main():
    g, bar, mm, fd = setup(3)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        bus.write(DEC, 0x07, 0x7f)
        bus.write(DEC, 0x00, 0x15)
        time.sleep(1.5)
        s1 = bus.read(DEC, 0x10)[0]
        std = (s1 >> 4) & 7
        fifty = std in (4, 5, 6)
        lines = 288 if fifty else 243
        print('autodetect: %s (S1 %02x) -> %s timing, %d lines per field' % (
            STD[std], s1, '50 Hz' if fifty else '60 Hz', lines), flush=True)
        for r, v in (PAL if fifty else NTSC).items():
            g.wr(r, v)
        forced = 0x85 if fifty else 0x65
        for name, mask, sel in (('auto', 0x7f, 0x15), ('forced', 0x00, forced), ('forced+mask', 0x7f, forced)):
            bus.write(DEC, 0x07, mask)
            bus.write(DEC, 0x00, sel)
            time.sleep(1.5)
            frames, s3s, misses = [], collections.Counter(), 0
            t0 = time.monotonic()
            for _ in range(FIELDS):
                if not grab(g, ADDR, 0x82000000):
                    misses += 1
                    continue
                s3s['%02x' % bus.read(DEC, 0x13)[0]] += 1
                frames.append(read(bar, ADDR, ROW * lines))
            dt = time.monotonic() - t0
            s1 = bus.read(DEC, 0x10)[0]
            print('-- %s (0x07 %02x, 0x00 %02x): AD_RESULT %s S1 %02x, Status3 seen %s, %d fields in %.1f s, '
                  '%d timeouts' % (name, mask, sel, STD[(s1 >> 4) & 7], s1, dict(s3s), len(frames), dt, misses),
                  flush=True)
            if frames:
                print(analyse(frames, 720, lines, name), flush=True)
        bus.write(DEC, 0x07, 0x7f)
        bus.write(DEC, 0x00, 0x15)
        for r, v in NTSC.items():
            g.wr(r, v)
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
