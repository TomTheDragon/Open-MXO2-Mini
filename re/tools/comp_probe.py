#!/usr/bin/env python3
"""Component probe 1: SD component (YPbPr 480i / 576i) through the ADV7181.

Windows (MVKAVCIO mode choice, re/NOTES.md "Analog inputs"): input 2 = component, decoder table 2 (NTSC / 525) or
5 (PAL / 625), hidden-register script b7=1b; FPGA VinControlAnalog bit 16 set only for component (MVKAVBUS
FUN_0002bf54). The SD component tables keep the 8-bit 656 output (0x03 = 0x80) like composite.
For decoder table 2 and 5, VinControlAnalog 0xc0000000 / 0xc0010000: decoder status, fields/s, rows of one
packed field, picture extent / colour -> notes/comp/c1-<table>-<van>.raw (720 x 340); for the matching table a
woven frame c1-<table>-frame.raw (720 x 680). FPGA timing (Control2 bit 4, genlock, clocks) per table: 2 = NTSC,
5 = PAL (pal_probe3 values).
Run as root, mxo2 unloaded, component source on the Y/Pb/Pr inputs:  sudo python3 -I comp_probe.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_audio_probe import setup, teardown  # noqa: E402
from analog_probe import DEC, VIN_CONTROL_ANALOG, dec_status, load_decoder  # noqa: E402
from analog_probe5 import changed_rows, describe, grab  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import read  # noqa: E402
from pal_probe2 import NROWS, ROW, field_rate, picture  # noqa: E402
from pal_probe3 import NTSC, PAL  # noqa: E402
from wdma_replay import WDMA_CTL2  # noqa: E402

OUT = os.path.join(HERE, '..', 'notes', 'comp')
HIDDEN_COMP = [(0xb7, 0x1b)]
VANS = (0xc0000000, 0xc0010000)


def main():
    os.makedirs(OUT, exist_ok=True)
    g, bar, mm, fd = setup(0)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        addr = 0x100000
        results = {}
        for table in (2, 5):
            load_decoder(bus, table)
            for r, v in HIDDEN_COMP:
                bus.write(DEC, r, v)
            fpga = PAL if table == 5 else NTSC
            for r, v in fpga.items():
                g.wr(r, v)
            time.sleep(1.0)
            print('== decoder table %d (%s):%s; 0x00..0x07 %s' % (
                table, '625 / PAL' if table == 5 else '525 / NTSC', dec_status(bus)[3],
                ' '.join('%02x' % bus.read(DEC, r)[0] for r in range(8))), flush=True)
            for van in VANS:
                g.wr(VIN_CONTROL_ANALOG, van)
                time.sleep(0.5)
                fps = field_rate(g)
                before = read(bar, addr, ROW * NROWS)
                ok = grab(g, addr, 0x82000000)
                data = read(bar, addr, ROW * NROWS)
                rows = changed_rows(before, data)
                tag = '%d-%x' % (table, van >> 16)
                open(os.path.join(OUT, 'c1-%s.raw' % tag), 'wb').write(data)
                s1, _, s3, _ = dec_status(bus)
                print('VinControlAnalog %08x: S1 %02x S3 %02x, %6.2f fields/s, %s rows, %s' % (
                    van, s1, s3, fps, len(rows) if ok else 'timeout', picture(data, max(rows) + 1 if rows else 0)),
                    flush=True)
                print('saved notes/comp/c1-%s.raw (720x%d)' % (tag, NROWS), flush=True)
                results[(table, van)] = (fps, len(rows) if ok else 0)
                addr += 0x80000
            # woven frame with the VinControlAnalog that wrote more rows
            van = max(VANS, key=lambda v: results[(table, v)][1])
            g.wr(VIN_CONTROL_ANALOG, van)
            time.sleep(0.3)
            n = 2 * NROWS
            before = read(bar, addr, ROW * n)
            g.wr(WDMA_CTL2, fpga[WDMA_CTL2])
            ok = grab(g, addr, 0x80000000, n=1) and grab(g, addr, 0x81000000, n=1)
            data = read(bar, addr, ROW * n)
            open(os.path.join(OUT, 'c1-%d-frame.raw' % table), 'wb').write(data)
            print('weave (VinControlAnalog %08x): %s' % (
                van, describe(changed_rows(before, data)) if ok else 'timeout'), flush=True)
            print('saved notes/comp/c1-%d-frame.raw (720x%d)' % (table, n), flush=True)
            addr += 0x100000
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        for r, v in NTSC.items():
            g.wr(r, v)
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
