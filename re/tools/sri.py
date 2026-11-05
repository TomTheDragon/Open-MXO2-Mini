#!/usr/bin/env python3
"""Dump the Gaia SRI (serial register interface) address space.

Protocol (from MVKAVBUS.sys FUN_00048aa8, normal/FIFO mode):
  write SriAddress (0x880) = bank << 10 | addr (10 bits)
  poll  SriData    (0x888) until bit31 (valid) is set
        bit30 = error, bits 25..16 = echoed address, bits 7..0 = data

Bank 1 is skipped by default: reading it on an idle box left the SRI engine
stuck (2026-11-03). Pass --bank1 to include it anyway.

Run as root with the mxo2 module unloaded:
    sudo python3 -I sri.py [--bank1] > ../notes/sri-dump.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import SRI_DATA, SRI_FIFO, Gaia  # noqa: E402


def main():
    banks = (0, 1) if '--bank1' in sys.argv else (0,)
    g = Gaia(write=True)
    try:
        print('# SriFifoCount=0x%08x SriData=0x%08x' % (g.rd(SRI_FIFO), g.rd(SRI_DATA)))
        for bank in banks:
            errs = 0
            print('\n# bank %d' % bank)
            for base in range(0, 0x400, 16):
                row = []
                for a in range(base, base + 16):
                    v, e = g.sri_read(bank, a)
                    if e:
                        errs += 1
                        row.append('--')
                        if errs <= 5:
                            print('# bank %d addr 0x%03x: %s' % (bank, a, e))
                    else:
                        row.append('%02x' % v)
                print('%03x: %s' % (base, ' '.join(row)))
                if errs >= 64 and base == 0x30:
                    print('# first 64 reads all failed, skipping rest of bank')
                    break
            print('# bank %d errors: %d' % (bank, errs))
    finally:
        g.close()


if __name__ == '__main__':
    main()
