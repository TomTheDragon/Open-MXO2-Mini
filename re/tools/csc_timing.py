#!/usr/bin/env python3
"""Find AD9398 output timing settings that make the chroma bits land correctly in memory.

csc_sweep.py showed (12-bit 4:2:2 output, 0x24=f0 0x25=4e): Cr byte bit7 = Cb[11] (Cr[11] lost),
Cb byte bit0 = Cr[4]; the other chroma bits and all luma bits are right, chroma samples are noisy.
Those are CbCr[11] = R7 and CbCr[4] = R0, the outer wires of the chroma port → sampling/timing.
Knobs (datasheet): 0x24 bit0 output clock invert, 0x25 [7:6] output clock (01 1×, 11 1× 90°),
[5:4] drive strength (Matrox uses 00 = lowest; power-up default 11).
For each combination the CSC generates constant colours (as csc_sweep.py) and the stored bytes are
compared with the expected ones (Y byte = Y>>4, Cb byte = Cb>>4, Cr byte = Cr>>4):
    ok     = patterns whose most common word is exactly right (of len(PATTERNS))
    badbit = stored bits that were wrong in any pattern (lane.bit)
    stable = average share of identical words in the 256 sampled (noise indicator)
Finally the mode table is reloaded, the best combination is applied with the CSC off and one camera
frame is saved to notes/csc-frames/timing-best.raw (+ chroma smoothness score, plain decode).
Run as root, mxo2 unloaded, HDMI source on:
    sudo python3 -I csc_timing.py [ad9398_mode] [shadow_log]
"""
import collections
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from csc_sweep import CHANNELS, COEFF_MSB, MID, set_coeff  # noqa: E402
from csc_test import chroma_score, grab  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL  # noqa: E402

MEM_TARGET = 0x028
# (Cr, Y, Cb), 12-bit; chosen so every chroma bit 11..4 is both 0 and 1 in some pattern, Cr != Cb
PATTERNS = [(0x000, 0x800, 0xff0), (0xff0, 0x800, 0x000), (0x5a0, 0x3c0, 0xa50), (0xa50, 0xc30, 0x5a0),
            (0x810, 0x800, 0x7e0), (0x7e0, 0x800, 0x810), (0x800, 0x800, 0x800), (0x330, 0x800, 0xcc0)]


def expected(cr, y, cb):
    """Little-endian word: lane0 Y0, lane1 Cb, lane2 Y1, lane3 Cr."""
    return (y >> 4) | (cb >> 4) << 8 | (y >> 4) << 16 | (cr >> 4) << 24


def measure(g, bus, bar, ctl):
    """Run PATTERNS through the CSC generator → (exact matches, OR of wrong bits, stability 0..1)."""
    ok, bad, same = 0, 0, 0
    for pat in PATTERNS:
        for (_, coeff), v in zip(CHANNELS, pat):
            set_coeff(bus, coeff, v)
        time.sleep(0.03)
        for _ in range(3):
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, ctl)
            time.sleep(1 / 30)
        time.sleep(0.04)
        words = [bar[MID + i] for i in range(256)]
        w, n = collections.Counter(words).most_common(1)[0]
        exp = expected(*pat)
        ok += w == exp
        bad |= w ^ exp
        same += n
    return ok, bad, same / len(PATTERNS) / 256


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
        r24, r25 = bus.read(RX, 0x24)[0], bus.read(RX, 0x25)[0]
        print('AD9398 STATUS1=%02x STATUS3=%02x 0x24=%02x 0x25=%02x' % (
            bus.read(RX, 0x2f)[0], bus.read(RX, 0x81)[0], r24, r25), flush=True)
        for name in COEFF_MSB:
            set_coeff(bus, name, 0)
        bus.write(RX, 0x34, bus.read(RX, 0x34)[0] | 2)          # CSC on = colour generator

        results = []
        print('%-4s %-4s  %-6s %-6s  %s' % ('0x24', '0x25', 'ok', 'stable', 'wrong bits (lane.bit)'),
              flush=True)
        for inv in (0, 1):
            for clk in (1, 3):
                for drive in range(4):
                    v24 = (r24 & ~1) | inv
                    v25 = (r25 & 0x0f) | clk << 6 | drive << 4
                    bus.write(RX, 0x24, v24)
                    bus.write(RX, 0x25, v25)
                    time.sleep(0.1)
                    ok, bad, stable = measure(g, bus, bar, ctl)
                    results.append((ok, stable, v24, v25))
                    print('%02x   %02x    %d/%d    %3.0f %%  %s' % (
                        v24, v25, ok, len(PATTERNS), stable * 100,
                        ' '.join('%d.%d' % (b // 8, b % 8) for b in range(32) if bad >> b & 1) or '-'),
                        flush=True)

        ok, stable, v24, v25 = max(results)
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        bus.write(RX, 0x24, v24)
        bus.write(RX, 0x25, v25)
        time.sleep(0.3)
        print('camera frame with 0x24=%02x 0x25=%02x (CSC as in the mode table):' % (v24, v25), flush=True)
        os.makedirs(os.path.join(HERE, '..', 'notes', 'csc-frames'), exist_ok=True)
        grab(g, bar, ctl, 'timing-best')
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        if bus:
            load_mode(bus, 0)
            load_mode(bus, rx_mode)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
