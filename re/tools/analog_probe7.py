#!/usr/bin/env python3
"""Analog probe 7: capture with decoder overrides -> 240p field + woven frame + bar levels.

Probe 6: luma filter 0x17 = 0xa2 removes the subcarrier dots; luma AGC (0x2c 9e..de, sync reference) gives
grey bar 0.60 (= NES $10), i.e. 2x the manual table/EEPROM gain; chroma AGC (0x2c bits 1:0 = 10) more
saturation; 0x04 = 0xcc limited range (black 16).
Default overrides: 04=cc 17=a2 2c=ce. Extra/other overrides as REG=VAL hex args (e.g. 2c=ae).
Saves notes/analog/p7-field.raw (720x243 packed, Control 0x82000000) and p7-frame.raw (720x486 weave).
Run as root, mxo2 unloaded:  sudo python3 -I analog_probe7.py [REG=VAL ...]
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from analog_probe import DEC, OUT, VIN_CONTROL_ANALOG, dec_status, load_decoder  # noqa: E402
from analog_probe5 import grab  # noqa: E402
from analog_probe6 import BARS, stats  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, read  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import SYSTEM, VIN_CONTROL, WDMA_CTL, WDMA_CTL2  # noqa: E402

ROW = 1440
DEFAULT = {0x04: 0xcc, 0x17: 0xa2, 0x2c: 0xce}


def main():
    over = dict(DEFAULT)
    for a in sys.argv[1:]:
        r, v = a.split('=')
        over[int(r, 16)] = int(v, 16)
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', 'analog', '2-preview.txt'))
    os.makedirs(OUT, exist_ok=True)
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        for off in SYSTEM:
            g.wr(off, shadow[off][0])
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        bus.write(RX, 0x26, bus.read(RX, 0x26)[0] | 1)
        load_decoder(bus, 0)
        for r, v in sorted(over.items()):
            bus.write(DEC, r, v)
        time.sleep(0.8)
        print('overrides ' + ' '.join('%02x=%02x' % rv for rv in sorted(over.items())), flush=True)
        print('decoder:%s' % dec_status(bus)[3], flush=True)
        g.wr(MEM_TARGET, 0)
        g.wr(VIN_CONTROL, 0x0d00)
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        g.wr(WDMA_CTL2, 0x101 | 360 << 16)

        ok = grab(g, 0x100000, 0x82000000)
        field = read(bar, 0x100000, ROW * 243)
        with open(os.path.join(OUT, 'p7-field.raw'), 'wb') as f:
            f.write(field)
        print('saved notes/analog/p7-field.raw (720x243 packed, %s)' % ('ok' if ok else 'timeout'), flush=True)
        rows = {r: field[r * ROW:(r + 1) * ROW] for r in range(243)}
        st, black = stats(rows)
        print('top bars  blk %5.1f  ' % black + '  '.join(
            '%s %5.1f(%3.1f) %3.0f/%3.0f' % (n, y, sd, u, v) for n, y, sd, u, v in st), flush=True)
        # bottom row of SMPTE bars (field rows ~185..230): sample the same x positions
        low = {r: rows[r] for r in range(190, 230, 4)}
        out = []
        for name, cx in BARS:
            x = cx & ~1
            y = sum(low[r][2 * x] for r in low) / len(low)
            u = sum(low[r][2 * x + 1] for r in low) / len(low)
            v = sum(low[r][2 * x + 3] for r in low) / len(low)
            out.append('x%d %5.1f %3.0f/%3.0f' % (cx, y, u, v))
        print('bottom    ' + '  '.join(out), flush=True)

        ok = grab(g, 0x200000, 0x80000000) and grab(g, 0x200000, 0x81000000)
        with open(os.path.join(OUT, 'p7-frame.raw'), 'wb') as f:
            f.write(read(bar, 0x200000, ROW * 486))
        print('saved notes/analog/p7-frame.raw (720x486 weave, %s)' % ('ok' if ok else 'timeout'), flush=True)
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(WDMA_CTL2, 0x41)
        g.wr(VIN_CONTROL, 0xd00)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
