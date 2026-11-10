#!/usr/bin/env python3
"""PAL probe 5: 288p up/down jitter vs the ADV7181 PAL V-bit / field-toggle timing registers.

Probe 4 (notes/pal-wobble-pal50.txt): packed PAL fields from a progressive console alternate by one line,
with every decoder standard setting; PAL 60 / NTSC 240p don't. Our PAL timing registers (Windows table):
0xE8 PVBEG = 0x65, 0xE9 PVEND = 0x14, 0xEA PFTOG = 0x63; NTSC NVBEG 0xE5 = 0x25 has no delay bits.
Assumed layout (ADV718x family): bit 7 DELO (delay by one line on odd field), bit 6 DELE (even field),
bit 5 SIGN, bits 4:0 line count -> PVBEG 0x65 = DELE set. Sweep and measure 40 packed fields each.
Run as root, mxo2 unloaded, PAL 50 Hz progressive source on composite:  sudo python3 -I pal_probe5.py
"""
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
LINES = 300                     # a few beyond 288 to see where the picture lands
CONFIGS = [  # (name, {reg: value})
    ('table e8=65', {0xe8: 0x65, 0xe9: 0x14, 0xea: 0x63}),
    ('e8=25 (no DELE)', {0xe8: 0x25, 0xe9: 0x14, 0xea: 0x63}),
    ('e8=a5 (DELO)', {0xe8: 0xa5, 0xe9: 0x14, 0xea: 0x63}),
    ('e8=e5 (both)', {0xe8: 0xe5, 0xe9: 0x14, 0xea: 0x63}),
    ('e8=25 ea=23', {0xe8: 0x25, 0xe9: 0x14, 0xea: 0x23}),
    ('e8=65 ea=23', {0xe8: 0x65, 0xe9: 0x14, 0xea: 0x23}),
]


def main():
    g, bar, mm, fd = setup(3)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        bus.write(DEC, 0x07, 0x7f)
        bus.write(DEC, 0x00, 0x15)
        time.sleep(1.5)
        s1, s3 = bus.read(DEC, 0x10)[0], bus.read(DEC, 0x13)[0]
        print('decoder: %s S1 %02x S3 %02x; e4..ea now %s' % (
            STD[(s1 >> 4) & 7], s1, s3, ' '.join('%02x' % bus.read(DEC, r)[0] for r in range(0xe4, 0xeb))),
            flush=True)
        if (s1 >> 4) & 7 not in (4, 5, 6):
            print('WARNING: not a 50 Hz standard - switch the console to 50 Hz', flush=True)
        for r, v in PAL.items():
            g.wr(r, v)
        for name, regs in CONFIGS:
            for r, v in regs.items():
                bus.write(DEC, r, v)
            time.sleep(0.8)
            frames, misses = [], 0
            for _ in range(FIELDS):
                if not grab(g, ADDR, 0x82000000):
                    misses += 1
                    continue
                frames.append(read(bar, ADDR, ROW * LINES))
            print('-- %s: %d fields, %d timeouts, S3 %02x' % (name, len(frames), misses, bus.read(DEC, 0x13)[0]),
                  flush=True)
            if frames:
                print(analyse(frames, 720, LINES, name), flush=True)
        for r, v in CONFIGS[0][1].items():
            bus.write(DEC, r, v)
        for r, v in NTSC.items():
            g.wr(r, v)
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
