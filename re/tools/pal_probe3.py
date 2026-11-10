#!/usr/bin/env python3
"""PAL probe 3: decoder autodetect mask + the FPGA values Windows changes for PAL (notes/win/pal/).

Probe 2: with the PAL decoder standard (0x00 = 0x85) colour is right but the picture is sheared and the FPGA counts
75 fields/s; AD_RESULT always reads NTSC because our tables write 0x07 (autodetect enable mask) = 0x00.
Windows PAL vs NTSC composite: VideoWDmaControl2 0x01680111 (bit 4), GenlockControl 0x1101 (0x1000),
SystemClockReload 0xf988 (0x8f48), ClockLoadCount 0x42060 (0x37176); VinControl stays 0x0d00.
HDMIHsync/Vsync/HalfLine 0x850/0x854/0x858 also differ (probably output timing; the driver never writes them).

Part 1: 0x07 = 0x7f with 0x00 = 0x15 / 0x05 (autodetect) -> AD_RESULT, Status1/3.
Part 2: forced PAL (0x85), FPGA combos -> fields/s, rows of a packed field, picture extent, colour,
notes/pal/p3-<combo>.raw (720 x 340); for the full Windows set also a woven frame p3-<combo>-frame.raw (720 x 680).
Run as root, mxo2 unloaded, PAL source on composite:  sudo python3 -I pal_probe3.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_audio_probe import setup, teardown  # noqa: E402
from analog_probe import DEC  # noqa: E402
from analog_probe5 import changed_rows, describe, grab  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import read  # noqa: E402
from pal_probe2 import NROWS, OUT, ROW, STD, field_rate, picture  # noqa: E402
from wdma_replay import WDMA_CTL2  # noqa: E402

GENLOCK, CLK_RELOAD, CLK_LOAD = 0x100, 0x190, 0x184
HSYNC, VSYNC, HALF = 0x850, 0x854, 0x858
NTSC = {WDMA_CTL2: 0x01680101, GENLOCK: 0x1000, CLK_RELOAD: 0x8f48, CLK_LOAD: 0x37176,
        HSYNC: 0x00a00024, VSYNC: 0x00070004, HALF: 0x37e}
PAL = {WDMA_CTL2: 0x01680111, GENLOCK: 0x1101, CLK_RELOAD: 0xf988, CLK_LOAD: 0x42060,
       HSYNC: 0x00940016, VSYNC: 0x00060003, HALF: 0x377}
COMBOS = [('base', []), ('ctl2', [WDMA_CTL2]), ('genlock', [GENLOCK]), ('clocks', [CLK_RELOAD, CLK_LOAD]),
          ('ctl2-genlock', [WDMA_CTL2, GENLOCK]),
          ('win', [WDMA_CTL2, GENLOCK, CLK_RELOAD, CLK_LOAD]),
          ('win-sync', [WDMA_CTL2, GENLOCK, CLK_RELOAD, CLK_LOAD, HSYNC, VSYNC, HALF])]


def status(bus):
    s1, s3 = bus.read(DEC, 0x10)[0], bus.read(DEC, 0x13)[0]
    return '%-16s S1 %02x S3 %02x lock %-3s interlaced %s' % (
        STD[(s1 >> 4) & 7], s1, s3, 'yes' if s1 & 1 else 'NO', 'yes' if s3 & 0x40 else 'no')


def weave(g, bar, addr, ctl2):
    """Two fields woven (Control bit 24 = 0 / 1, line stride 2 rows) -> (rows changed, data)."""
    n = 2 * NROWS
    before = read(bar, addr, ROW * n)
    g.wr(WDMA_CTL2, ctl2)
    ok = grab(g, addr, 0x80000000, n=1) and grab(g, addr, 0x81000000, n=1)
    data = read(bar, addr, ROW * n)
    return (changed_rows(before, data) if ok else None), data


def main():
    os.makedirs(OUT, exist_ok=True)
    g, bar, mm, fd = setup(3)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        print('-- part 1: autodetect (0x07 = 0x7f)', flush=True)
        for sel in (0x15, 0x05):
            bus.write(DEC, 0x07, 0x7f)
            bus.write(DEC, 0x00, sel)
            time.sleep(1.5)
            print('0x00 %02x: %s' % (sel, status(bus)), flush=True)
        bus.write(DEC, 0x07, 0x00)
        bus.write(DEC, 0x00, 0x85)
        time.sleep(1.0)
        print('forced PAL 0x85: %s' % status(bus), flush=True)

        print('-- part 2: forced PAL, FPGA values from the Windows PAL run', flush=True)
        print('combo          fields/s  rows  picture', flush=True)
        addr = 0x100000
        for name, regs in COMBOS:
            vals = {r: (PAL if r in regs else NTSC)[r] for r in NTSC}
            for r, v in vals.items():
                g.wr(r, v)
            time.sleep(0.5)
            fps = field_rate(g)
            before = read(bar, addr, ROW * NROWS)
            ok = grab(g, addr, 0x82000000)
            data = read(bar, addr, ROW * NROWS)
            rows = changed_rows(before, data)
            open(os.path.join(OUT, 'p3-%s.raw' % name), 'wb').write(data)
            print('%-14s %6.2f    %3d   %s' % (name, fps, len(rows) if ok else -1,
                                               picture(data, max(rows) + 1 if rows else 0)), flush=True)
            print('saved notes/pal/p3-%s.raw (720x%d)' % (name, NROWS), flush=True)
            addr += 0x80000
            if name.startswith('win'):
                rows, data = weave(g, bar, addr, vals[WDMA_CTL2])
                open(os.path.join(OUT, 'p3-%s-frame.raw' % name), 'wb').write(data)
                print('  weave: %s' % (describe(rows) if rows is not None else 'timeout'), flush=True)
                print('saved notes/pal/p3-%s-frame.raw (720x%d)' % (name, 2 * NROWS), flush=True)
                print('  decoder: %s' % status(bus), flush=True)
                addr += 0x100000
        for r, v in NTSC.items():
            g.wr(r, v)
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
