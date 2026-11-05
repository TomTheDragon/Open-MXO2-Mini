#!/usr/bin/env python3
"""GPIO experiment: does driving the 7 Gaia GPIO pins wake up the box's chips?

Steps (each followed by a snapshot of SRI and input-status registers):
  1. baseline (pins are inputs, as after power-up)
  2. GpIO=0, GpOEnable=0x7f  -> same state the Windows driver sets at start
  3. each pin 0..6 driven high alone for ~1 s, then low again
  4. all pins high
At the end the pins are switched back to inputs (GpOEnable=0).

Run as root with the mxo2 module unloaded:
    sudo python3 -I gpio.py > ../notes/gpio-test.txt
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import (GPIO, GPOENABLE, INT_STATUS0, VIN_STATUS_ANALOG,  # noqa: E402
                  VIN_STATUS_HDMI, Gaia)

SRI_PROBE = [(0, a) for a in range(0, 16)] + [(1, a) for a in range(0, 16)]


def snapshot(g, label):
    sri = []
    for bank, addr in SRI_PROBE:
        v, e = g.sri_read(bank, addr)
        sri.append('--' if e else '%02x' % v)
    print('%-22s GpIO=%08x VinHDMI=%08x VinAnalog=%08x Int0=%08x' %
          (label, g.rd(GPIO), g.rd(VIN_STATUS_HDMI), g.rd(VIN_STATUS_ANALOG), g.rd(INT_STATUS0)))
    print('%-22s SRI0: %s' % ('', ' '.join(sri[:16])))
    print('%-22s SRI1: %s' % ('', ' '.join(sri[16:])), flush=True)


def main():
    g = Gaia(write=True)
    try:
        snapshot(g, 'baseline (inputs)')
        g.wr(GPIO, 0)
        g.wr(GPOENABLE, 0x7f)
        time.sleep(0.5)
        snapshot(g, 'all outputs low')
        for pin in range(7):
            g.wr(GPIO, 1 << pin)
            time.sleep(1.0)
            snapshot(g, 'pin %d high' % pin)
            g.wr(GPIO, 0)
            time.sleep(0.3)
        g.wr(GPIO, 0x7f)
        time.sleep(1.0)
        snapshot(g, 'all high')
        time.sleep(2.0)
        snapshot(g, 'all high +2s')
    finally:
        g.wr(GPIO, 0)
        g.wr(GPOENABLE, 0)
        time.sleep(0.2)
        snapshot(g, 'restored (inputs)')
        g.close()


if __name__ == '__main__':
    main()
