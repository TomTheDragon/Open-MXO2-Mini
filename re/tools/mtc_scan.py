#!/usr/bin/env python3
"""Read-only scan: what does BAR1 show for each MemoryTargetControl (0x028) value?

Windows keeps MemoryTargetControl = 0xd (notes/win dumps); with it BAR1 changed content
(notes/capture-diag.txt). For every value 0..0x1f this samples BAR1 at a few offsets twice
(to see live/changing data) and counts distinct words in the first 4 KiB. No BAR1 writes
(one target may be flash). Restores the original value.
Run as root with the mxo2 module unloaded:
    sudo python3 -I mtc_scan.py
"""
import mmap
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import Gaia  # noqa: E402

MEM_TARGET = 0x028
OFFSETS = [0x0, 0x4, 0x8, 0x100, 0x1000, 0x10000, 0x100000, 0x400000, 0x7ffffc]


def main():
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    orig = g.rd(MEM_TARGET)
    print('MemoryTargetControl as found %08x, MemoryControl %08x' % (orig, g.rd(0x02c)), flush=True)
    try:
        for mtc in list(range(0x20)) + [0x100, 0x10000, 0x80000000]:
            g.wr(MEM_TARGET, mtc)
            time.sleep(0.01)
            rb = g.rd(MEM_TARGET)
            t0 = time.monotonic()
            a = [bar[o // 4] for o in OFFSETS]
            dt = (time.monotonic() - t0) / len(OFFSETS)
            if dt > 0.002:
                print('%08x reads slow (%.0f us), stopping' % (mtc, dt * 1e6), flush=True)
                break
            b = [bar[o // 4] for o in OFFSETS]
            page = [bar[i] for i in range(1024)]
            live = ''.join('*' if x != y else '.' for x, y in zip(a, b))
            print('%08x rb=%08x  %s  live=%s  4K: %d distinct, %d zero, %d ffffffff' % (
                mtc, rb, ' '.join('%08x' % x for x in a), live, len(set(page)),
                page.count(0), page.count(0xffffffff)), flush=True)
    finally:
        g.wr(MEM_TARGET, orig)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
