#!/usr/bin/env python3
"""Replay the Windows register-creation step, then retry SRI.

MVKAVBUS FUN_00013590 writes every writable Gaia register (flag 2 or 3) with
its table "reset" value, in table order, when it builds the register objects.
That includes PllControl, AudioPllCtl and the 0x80000000 pulse on reg 0x8.
We never did this, which may be why SriControl bit31 (busy) never clears.

Run as root with the mxo2 module unloaded:
    sudo python3 -I init.py
"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gaia import INT_STATUS0, SRI_DATA, SRI_FIFO, Gaia  # noqa: E402

REGMAP = os.path.join(HERE, '..', 'notes', 'MVKAVBUS.regmap.txt')
GAIA_TABLE = '# table @0x4e730'
SRI_CONTROL = 0x884
SRI_DATA_HP = 0x88c

WATCH = [(0x000, 'BoardStatus'), (0x008, 'GaiaVersion'), (0x014, 'GenlockStatus'),
         (0x080, 'InterruptStatus0'), (0x7c4, 'VinStatusAnalog'), (0x7cc, 'VinStatusHDMI'),
         (SRI_CONTROL, 'SriControl'), (SRI_DATA, 'SriData'), (SRI_FIFO, 'SriFifoCount')]


def gaia_table():
    """Return [(offset, flags, reset, name)] for the Gaia table, in order."""
    regs, inside = [], False
    line_re = re.compile(r'\s+0x([0-9a-f]+)\s+(\d)\s+reset=0x([0-9a-f]+)\s+idx=\s*\d+\s+(.*)')
    with open(REGMAP) as f:
        for line in f:
            if line.startswith('# table'):
                inside = line.startswith(GAIA_TABLE)
                continue
            m = line_re.match(line) if inside else None
            if m:
                regs.append((int(m[1], 16), int(m[2]), int(m[3], 16), m[4].strip()))
    if not regs:
        sys.exit('Gaia table not found in ' + REGMAP)
    return regs


def snap(g, label):
    print('--', label)
    for off, name in WATCH:
        print('   %-18s @%03x = %08x' % (name, off, g.rd(off)))


def main():
    regs = gaia_table()
    g = Gaia(write=True)
    try:
        snap(g, 'before')
        n = 0
        for off, flags, reset, name in regs:
            if flags in (2, 3):
                g.wr(off, reset)
                n += 1
        print('wrote defaults to %d writable registers (of %d)' % (n, len(regs)))
        time.sleep(0.1)
        snap(g, 'after defaults (+100 ms)')

        print('SRI bank 0 reads:')
        for a in range(16):
            v, e = g.sri_read(0, a, timeout=0.05)
            print('   addr %02x: %s' % (a, e if e else '%02x' % v))
            if e and a >= 3:
                print('   (stopping after repeated failures)')
                break
        snap(g, 'final')
    finally:
        g.close()


if __name__ == '__main__':
    main()
