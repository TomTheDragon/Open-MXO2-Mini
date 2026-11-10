#!/usr/bin/env python3
"""PAL probe 2: decoder standard selection x FPGA mode code, with a PAL source on composite.

Probe 1 (forced PAL table, 0x00 = 0x85, VinControl code 0x0d): AD_RESULT "NTSC M/J" (autodetect does not run with
a forced standard), Status3 0x8d (PAL swinging-burst lock), 243 lines per field, 75 fields/s, picture = sync
garbage. The NTSC and PAL Windows tables differ only in 0x00 (standard), 0x17/0x19 (filters), 0x2e/0x30 (manual
gains), so one table with the standard set by 0x00 is enough.
For 0x00 in 0x05 (autodetect, NTSC-J no pedestal), 0x15 (autodetect, NTSC-M pedestal), 0x85 (PAL B/G/H/I/D),
0x65 (PAL 60), 0x55 (NTSC M) and VinControl code 0x0d / 0x11 (576i in the Windows mode table): decoder status,
field rate, rows of one packed field, picture extent, colour (mean |Cb|+|Cr| deviation), field image ->
notes/pal/p2-<0x00>-<code>.raw (720 x 340).
Run as root, mxo2 unloaded, PAL source on composite:  sudo python3 -I pal_probe2.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_audio_probe import setup, teardown  # noqa: E402
from analog_probe import DEC  # noqa: E402
from analog_probe5 import changed_rows, grab  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import read  # noqa: E402
from wdma_replay import VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402

ROW = 1440
NROWS = 340
OUT = os.path.join(HERE, '..', 'notes', 'pal')
STD = ['NTSC M/J', 'NTSC 4.43', 'PAL M', 'PAL 60', 'PAL B/G/H/I/D', 'SECAM', 'PAL Comb N', 'SECAM 525']
SELS = [(0x05, 'auto, NTSC-J'), (0x15, 'auto, NTSC-M'), (0x85, 'PAL B/G'), (0x65, 'PAL 60'), (0x55, 'NTSC M')]


def field_rate(g, secs=1.0):
    ts, n, t0 = g.rd(WDMA_TS1), 0, time.monotonic()
    g.wr(WDMA_ADDR, 0x700000)
    g.wr(WDMA_CTL, 0x82000000)
    while time.monotonic() - t0 < secs:
        t = g.rd(WDMA_TS1)
        if t != ts:
            ts, n = t, n + 1
            g.wr(WDMA_ADDR, 0x700000)
            g.wr(WDMA_CTL, 0x82000000)
        time.sleep(0.0003)
    g.wr(WDMA_CTL, 0)
    return n / (time.monotonic() - t0)


def picture(data, rows):
    """Content rows/columns (luma > 40) and mean chroma deviation over content pixels."""
    rws, c0, c1, sat, n = [], 720, -1, 0, 0
    for r in range(rows):
        line = data[r * ROW:(r + 1) * ROW]
        hot = [x for x in range(720) if line[2 * x] > 40]
        if hot:
            rws.append(r)
            c0, c1 = min(c0, hot[0]), max(c1, hot[-1])
            for x in hot[::8]:
                p = (x & ~1) * 2
                sat += abs(line[p + 1] - 128) + abs(line[p + 3] - 128)
                n += 1
    if not rws:
        return 'no content'
    return 'content rows %d..%d cols %d..%d, colour %.1f' % (rws[0], rws[-1], c0, c1, sat / max(n, 1))


def main():
    os.makedirs(OUT, exist_ok=True)
    g, bar, mm, fd = setup(0)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        g.wr(WDMA_CTL2, 0x101 | 360 << 16)
        addr = 0x100000
        print('0x00 standard       code  AD_RESULT        S1 S3  lock fields/s  rows  picture', flush=True)
        for sel, name in SELS:
            for code in (0x0d, 0x11):
                bus.write(DEC, 0x00, sel)
                g.wr(VIN_CONTROL, code << 8)
                time.sleep(1.0)
                s1, s3 = bus.read(DEC, 0x10)[0], bus.read(DEC, 0x13)[0]
                fps = field_rate(g)
                before = read(bar, addr, ROW * NROWS)
                ok = grab(g, addr, 0x82000000)
                data = read(bar, addr, ROW * NROWS)
                rows = changed_rows(before, data)
                tag = '%02x-%02x' % (sel, code)
                open(os.path.join(OUT, 'p2-%s.raw' % tag), 'wb').write(data)
                print('%02x   %-14s %02x    %-16s %02x %02x  %-4s %6.2f    %3d   %s' % (
                    sel, name, code, STD[(s1 >> 4) & 7], s1, s3, 'yes' if s1 & 1 else 'NO', fps,
                    len(rows) if ok else -1, picture(data, max(rows) + 1 if rows else 0)), flush=True)
                print('saved notes/pal/p2-%s.raw (720x%d)' % (tag, NROWS), flush=True)
                addr += 0x80000
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
