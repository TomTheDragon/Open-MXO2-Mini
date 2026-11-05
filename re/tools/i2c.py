#!/usr/bin/env python3
"""Read-only probe of the I2C buses behind SRI (from MVKAVCIO.sys).

The FPGA has I2C masters in SRI bank 0 (MVKAVCIO FUN_0003041c / FUN_0005c7d0):
  "I2CCtl"  control = SRI 3, data = SRI 4   (all box chips are on this one)
  "I2CCtl2" control = SRI 6, data = SRI 7
  "leSPICtl" (SPI) control = SRI 1, data = SRI 2   (not used here)
Control commands (FUN_0005cf2c): 6,7 = start; 5,7 = stop; before each read
byte 3 = ack / 7 = nack (last byte); then data = 0xff and read data back.
There is no ACK check, so an absent device reads 0xff.

By default the Gaia register defaults are written first, as Windows does
(see init.py). Run as root with the mxo2 module unloaded:
    sudo python3 -I i2c.py [--no-init] [--scan]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import Gaia  # noqa: E402
import init  # noqa: E402

BUSES = {'I2CCtl': (3, 4), 'I2CCtl2': (6, 7)}

# 8-bit I2C addresses MVKAVCIO uses on I2CCtl (FUN_0003041c and chip constructors).
KNOWN = [(0x98, 'AD9398 HDMI/analog receiver'), (0x72, 'AD9889B HDMI transmitter'),
         (0x70, 'AD9889B (GMP variant)'), (0x54, 'ADV7341/ADV7321 video encoder'),
         (0x42, 'ADV74xx/ADV7181 video decoder?'), (0xe0, 'SI5338 clock generator'),
         (0xa2, 'FUN_00040578 (EEPROM?)'), (0x46, 'FUN_0005ec18 "LE"'),
         (0x5c, 'FUN_00054770 #1'), (0x58, 'FUN_00054770 #2'), (0xd2, 'FUN_0003f708'),
         (0x8c, 'FUN_0004d04c')]


class I2C:
    def __init__(self, g, ctrl, data):
        self.g, self.ctrl, self.data = g, ctrl, data

    def _c(self, v):
        self.g.sri_write(0, self.ctrl, v)

    def _d(self, v):
        self.g.sri_write(0, self.data, v)

    def write(self, dev, reg, *vals):
        """Register write as in FUN_0005c99c: start, addr, reg, bytes, stop."""
        self._c(6); self._c(7)
        self._d(dev & 0xfe)
        self._d(reg & 0xff)
        for v in vals:
            self._d(v)
        self._c(5); self._c(7)

    def read(self, dev, reg, count=1):
        """Return a list of bytes, or raise on SRI timeout."""
        self._c(6); self._c(7)
        self._d(dev & 0xfe)
        self._d(reg & 0xff)
        self._c(6); self._c(7)
        self._d(dev | 1)
        out = []
        for i in range(count):
            self._c(7 if i == count - 1 else 3)
            self._d(0xff)
            self.g.sri_wait_free(0x200)          # Windows waits for an empty FIFO
            v, e = self.g.sri_read(0, self.data, timeout=0.05)
            if e:
                raise TimeoutError('SRI read: ' + e)
            out.append(v)
        self._c(5); self._c(7)
        return out


def main():
    g = Gaia(write=True)
    try:
        if '--no-init' not in sys.argv:
            n = 0
            for off, flags, reset, _ in init.gaia_table():
                if flags in (2, 3):
                    g.wr(off, reset)
                    n += 1
            print('# wrote %d Gaia defaults' % n)
        print('# SRI bank 0 before:', ' '.join('%02x' % (g.sri_read(0, a)[0] or 0)
                                               for a in range(8)))
        bus = I2C(g, *BUSES['I2CCtl'])
        print('\n# I2CCtl known devices, registers 0x00..0x0f')
        for dev, what in KNOWN:
            try:
                b = bus.read(dev, 0, 16)
                print('%02x %-34s %s' % (dev, what, ' '.join('%02x' % x for x in b)))
            except TimeoutError as e:
                print('%02x %-34s %s' % (dev, what, e))
        if '--scan' in sys.argv:
            for name, (c, d) in BUSES.items():
                bus = I2C(g, c, d)
                print('\n# %s scan: reg 0, 1 of every address (ff ff = probably absent)' % name)
                for dev in range(0x02, 0x100, 2):
                    try:
                        b = bus.read(dev, 0, 2)
                    except TimeoutError as e:
                        print('%02x %s' % (dev, e))
                        continue
                    if b != [0xff, 0xff]:
                        print('%02x %02x %02x' % (dev, b[0], b[1]))
        print('\n# SRI bank 0 after:', ' '.join('%02x' % (g.sri_read(0, a)[0] or 0)
                                              for a in range(8)))
    finally:
        g.close()


if __name__ == '__main__':
    main()
