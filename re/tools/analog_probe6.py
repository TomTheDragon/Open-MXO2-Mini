#!/usr/bin/env python3
"""Analog probe 6: ADV7181 level / filter sweep on SMPTE colour bars (240p Test Suite, NTSC composite).

Probe 5: data clean (plain YUYV) but levels low: black Y 1 (0x04 bit 0 = extended range 1..254), grey bar 77,
white 112, chroma ~0.45x; dot pattern in coloured bars (luma sd 5-6 = subcarrier left in luma; NES 240p has
no standard chroma phase, so the comb filter cannot remove it). AGC is manual (0x2c = 0x8c) with the mode
table's gains (chroma 0x2cc, luma 0x4a6); Windows writes the EEPROM NTSC record instead (0x2e5 / 0x4cb).
Each config: decoder mode 0 + changes, one packed field (Control 0x82000000, 243 lines), per-bar Y/Cb/Cr means,
black, luma sd in the green bar. Run as root, mxo2 unloaded:  sudo python3 -I analog_probe6.py
"""
import mmap
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from analog_probe import DEC, VIN_CONTROL_ANALOG, load_decoder  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, read  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402

ROW = 1440
ADDR = 0x100000
BARS = [('wht', 90), ('yel', 182), ('cyn', 275), ('grn', 362), ('mag', 452), ('red', 545), ('blu', 637)]
ROWS = range(30, 140, 6)                  # field rows inside the top bars (frame rows 60..280)


def gains(lmg, cmg):
    return [(0x2d, 0xf0 | cmg >> 8), (0x2e, cmg & 0xff), (0x2f, 0xb0 | lmg >> 8), (0x30, lmg & 0xff)]


CONFIGS = [
    ('table (as probe 5)', []),
    ('EEPROM NTSC gains', gains(0x4cb, 0x2e5)),
    ('gains x2', gains(0x94c, 0x598)),
    ('limited range 0x04=cc', [(0x04, 0xcc)]),
    ('EEPROM gains + limited', gains(0x4cb, 0x2e5) + [(0x04, 0xcc)]),
] + [('AGC 0x2c=%02x' % v, [(0x2c, v)]) for v in (0x9e, 0xae, 0xbe, 0xce, 0xde, 0xee, 0xfe, 0x8e)] + [
    ('luma filter 0x17=%02x' % v, [(0x17, v)]) for v in (0xa0, 0xa1, 0xa2, 0xac, 0xad, 0xae, 0xaf, 0xb0, 0xb1, 0xb2, 0xb3)] + [
    ('comb 0x19=%02x' % v, [(0x19, v)]) for v in (0xf0, 0xf1, 0xf2, 0xf4, 0xf8, 0xfc)]


def field(g, bar):
    """One packed field (243 lines) at ADDR; returns the sample rows or None on timeout."""
    ts = g.rd(WDMA_TS1)
    for _ in range(2):
        g.wr(WDMA_ADDR, ADDR)
        g.wr(WDMA_CTL, 0x82000000)
        t0 = time.monotonic()
        while g.rd(WDMA_TS1) == ts:
            if time.monotonic() - t0 > 0.5:
                return None
            time.sleep(0.0005)
        ts = g.rd(WDMA_TS1)
    g.wr(WDMA_CTL, 0)
    time.sleep(0.02)
    return {r: read(bar, ADDR + r * ROW, ROW) for r in list(ROWS) + [225]}


def stats(rows):
    out = []
    for name, cx in BARS:
        y, u, v = [], [], []
        for r in ROWS:
            ln = rows[r]
            for x in range((cx - 20) & ~1, cx + 20, 2):
                y += [ln[2 * x], ln[2 * x + 2]]
                u.append(ln[2 * x + 1])
                v.append(ln[2 * x + 3])
        out.append((name, statistics.mean(y), statistics.pstdev(y), statistics.mean(u), statistics.mean(v)))
    black = statistics.mean(rows[225][2 * x] for x in range(8, 30))
    return out, black


def main():
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', 'analog', '2-preview.txt'))
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        for off in SYSTEM:
            g.wr(off, shadow[off][0])
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        bus.write(RX, 0x26, bus.read(RX, 0x26)[0] | 1)
        g.wr(MEM_TARGET, 0)
        g.wr(VIN_CONTROL, 0x0d00)
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        g.wr(WDMA_CTL2, 0x101 | 360 << 16)
        print('Y mean (sd) / Cb Cr per bar; blk = left border Y.  75%% bars ref (BT.601 limited): '
              'wht 180, yel 162/44/142, cyn 131/156/44, grn 112/72/58, mag 84/184/198, red 65/100/212, '
              'blu 35/212/114', flush=True)
        for name, changes in CONFIGS:
            load_decoder(bus, 0)
            for reg, val in changes:
                bus.write(DEC, reg, val)
            time.sleep(0.5)
            rows = field(g, bar)
            if rows is None:
                print('%-24s timeout' % name, flush=True)
                continue
            st, black = stats(rows)
            print('%-24s blk %5.1f  ' % (name, black) + '  '.join(
                '%s %5.1f(%3.1f) %3.0f/%3.0f' % (n, y, sd, u, v) for n, y, sd, u, v in st), flush=True)
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
