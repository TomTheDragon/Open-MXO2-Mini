#!/usr/bin/env python3
"""Configure the AD9398 HDMI receiver like Windows, then watch its status.

Steps (MVKAVCIO): Gaia register defaults, AD9398 mode 0 (chip creation, FUN_00014a5c),
then mode N (default 7 = 720p60), then if HDMI MODE (0x5b) bit3 is set: FUN_00014c10 + reset
pulse on reg 0x59 bits 1,2 (FUN_00014ce4), else FUN_00014c90 (reg 0x34 |= 2).
Afterwards status registers are printed whenever they change.

Plug plan for the default 60 s watch: 0-15 s nothing, 15-60 s HDMI source plugged in.
Run as root with the mxo2 module unloaded:
    sudo python3 -I ad9398.py [mode] [seconds]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ad9398_modes import MODES  # noqa: E402
from gaia import VIN_STATUS_HDMI, Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402

RX = 0x98
STATUS = [(0x2f, 'STATUS1'), (0x30, 'STATUS2'), (0x17, 'HSVS1'), (0x18, 'HSVS2'),
          (0x5a, 'PKT'), (0x5b, 'HDMIMODE'), (0x81, 'STATUS3'), (0x82, 'STATUS4'), (0x84, 'VIC')]


def load_mode(bus, mode):
    for reg, val in MODES[mode]:
        bus.write(RX, reg, val)
    hdmi = bus.read(RX, 0x5b)[0]
    gs2 = bus.read(RX, 0x34)[0]                    # idx 0x1b = GENERAL SETUP 2 (0x34)
    if hdmi & 8:
        st3 = bus.read(RX, 0x81)[0]                # idx 0x46 = STATUS REG 3
        bus.write(RX, 0x34, gs2 | 2 if (st3 & 0x60) == 0 else gs2 & ~2 & 0xff)
        gs3 = bus.read(RX, 0x59)[0]                # idx 0x38 = GENERAL SETUP 3
        bus.write(RX, 0x59, gs3 | 6)
        bus.write(RX, 0x59, gs3 & ~6 & 0xff)
    else:
        bus.write(RX, 0x34, gs2 | 2)
    return hdmi


def main():
    mode = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60
    g = Gaia(write=True)
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        print('before: 24..27 =', ' '.join('%02x' % v for v in bus.read(RX, 0x24, 4)))
        load_mode(bus, 0)
        h = load_mode(bus, mode)
        print('loaded mode 0 then mode %d, HDMI MODE=%02x' % (mode, h))
        bad = [(r, v, bus.read(RX, r)[0]) for r, v in MODES[mode]]
        bad = [b for b in bad if b[1] != b[2] and b[0] not in (0x34, 0x59)]
        print('readback mismatches (reg want got):', ' '.join('%02x:%02x/%02x' % b for b in bad)
              or 'none')
        print('plug plan: 0-15 s nothing, 15-%d s HDMI source plugged in' % secs, flush=True)
        last, t0 = {}, time.monotonic()
        while time.monotonic() - t0 < secs:
            cur = {n: '%02x' % bus.read(RX, r)[0] for r, n in STATUS}
            cur['VinStatusHDMI'] = '%08x' % g.rd(VIN_STATUS_HDMI)
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
