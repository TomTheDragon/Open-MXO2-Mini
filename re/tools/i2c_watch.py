#!/usr/bin/env python3
"""Read-only: watch AD9398 (HDMI RX) and ADV7181 (analog decoder) status while plugging sources.

Prints a line whenever a watched value changes. Plan for a 90 s run:
  0-20 s nothing plugged, 20-45 s HDMI source plugged into the box, 45-65 s unplugged,
  65-90 s composite source plugged.
Run as root with the mxo2 module unloaded:
    sudo python3 -I i2c_watch.py [seconds]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import VIN_STATUS_ANALOG, VIN_STATUS_HDMI, Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402

# (8-bit addr, register, name) – names from notes/MVKAVCIO.chipregs.txt
WATCH = [(0x98, 0x17, 'rx HS/VS 1'), (0x98, 0x18, 'rx HS/VS 2'), (0x98, 0x26, 'rx OUT3/PWRDN'),
         (0x98, 0x2f, 'rx STATUS1'), (0x98, 0x30, 'rx STATUS2'), (0x98, 0x5a, 'rx PKT STATUS'),
         (0x98, 0x5b, 'rx HDMI MODE'), (0x98, 0x81, 'rx STATUS3'), (0x98, 0x82, 'rx STATUS4'),
         (0x98, 0x84, 'rx VIC'),
         (0x42, 0x10, 'dec STATUS1'), (0x42, 0x12, 'dec STATUS2'), (0x42, 0x13, 'dec STATUS3')]


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 90
    g = Gaia(write=True)
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        last = {}
        t0 = time.monotonic()
        print('plug plan: 20-45 s HDMI in, 65-90 s composite in', flush=True)
        while time.monotonic() - t0 < secs:
            cur = {}
            for dev, reg, name in WATCH:
                try:
                    cur[name] = '%02x' % bus.read(dev, reg)[0]
                except TimeoutError:
                    cur[name] = '??'
            cur['VinStatusHDMI'] = '%08x' % g.rd(VIN_STATUS_HDMI)
            cur['VinStatusAnalog'] = '%08x' % g.rd(VIN_STATUS_ANALOG)
            diff = {k: v for k, v in cur.items() if last.get(k) != v}
            if diff:
                print('%5.1f s  %s' % (time.monotonic() - t0,
                                        '  '.join('%s=%s' % kv for kv in diff.items())), flush=True)
            last = cur
            time.sleep(0.25)
    finally:
        g.close()


if __name__ == '__main__':
    main()
