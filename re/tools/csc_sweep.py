#!/usr/bin/env python3
"""Map receiver output bits → bytes in on-board memory using the AD9398 CSC as a pattern generator.

Datasheet (original/AD9398.PDF, notes/datasheet/AD9398.txt): 0x34 bit1 = CSC enable,
ROUT = A1·R + A2·G + A3·B + A4 (GOUT with B*, BOUT with C*), 13-bit two's complement, 0x35 bits 6:5 =
CSC_Mode (00 = ±1.0, offsets in 12-bit LSBs). With A1..A3, B1..B3, C1..C3 = 0 every pixel becomes
(R, G, B) = (A4, B4, C4) = (Cr, Y, Cb) in YCbCr output (0x25 = 0x4e → 12-bit 4:2:2 output).
For each channel, the 12-bit value is swept over 0, 1<<k, 0xfff (others held at 0x800, Y at 0x800),
a few frames are captured into on-board 0, and 256 words from the middle of the frame are read
through BAR1 window 0. Prints the most common word per step and which stored bits each input bit drives.
Restores the mode table at the end. Run as root, mxo2 unloaded, HDMI source on (any picture):
    sudo python3 -I csc_sweep.py [ad9398_mode] [shadow_log]
"""
import collections
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_TS1  # noqa: E402

MEM_TARGET = 0x028
COEFF_MSB = {'A1': 0x35, 'A2': 0x37, 'A3': 0x39, 'A4': 0x3b, 'B1': 0x3d, 'B2': 0x3f, 'B3': 0x41,
             'B4': 0x43, 'C1': 0x45, 'C2': 0x47, 'C3': 0x49, 'C4': 0x4b}
CHANNELS = [('Cr', 'A4'), ('Y', 'B4'), ('Cb', 'C4')]
MID = 300 * 1920 * 2 // 4   # word index inside the active picture for both 720p and 1080p
STEPS = [0] + [1 << k for k in range(12)] + [0xfff]


def set_coeff(bus, name, val, mode=0):
    msb = (val >> 8) & 0x1f
    if name == 'A1':
        msb |= (mode & 3) << 5
    bus.write(RX, COEFF_MSB[name], msb)
    bus.write(RX, COEFF_MSB[name] + 1, val & 0xff)


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
        print('AD9398 STATUS1=%02x STATUS3=%02x 0x25=%02x' % (
            bus.read(RX, 0x2f)[0], bus.read(RX, 0x81)[0], bus.read(RX, 0x25)[0]), flush=True)

        for name in COEFF_MSB:
            set_coeff(bus, name, 0)
        bus.write(RX, 0x34, bus.read(RX, 0x34)[0] | 2)          # CSC on

        def capture(vals):
            for (_, coeff), v in zip(CHANNELS, vals):
                set_coeff(bus, coeff, v)
            time.sleep(0.05)
            ts0 = g.rd(WDMA_TS1)
            for _ in range(3):
                g.wr(WDMA_ADDR, 0)
                g.wr(WDMA_CTL, ctl)
                time.sleep(1 / 30)
            time.sleep(0.04)
            words = [bar[MID + i] for i in range(256)]
            common, n = collections.Counter(words).most_common(1)[0]
            return common, n, g.rd(WDMA_TS1) != ts0

        print('%-3s %-5s  %-8s  %-7s  %-12s  %s' % ('ch', 'value', 'word', 'same', 'lanes b0..b3',
                                                     'stored bits changed vs value 0 (lane.bit)'),
              flush=True)
        for ci, (ch, _) in enumerate(CHANNELS):
            base = None
            for v in STEPS:
                vals = [0x800, 0x800, 0x800]
                vals[ci] = v
                w, n, ok = capture(vals)
                if base is None:
                    base = w
                diff = w ^ base
                bits = ' '.join('%d.%d' % (b // 8, b % 8) for b in range(32) if diff >> b & 1)
                print('%-3s %03x    %08x  %3d/256  %s  %s%s' % (
                    ch, v, w, n, ' '.join('%02x' % (w >> (8 * i) & 0xff) for i in range(4)), bits,
                    '' if ok else '  (NO new frame)'), flush=True)
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
