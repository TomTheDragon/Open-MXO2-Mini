#!/usr/bin/env python3
"""Diagnose a stuck SRI engine and try the high-priority (HP) read path.

Run as root with the mxo2 module unloaded:
    sudo python3 -I sri_diag.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import INT_STATUS0, SRI_ADDRESS, SRI_DATA, SRI_FIFO, Gaia  # noqa: E402

SRI_CONTROL = 0x884
SRI_DATA_HP = 0x88c


def raw(g, label):
    print('%-26s SriControl=%08x SriData=%08x SriDataHp=%08x SriFifo=%08x Int0=%08x' %
          (label, g.rd(SRI_CONTROL), g.rd(SRI_DATA), g.rd(SRI_DATA_HP), g.rd(SRI_FIFO),
           g.rd(INT_STATUS0)), flush=True)


def poll(g, off, addr, timeout):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = g.rd(off)
        if v & (1 << 30):
            return 'error bit, raw %08x' % v
        if v & (1 << 31) and ((v >> 16) & 0x3ff) == addr:
            return 'OK data=%02x (raw %08x)' % (v & 0xff, v)
    return 'timeout, raw %08x' % g.rd(off)


def hp_read(g, addr, timeout=0.05):
    """HP read as in FUN_00048aa8 mode 2."""
    g.wr(SRI_CONTROL, 1)
    t0 = time.monotonic()
    while g.rd(SRI_CONTROL) & (1 << 31):
        if time.monotonic() - t0 > timeout:
            g.wr(SRI_CONTROL, 0)
            return 'SriControl stays busy (%08x)' % g.rd(SRI_CONTROL)
    g.wr(SRI_ADDRESS, addr & 0x3ff)
    r = poll(g, SRI_DATA_HP, addr, timeout)
    g.wr(SRI_CONTROL, 0)
    return r


def main():
    g = Gaia(write=True)
    try:
        raw(g, 'initial')
        g.wr(SRI_ADDRESS, 4)
        print('normal read bank0/4 (50 ms):', poll(g, SRI_DATA, 4, 0.05))
        raw(g, 'after normal read')
        print('HP read bank0/4:', hp_read(g, 4))
        raw(g, 'after HP read')
        print('HP read bank0/5:', hp_read(g, 5))
        g.wr(SRI_ADDRESS, 5)
        print('normal read bank0/5 (50 ms):', poll(g, SRI_DATA, 5, 0.05))
        raw(g, 'final')
    finally:
        g.close()


if __name__ == '__main__':
    main()
