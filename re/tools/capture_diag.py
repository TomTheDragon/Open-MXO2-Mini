#!/usr/bin/env python3
"""Capture diagnostics: is BAR1 RAM, which interrupt bits tick per frame, does the WDMA run?

Steps: Gaia defaults, AD9398 mode, VinControl=0xd02 (HDMI selected); for MemoryTargetControl = 0
and 0xd (the value Windows has while its driver is loaded, notes/win dumps) and VinControl 0xd02 /
0x80000d02:
  1. BAR1 write/readback test at offset 0x100 (original value restored)
  2. clear InterruptStatus0 (InterruptClear0), sample it for 300 ms → bits that come back
  3. same with the video write DMA enabled for each Control variant, plus VideoWDmaStatus and
     VideoWDmaTimeStamp1/2 (should change per captured frame)
Run as root with the mxo2 module unloaded and HDMI plugged in:
    sudo python3 -I capture_diag.py [ad9398_mode]
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import load_mode  # noqa: E402
from gaia import INT_STATUS0, VIN_STATUS_HDMI, Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402

INT_ENABLE0, INT_CLEAR0 = 0x084, 0x088
VIN_CONTROL = 0x780
MEM_TARGET = 0x028
WDMA_CTL, WDMA_CTL2, WDMA_ADDR = 0x580, 0x584, 0x588
WDMA_STATUS, WDMA_TS1, WDMA_TS2 = 0x910, 0x914, 0x918
SYSTEM_CLOCK = 0x188
VARIANTS = [('off', None), ('p7=1/3', 0x84600000), ('p7=6', 0x84400000), ('p7=5', 0x80000000),
            ('p7=0x35', 0x80100000)]


def watch(g, label, ms=300):
    g.wr(INT_CLEAR0, 0xffffffff)
    g.wr(INT_CLEAR0, 0)
    seen_or, seen_toggle, last = 0, 0, g.rd(INT_STATUS0)
    first = last
    regs = {WDMA_STATUS: set(), WDMA_TS1: set(), WDMA_TS2: set(), VIN_STATUS_HDMI: set()}
    t0 = time.monotonic()
    n = 0
    while time.monotonic() - t0 < ms / 1000:
        v = g.rd(INT_STATUS0)
        seen_or |= v
        seen_toggle |= v ^ last
        last = v
        if n % 20 == 0:
            for r in regs:
                regs[r].add(g.rd(r))
        n += 1
        time.sleep(0.0005)
    def fmt(s):
        s = sorted(s)
        return ('%08x' % s[0]) if len(s) == 1 else '%d values %08x..%08x' % (len(s), s[0], s[-1])
    print('%-8s Int0 after clear %08x, OR %08x, toggling %08x | WDmaStatus %s | TS1 %s | TS2 %s | '
          'VinStatusHDMI %s' % (label, first, seen_or, seen_toggle, fmt(regs[WDMA_STATUS]),
                                fmt(regs[WDMA_TS1]), fmt(regs[WDMA_TS2]),
                                fmt(regs[VIN_STATUS_HDMI])), flush=True)


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
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
        g.wr(VIN_CONTROL, 0xd02)
        time.sleep(0.3)
        print('AD9398 STATUS1=%02x  VinStatusHDMI=%08x  SystemClock %08x→%08x (10 ms)' % (
            bus.read(0x98, 0x2f)[0], g.rd(VIN_STATUS_HDMI), g.rd(SYSTEM_CLOCK),
            (time.sleep(0.01), g.rd(SYSTEM_CLOCK))[1]))

        print('InterruptEnable0 reads %08x (Windows: d0dd5030)' % g.rd(INT_ENABLE0))
        for mtc in (0x0, 0xd):
            g.wr(MEM_TARGET, mtc)
            time.sleep(0.05)
            print('==== MemoryTargetControl=%08x (reads %08x)' % (mtc, g.rd(MEM_TARGET)), flush=True)
            old = bar[0x40]
            bar[0x40] = 0x12345678
            a = bar[0x40]
            bar[0x40] = 0xa5a5a5a5
            b = bar[0x40]
            bar[0x40] = old
            print('BAR1 write test @0x100: wrote 12345678 read %08x, wrote a5a5a5a5 read %08x, '
                  'restored %08x' % (a, b, bar[0x40]), flush=True)
            print('BAR1 samples: ' + ' '.join('%06x=%08x' % (o, bar[o // 4]) for o in
                                             (0, 0x1000, 0x100000, 0x400000, 0x7ffffc)), flush=True)
            # FUN_00022a34 (suspend) clears bit31 of VinControl like other engine enables
            for vin in (0xd02, 0x80000d02):
                g.wr(VIN_CONTROL, vin)
                time.sleep(0.05)
                print('-- VinControl=%08x' % vin, flush=True)
                for name, ctl in VARIANTS:
                    g.wr(WDMA_CTL, 0)
                    if ctl is not None:
                        g.wr(WDMA_ADDR, 0)
                        g.wr(WDMA_CTL2, (1920 * 2 // 4) << 16)
                        g.wr(WDMA_CTL, ctl)
                    watch(g, name)
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd03)
        g.wr(MEM_TARGET, 0)
    finally:
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
