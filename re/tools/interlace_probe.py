#!/usr/bin/env python3
"""How does the video write DMA handle an interlaced (1080i) HDMI input?

VideoWDmaControl (MVKAVBUS FUN_00014a14) = 0x80000000 | u3 << 26 | (p5 & 3) << 24 | 10bit << 23 | fmt << 20;
progressive captures use p5 = 2 (0x82000000). p5 is probably the scan/field mode. VinControl bits 12:8 may
be the FPGA mode code (1080i50 = 3, 1080i60 = 5; we always used 0x0d = 1080p50 for progressive inputs).
For every combination of VinControl code and p5 this probe:
  - fills on-board slot 0 (first 4 MiB) with a marker through BAR1, if BAR1 accepts writes
  - re-arms the WDMA for 1 s and counts VideoWDmaTimeStamp1 changes (50/s = per field, 25/s = per frame)
  - captures once more, reads the slot back and counts 3840-byte rows written (no marker left), where the
    written area ends, and the luma similarity of neighbouring rows (fields stored separately look smoother
    row-to-row than two fields woven together on moving edges - mostly useful together with the images)
  - saves notes/interlace/<vin>-p<p5>.raw for tools/raw2png.py
Run as root, mxo2 unloaded, an interlaced 1080i HDMI source connected:
    sudo python3 -I interlace_probe.py [ad9398_mode]       (default 5 = AD9398 1080i table)
"""
import array
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
VIN_STATUS_HDMI = 0x7cc
ROW = 3840
ROWS = 1080
SIZE = ROW * ROWS
MARK = 0x5a17c0de
OUT = os.path.join(HERE, '..', 'notes', 'interlace')


def ctl(p5):
    """go | scan mode; pixel format field 0 = 8-bit 4:2:2 (Windows fmt 5)"""
    return 0x80000000 | (p5 & 3) << 24


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt'))
    os.makedirs(OUT, exist_ok=True)
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDWR | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
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
        g.wr(WDMA_CTL, 0)
        g.wr(MEM_TARGET, 0)
        time.sleep(0.3)
        r = lambda reg: bus.read(RX, reg)[0]  # noqa: E731
        hsvs = (r(0x17) & 0xf) << 8 | r(0x18)
        print('AD9398 mode %d: STATUS1=%02x STATUS2=%02x HDMI MODE=%02x VIC=%d HS/VS=%d (1080i: ~562 per field)' % (
            rx_mode, r(0x2f), r(0x30), r(0x5b), r(0x84), hsvs), flush=True)

        old = bar[100]
        bar[100] = MARK
        writable = bar[100] == MARK
        bar[100] = old
        print('BAR1 writable: %s' % writable, flush=True)

        for code in (0x0d, 0x03, 0x05):
            vin = code << 8 | 0x02
            g.wr(VIN_CONTROL, vin)
            time.sleep(0.2)
            print('== VinControl %04x  VinStatusHDMI %08x' % (vin, g.rd(VIN_STATUS_HDMI)), flush=True)
            for p5 in range(4):
                g.wr(WDMA_CTL, 0)
                if writable:
                    for i in range(SIZE // 4):
                        bar[i] = MARK
                # rate of TS1 changes with continuous re-arming
                ts, n, t0 = g.rd(WDMA_TS1), 0, time.monotonic()
                g.wr(WDMA_ADDR, 0)
                g.wr(WDMA_CTL, ctl(p5))
                while time.monotonic() - t0 < 1.0:
                    v = g.rd(WDMA_TS1)
                    if v != ts:
                        ts, n = v, n + 1
                        g.wr(WDMA_ADDR, 0)
                        g.wr(WDMA_CTL, ctl(p5))
                    time.sleep(0.0005)
                time.sleep(0.1)          # let the last capture finish, no new arm
                words = array.array('I', (bar[i] for i in range(SIZE // 4)))
                raw = words.tobytes()
                with open(os.path.join(OUT, '%04x-p%d.raw' % (vin, p5)), 'wb') as f:
                    f.write(raw)
                mark = MARK.to_bytes(4, 'little') * (ROW // 4)
                written = [raw[i * ROW:(i + 1) * ROW] != mark for i in range(ROWS)]
                last = max((i for i, w in enumerate(written) if w), default=-1)
                ys = [raw[i * ROW:(i + 1) * ROW:2] for i in range(0, min(ROWS, last + 1))]

                def diff(a, b):
                    return sum(abs(x - y) for x, y in zip(a[::8], b[::8])) / max(1, len(a[::8]))
                d1 = sum(diff(ys[i], ys[i + 1]) for i in range(0, len(ys) - 2, 4)) / max(1, len(ys) // 4)
                d2 = sum(diff(ys[i], ys[i + 2]) for i in range(0, len(ys) - 2, 4)) / max(1, len(ys) // 4)
                print('  p5=%d: %3d TS1 changes/s | rows written %s (last row %d) | row diff +1 %.1f, +2 %.1f' % (
                    p5, n, sum(written) if writable else '?', last, d1, d2), flush=True)
        g.wr(WDMA_CTL, 0)
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
