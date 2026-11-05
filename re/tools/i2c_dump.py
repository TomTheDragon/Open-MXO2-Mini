#!/usr/bin/env python3
"""Read-only full register dump of the chips found on I2CCtl (see i2c.py).

Run as root with the mxo2 module unloaded:
    sudo python3 -I i2c_dump.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402

# (8-bit address, name, number of registers)
DEVICES = [(0xa2, 'EEPROM', 0x100), (0x98, 'AD9398 HDMI receiver', 0x100),
           (0x72, 'AD9889B HDMI transmitter', 0x100), (0x7e, 'AD9889B EDID memory', 0x100),
           (0x70, 'AD9889B? (0x70)', 0x100), (0x54, 'ADV7341/7321 encoder', 0x100),
           (0x42, 'ADV7181/74xx decoder (main map)', 0x100), (0x90, 'Tnc75 temperature', 4)]


def main():
    g = Gaia(write=True)
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        for dev, name, n in DEVICES:
            print('\n# %02x %s' % (dev, name))
            for base in range(0, n, 16):
                try:
                    b = bus.read(dev, base, min(16, n - base))
                except TimeoutError as e:
                    print('%02x: %s' % (base, e))
                    break
                txt = ''.join(chr(x) if 32 <= x < 127 else '.' for x in b)
                print('%02x: %-47s  %s' % (base, ' '.join('%02x' % x for x in b), txt))
    finally:
        g.close()


if __name__ == '__main__':
    main()
