#!/usr/bin/env python3
"""Analog probe 5: colours (test pattern) and 240p layout.

Setup as probe 4 (Windows analog system values, decoder mode, VideoWDmaControl2 bit 8 = analog source).
1. Decoder registers 0x00-0x1f + status (0x13 bit 6 = interlaced).
2. Colour: two woven frames (Control bit 24 = 0 then 1, width 360 dwords) at card address 0x600000 / 0x700000
   -> notes/analog/colour-{a,b}.raw (720 x 600 rows read), plus which 1440-byte rows the capture changed
   (= lines per frame).
3. 240p layout: for several Control2 / Control values one capture into an untouched area; prints which
   1440-byte rows changed (count, first/last, gaps) -> shows packed vs. every-other-row writes.
Run as root, mxo2 unloaded, composite source on:  sudo python3 -I analog_probe5.py [decoder_mode]
"""
import collections
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from analog_probe import DEC, OUT, VIN_CONTROL_ANALOG, dec_status, load_decoder  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, read  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402

ROW = 1440
NROWS = 600
ANALOG = 0x101
LAYOUTS = [  # (name, Control2, Control)
    ('weave-even', ANALOG | 360 << 16, 0x80000000),
    ('width0', ANALOG, 0x80000000),
    ('width0-b25', ANALOG, 0x82000000),
    ('w360-b25', ANALOG | 360 << 16, 0x82000000),
    ('w180', ANALOG | 180 << 16, 0x80000000),
    ('w720', ANALOG | 720 << 16, 0x80000000),
]


def grab(g, addr, ctl, n=2, timeout=0.5):
    """n captures to `addr` (re-armed after each TimeStamp1 change); False on timeout."""
    ts = g.rd(WDMA_TS1)
    for _ in range(n):
        g.wr(WDMA_ADDR, addr)
        g.wr(WDMA_CTL, ctl)
        t0 = time.monotonic()
        while g.rd(WDMA_TS1) == ts:
            if time.monotonic() - t0 > timeout:
                g.wr(WDMA_CTL, 0)
                return False
            time.sleep(0.0005)
        ts = g.rd(WDMA_TS1)
    g.wr(WDMA_CTL, 0)
    time.sleep(0.03)
    return True


def changed_rows(before, after):
    return [i for i in range(len(after) // ROW) if before[i * ROW:(i + 1) * ROW] != after[i * ROW:(i + 1) * ROW]]


def describe(rows):
    if not rows:
        return 'no rows changed'
    gaps = collections.Counter(b - a for a, b in zip(rows, rows[1:]))
    par = collections.Counter(r & 1 for r in rows)
    return '%d rows changed, first %d last %d, gaps %s, parity even %d odd %d' % (
        len(rows), rows[0], rows[-1], dict(gaps.most_common(4)), par[0], par[1])


def main():
    mode = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', 'analog', '2-preview.txt'))
    os.makedirs(OUT, exist_ok=True)
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
        load_decoder(bus, mode)
        time.sleep(0.6)
        s3 = dec_status(bus)[2]
        print('decoder mode %d:%s' % (mode, dec_status(bus)[3]), flush=True)
        print('Status3 bit 6 (interlaced) = %d' % (s3 >> 6 & 1), flush=True)
        regs = bus.read(DEC, 0x00, 0x20)
        print('decoder 00-1f: ' + ' '.join('%02x' % b for b in regs), flush=True)
        g.wr(MEM_TARGET, 0)
        g.wr(VIN_CONTROL, 0x0d00)
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        n = ROW * NROWS

        print('--- colour', flush=True)
        g.wr(WDMA_CTL2, ANALOG | 360 << 16)
        for tag, addr in (('a', 0x600000), ('b', 0x700000)):
            before = read(bar, addr, n)
            ok = grab(g, addr, 0x80000000) and grab(g, addr, 0x81000000)
            after = read(bar, addr, n)
            with open(os.path.join(OUT, 'colour-%s.raw' % tag), 'wb') as f:
                f.write(after)
            print('saved notes/analog/colour-%s.raw (720x%d, %s): %s' % (
                tag, NROWS, 'ok' if ok else 'timeout', describe(changed_rows(before, after))), flush=True)

        print('--- 240p layout (one capture each, into an untouched area)', flush=True)
        addr = 0x100000
        for name, c2, ctl in LAYOUTS:
            g.wr(WDMA_CTL2, c2)
            before = read(bar, addr, n)
            ok = grab(g, addr, ctl, n=1)
            after = read(bar, addr, n)
            with open(os.path.join(OUT, 'layout-%s.raw' % name), 'wb') as f:
                f.write(after)
            print('%-11s Control2 %08x Control %08x %-7s %s' % (
                name, c2, ctl, 'ok' if ok else 'timeout', describe(changed_rows(before, after))), flush=True)
            addr += 0x100000
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(WDMA_CTL2, 0x41)
        g.wr(VIN_CONTROL, 0xd00)
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
