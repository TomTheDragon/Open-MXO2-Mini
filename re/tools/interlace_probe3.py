#!/usr/bin/env python3
"""Interlace probe 3: hardware weave of two fields into one 1920x1080 frame.

Probe 2: with a line pitch in VideoWDmaControl2[26:16], scan mode p5 = 0 writes field line i to row 2i
(of the pitch), p5 = 1 to row 2i+1, stopping at 1080 rows; p5 = 2/3 ignore the pitch. InterruptStatus0
bit 24 alternates per field; read right after VideoWDmaTimeStamp1 changes, bit 24 = 1 came before a
TOP field (half a line above the previous one).
Here: pitch 3840 (one line), and at every TS1 change the next capture is armed into the same frame slot
with p5 = 0 (even rows) or 1 (odd rows) chosen from bit 24:
  rule A: bit24 = 1 -> top field -> p5 0        rule B: the inverse (deliberately wrong)
After 8 fields the slot holds the last two fields woven. Comb metric on luma: mean |row i - row i+1|
relative to mean |row i - row i+2|; the correct field order gives the smaller ratio. Frames saved as
notes/interlace/weave-A.raw / weave-B.raw (1920x1080, view with tools/raw2png.py ... plain).
Run as root, mxo2 unloaded, 1080i source (static scene with horizontal detail is best):
    sudo python3 -I interlace_probe3.py [ad9398_mode]
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, ROW, luma_rows, read, rowdiff, wait_ts  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402
import mmap  # noqa: E402

INT_STATUS0 = 0x080
OUT = os.path.join(HERE, '..', 'notes', 'interlace')
SLOT = {'A': 0, 'B': 0x400000}


def comb(raw):
    """Mean |row i - row i+1| over ALL i (wrong field order alternates 1- and 3-line distances) and,
    for scale, mean |row i - row i+2| (always 2 lines of one field apart)."""
    rows = luma_rows(raw, 1080, step=2)
    d1 = sum(rowdiff(rows[i], rows[i + 1]) for i in range(10, 1060)) / 1050
    d2 = sum(rowdiff(rows[i], rows[i + 2]) for i in range(10, 1060)) / 1050
    return d1, d2


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt'))
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        for off in SYSTEM + INPUT:
            g.wr(off, shadow[off][0])
        g.wr(VIN_CONTROL, 0x0d02)
        g.wr(WDMA_CTL, 0)
        g.wr(MEM_TARGET, 0)
        g.wr(WDMA_CTL2, 0x41 | (ROW // 4) << 16)
        time.sleep(0.3)
        print('AD9398 VIC %d, STATUS1 %02x' % (bus.read(RX, 0x84)[0], bus.read(RX, 0x2f)[0]), flush=True)
        results = {}
        for rule in ('A', 'B'):
            base = SLOT[rule]
            seq = []
            ts = g.rd(WDMA_TS1)
            g.wr(WDMA_ADDR, base)
            g.wr(WDMA_CTL, 0x81000000)          # first field: whichever, overwritten later
            for _ in range(8):
                ts = wait_ts(g, ts)
                if ts is None:
                    print('  rule %s: capture stopped' % rule, flush=True)
                    break
                top = bool(g.rd(INT_STATUS0) >> 24 & 1)
                if rule == 'B':
                    top = not top
                p5 = 0 if top else 1
                seq.append(p5)
                g.wr(WDMA_ADDR, base)
                g.wr(WDMA_CTL, 0x80000000 | p5 << 24)
            wait_ts(g, ts)                      # let the last armed field land
            g.wr(WDMA_CTL, 0)
            time.sleep(0.03)
            raw = read(bar, base, ROW * 1080)
            with open(os.path.join(OUT, 'weave-%s.raw' % rule), 'wb') as f:
                f.write(raw)
            d1, d2 = comb(raw)
            results[rule] = d1 / d2
            print('rule %s: p5 sequence %s | row diff +1 %.2f, +2 %.2f, ratio %.3f' % (
                rule, ''.join(map(str, seq)), d1, d2, d1 / d2), flush=True)
        best = min(results, key=results.get)
        print('-> smoother (correct field order): rule %s%s' % (
            best, ' (bit24=1 before a TOP field)' if best == 'A' else ' (bit24=1 before a BOTTOM field)'),
            flush=True)
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(WDMA_CTL2, 0x41)
        g.wr(VIN_CONTROL, 0xd00)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
