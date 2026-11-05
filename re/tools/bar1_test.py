#!/usr/bin/env python3
"""Time single 32-bit BAR1 reads at each init stage; stop as soon as one is slow.

The first capture attempt (capture.py) hung the machine while fingerprinting all 8 MiB of BAR1
after the Gaia defaults were written. The read-only probe before that read BAR1 fine.
Stages: as found → after Gaia defaults → MemoryControl/MemoryTargetControl variants.
Run as root with the mxo2 module unloaded:
    sudo python3 -I bar1_test.py
"""
import mmap
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import Gaia  # noqa: E402
import init  # noqa: E402

MEM_TARGET, MEM_CONTROL = 0x028, 0x02c
SLOW = 0.002          # 2 ms for one read = something is wrong
OFFSETS = [0, 0x1000, 0x100000, 0x400000, 0x7ffffc]


def timed_reads(bar, label):
    res = []
    for off in OFFSETS:
        t0 = time.monotonic()
        v = bar[off // 4]
        dt = time.monotonic() - t0
        res.append('%06x=%08x/%.0fus' % (off, v, dt * 1e6))
        if dt > SLOW:
            print('%-34s %s  ** SLOW, stopping' % (label, ' '.join(res)), flush=True)
            return False
    print('%-34s %s' % (label, ' '.join(res)), flush=True)
    return True


def main():
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDWR | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    try:
        print('MemoryTargetControl=%08x MemoryControl=%08x' % (g.rd(MEM_TARGET), g.rd(MEM_CONTROL)))
        if not timed_reads(bar, 'as found'):
            return
        for off, flags, reset, name in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        print('MemoryTargetControl=%08x MemoryControl=%08x' % (g.rd(MEM_TARGET), g.rd(MEM_CONTROL)))
        if not timed_reads(bar, 'after Gaia defaults'):
            for v in (0x0, 0x1, 0x100):
                g.wr(MEM_CONTROL, v)
                time.sleep(0.01)
                if timed_reads(bar, 'MemoryControl=%x' % v):
                    break
    finally:
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
