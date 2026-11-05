#!/usr/bin/env python3
"""Try AD9398 settings that could fix YCbCr 4:2:2 input (HDMI camera), one variant at a time.

Laptop (RGB input, STATUS3 0x1e) captures with correct colours; the camera (YCbCr 4:2:2, STATUS3 0x20)
gets mangled chroma, on Windows too. Candidates (datasheet):
  0x50 "Test – must be written to 0x20 for proper operation": chip powers up with 0x80
       (notes/i2c-dump.txt) and neither the Matrox mode table nor we ever write it.
  0x34 bit3 Up Conversion Mode (1 = interpolate Cr/Cb), bit2 CrCb Filter Enable.
Per variant: mode table reloaded, variant written, known-colour check (CSC as generator,
csc_timing.PATTERNS), mode table reloaded + variant again, one camera frame saved to
notes/csc-frames/fix-<variant>.raw with its chroma smoothness score (plain decode; laptop-like
correct colours ≈ low plain score). Mode table (without 0x50) restored at the end.
Run as root, mxo2 unloaded, HDMI camera on at 1080p50:
    sudo python3 -I rx_fix_test.py [ad9398_mode] [shadow_log]
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from csc_sweep import COEFF_MSB, set_coeff  # noqa: E402
from csc_test import grab  # noqa: E402
from csc_timing import PATTERNS, measure  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_CTL  # noqa: E402

MEM_TARGET = 0x028
# (name, [(reg, and_mask, or_value)])
VARIANTS = [
    ('base', []),
    ('r50', [(0x50, 0x00, 0x20)]),
    ('upconv', [(0x34, 0xff, 0x08)]),
    ('filter', [(0x34, 0xff, 0x04)]),
    ('r50-upconv', [(0x50, 0x00, 0x20), (0x34, 0xff, 0x08)]),
    ('r50-upconv-filter', [(0x50, 0x00, 0x20), (0x34, 0xff, 0x0c)]),
]


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    bus = None
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        g.wr(VIN_CONTROL, 0xd02)
        for off in SYSTEM + INPUT:
            g.wr(off, shadow[off][0])
        g.wr(WDMA_CTL, 0)
        g.wr(MEM_TARGET, 0)
        ctl = shadow[WDMA_CTL][0]
        r50_orig = bus.read(RX, 0x50)[0]
        print('AD9398 STATUS1=%02x STATUS3=%02x (20 = YCbCr 4:2:2) 0x50=%02x 0x56=%02x 0x34=%02x' % (
            bus.read(RX, 0x2f)[0], bus.read(RX, 0x81)[0], r50_orig, bus.read(RX, 0x56)[0],
            bus.read(RX, 0x34)[0]), flush=True)
        os.makedirs(os.path.join(HERE, '..', 'notes', 'csc-frames'), exist_ok=True)

        def setup(writes):
            bus.write(RX, 0x50, r50_orig)
            load_mode(bus, rx_mode)
            for reg, mask, val in writes:
                bus.write(RX, reg, (bus.read(RX, reg)[0] & mask) | val)
            time.sleep(0.3)

        for name, writes in VARIANTS:
            setup(writes)
            print('== %-18s 0x50=%02x 0x34=%02x STATUS1=%02x' % (
                name, bus.read(RX, 0x50)[0], bus.read(RX, 0x34)[0], bus.read(RX, 0x2f)[0]), flush=True)
            for coeff in COEFF_MSB:
                set_coeff(bus, coeff, 0)
            bus.write(RX, 0x34, bus.read(RX, 0x34)[0] | 2)      # CSC on = colour generator
            ok, bad, stable = measure(g, bus, bar, ctl)
            print('   colours %d/%d exact, %3.0f %% stable, wrong bits: %s' % (
                ok, len(PATTERNS), stable * 100,
                ' '.join('%d.%d' % (b // 8, b % 8) for b in range(32) if bad >> b & 1) or '-'), flush=True)
            setup(writes)
            grab(g, bar, ctl, 'fix-' + name)
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        if bus:
            bus.write(RX, 0x50, r50_orig)
            load_mode(bus, 0)
            load_mode(bus, rx_mode)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
