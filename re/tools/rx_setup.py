#!/usr/bin/env python3
"""Configure the AD9398 HDMI receiver for the kernel driver (which does not do I2C yet).

Writes the Gaia table defaults (needed for the SRI/I2C bridge), loads AD9398 mode 0 then the
given mode, prints receiver status and exits. Run as root with the mxo2 module unloaded:
    sudo python3 -I rx_setup.py [ad9398_mode]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ad9398 import RX, load_mode  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    g = Gaia(write=True)
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        time.sleep(0.3)
        st3 = bus.read(RX, 0x81)[0]
        print('AD9398 mode %d loaded: STATUS1=%02x (67 = locked) STATUS3=%02x (colour space %s)' % (
            rx_mode, bus.read(RX, 0x2f)[0], st3, ['RGB', 'YCbCr 4:2:2', 'YCbCr 4:4:4', '?'][st3 >> 5 & 3]))
    finally:
        g.close()


if __name__ == '__main__':
    main()
