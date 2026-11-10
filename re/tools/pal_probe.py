#!/usr/bin/env python3
"""PAL composite probe: what the card stores for a 50 Hz composite signal (PAL source on the composite input).

NTSC (probe 5): 243 lines per field, 486 per frame; the driver skips 2/4 lines to deliver 240/480. For PAL the driver
assumes 288 lines per field and skips none - unchecked. This probe, with the ADV7181 PAL table (mode 3) + the
driver's overrides:
  - decoder status (lock, detected standard, Status3 bit 6 = interlaced source)
  - field rate (packed captures, TimeStamp1 changes per second)
  - rows the card writes: one packed field (Control 0x82000000) and one woven frame (bit 24 = 0 then 1) into fresh
    areas -> count/first/last changed 1440-byte rows
  - picture extent in both: first/last row and column with content (luma > 40) -> vertical offset to skip and the
    horizontal centre for the 256/320 formats
  -> notes/pal/field.raw (packed, 720 x rows) and frame.raw (woven)
Run as root, mxo2 unloaded, PAL source on composite:  sudo python3 -I pal_probe.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_audio_probe import setup, teardown  # noqa: E402
from analog_probe import DEC, dec_status  # noqa: E402
from analog_probe5 import changed_rows, describe, grab  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import read  # noqa: E402
from wdma_replay import WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402

ROW = 1440
OUT = os.path.join(HERE, '..', 'notes', 'pal')
STD = ['NTSC M/J', 'NTSC 4.43', 'PAL M', 'PAL 60', 'PAL B/G/H/I/D', 'SECAM', 'PAL Comb N', 'SECAM 525']


def extent(data, nrows, step=1):
    """First/last content row and column (luma > 40) in rows 0, step, 2*step, ..."""
    rows, cols = [], set()
    for r in range(0, nrows, step):
        line = data[r * ROW:(r + 1) * ROW]
        ys = line[0::2]
        hot = [x for x, y in enumerate(ys) if y > 40]
        if hot:
            rows.append(r)
            cols.update((hot[0], hot[-1]))
    if not rows:
        return 'no content (luma > 40) found'
    return 'content rows %d..%d, columns %d..%d (centre %.1f)' % (rows[0], rows[-1], min(cols), max(cols),
                                                                   (min(cols) + max(cols)) / 2)


def main():
    os.makedirs(OUT, exist_ok=True)
    g, bar, mm, fd = setup(3)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        for _ in range(3):
            s1, s2, s3, txt = dec_status(bus)
            print('decoder:%s | standard %s, Status3 bit 6 (interlaced) %d' % (txt, STD[(s1 >> 4) & 7],
                                                                              s3 >> 6 & 1), flush=True)
            time.sleep(0.3)
        if not s1 & 1:
            print('RESULT: decoder not locked - is a PAL source on the composite input?')
            return

        g.wr(WDMA_CTL2, 0x101 | 360 << 16)
        # field rate: packed captures back to back for 2 s
        ts, n, t0 = g.rd(WDMA_TS1), 0, time.monotonic()
        g.wr(WDMA_ADDR, 0x100000)
        g.wr(WDMA_CTL, 0x82000000)
        while time.monotonic() - t0 < 2:
            t = g.rd(WDMA_TS1)
            if t != ts:
                ts, n = t, n + 1
                g.wr(WDMA_ADDR, 0x100000)
                g.wr(WDMA_CTL, 0x82000000)
            time.sleep(0.0003)
        g.wr(WDMA_CTL, 0)
        print('field rate: %.2f fields/s' % (n / (time.monotonic() - t0)), flush=True)

        # one packed field into a fresh area
        nrows = 340
        before = read(bar, 0x200000, ROW * nrows)
        ok = grab(g, 0x200000, 0x82000000)
        field = read(bar, 0x200000, ROW * nrows)
        print('packed field (Control 0x82000000, %s): %s' % ('ok' if ok else 'timeout',
                                                             describe(changed_rows(before, field))), flush=True)
        print('  %s' % extent(field, nrows), flush=True)
        open(os.path.join(OUT, 'field.raw'), 'wb').write(field)
        print('saved notes/pal/field.raw (720x%d)' % nrows, flush=True)

        # one woven frame (bit 24 = 0 then 1) into a fresh area
        nrows = 650
        before = read(bar, 0x400000, ROW * nrows)
        ok = grab(g, 0x400000, 0x80000000) and grab(g, 0x400000, 0x81000000)
        frame = read(bar, 0x400000, ROW * nrows)
        print('woven frame (%s): %s' % ('ok' if ok else 'timeout', describe(changed_rows(before, frame))),
              flush=True)
        print('  %s' % extent(frame, nrows), flush=True)
        open(os.path.join(OUT, 'frame.raw'), 'wb').write(frame)
        print('saved notes/pal/frame.raw (720x%d)' % nrows, flush=True)
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
