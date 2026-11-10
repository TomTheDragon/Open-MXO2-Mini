#!/usr/bin/env python3
"""PAL probe 6: find a field-parity indicator for 288p (progressive PAL fields alternate by one line).

Driver stream (notes/pal-timing-test.txt): consecutive 288p fields alternate 0/1 line with every ADV7181 0xE8/0xEA
setting -> fix in the driver by skipping one line on every other field, which needs each field's parity.
Captures ~80 consecutive packed fields (double buffer, re-armed right after each VideoWDmaTimeStamp1 change),
gets each field's parity from the picture (row-profile shift vs field 0), and right after each field snapshots
every side-effect-free readable Gaia register. Reports register bits that follow the parity, the timestamp
deltas per parity, and raw values of a few candidates.
Run as root, mxo2 unloaded, PAL 50 Hz progressive source on composite:  sudo python3 -I pal_probe6.py
"""
import array
import collections
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_audio_probe import setup, teardown  # noqa: E402
from analog_probe import DEC  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from pal_probe2 import ROW, STD  # noqa: E402
from pal_probe3 import NTSC, PAL  # noqa: E402
from wdma_replay import WDMA_ADDR, WDMA_CTL, WDMA_TS1  # noqa: E402
from wobble import best_shift  # noqa: E402

FIELDS = 80
ROWS = 64                       # picture starts near row 29: enough to see a one-line shift
BUFS = (0x100000, 0x200000)
SKIP = {0x088, 0x680, 0x688, 0x884, 0x888, 0x88c, 0x890}   # clear-on-access / FIFO data: don't touch
WATCH = [(0x7c4, 'VinStatusAnalog'), (0x910, 'WDmaStatus'), (0x918, 'WDmaTS2'), (0x080, 'Int0'),
         (0x014, 'GenlockStatus'), (0x930, 'WDmaDebug')]


def rows_profile(bar, addr):
    words = array.array('I', (bar[(addr + r * ROW) // 4 + i] for r in range(ROWS) for i in range(0, 360, 4)))
    data = words.tobytes()
    per = len(data) // ROWS
    return [sum(data[r * per:(r + 1) * per:2]) / (per / 2) for r in range(ROWS)]


def main():
    regs = [o for o, f, _, _ in init.gaia_table() if f in (1, 3) and o not in SKIP]
    g, bar, mm, fd = setup(3)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        bus.write(DEC, 0x07, 0x7f)
        bus.write(DEC, 0x00, 0x15)
        time.sleep(1.5)
        s1 = bus.read(DEC, 0x10)[0]
        print('decoder: %s S1 %02x S3 %02x' % (STD[(s1 >> 4) & 7], s1, bus.read(DEC, 0x13)[0]), flush=True)
        for r, v in PAL.items():
            g.wr(r, v)
        time.sleep(0.5)
        snaps, profs, ts = [], [], []
        k = 0
        g.wr(WDMA_ADDR, BUFS[0])
        g.wr(WDMA_CTL, 0x82000000)
        last = g.rd(WDMA_TS1)
        t0 = time.monotonic()
        while len(snaps) < FIELDS + 1 and time.monotonic() - t0 < 10:
            t = g.rd(WDMA_TS1)
            if t == last:
                time.sleep(0.0002)
                continue
            snap = {o: g.rd(o) for o in regs}
            done = BUFS[k & 1]
            k += 1
            g.wr(WDMA_ADDR, BUFS[k & 1])
            g.wr(WDMA_CTL, 0x82000000)
            last = t
            snaps.append(snap)
            ts.append(t)
            profs.append(rows_profile(bar, done))
        g.wr(WDMA_CTL, 0)
        # the first capture may have started mid-field: drop it
        snaps, profs, ts = snaps[1:], profs[1:], ts[1:]
        n = len(snaps)
        par = [best_shift(p, profs[0], 3) for p in profs]
        dts = [b - a for a, b in zip(ts, ts[1:])]
        print('%d fields in %.2f s; parity (shift vs field 0): %s' % (
            n, time.monotonic() - t0, ''.join('%+d' % p for p in par)), flush=True)
        print('TS1 deltas: %s' % ' '.join(str(d) for d in dts[:40]), flush=True)
        med = sorted(dts)[len(dts) // 2] if dts else 0
        gaps = [i for i, d in enumerate(dts) if d > 1.5 * med]
        print('median TS1 delta %d, missed-field gaps at %s' % (med, gaps), flush=True)
        vals = sorted(set(par))
        if len(vals) != 2:
            print('parity does not alternate between two values (%s) - nothing to correlate' % vals)
        else:
            bits = [1 if p == vals[1] else 0 for p in par]
            byp = collections.defaultdict(list)
            for b, d in zip(bits[1:], dts):
                byp[b].append(d)
            for b in (0, 1):
                if byp[b]:
                    print('field parity %d: TS1 delta to it min %d max %d mean %.1f' % (
                        b, min(byp[b]), max(byp[b]), sum(byp[b]) / len(byp[b])), flush=True)
            hits = []
            for o in regs:
                for bit in range(32):
                    v = [(s[o] >> bit) & 1 for s in snaps]
                    if len(set(v)) < 2:
                        continue
                    agree = sum(a == b for a, b in zip(v, bits))
                    score = max(agree, n - agree) / n
                    if score >= 0.9:
                        hits.append((score, o, bit, agree >= n - agree))
            hits.sort(reverse=True)
            print('register bits following the parity (>= 90 %%): %s' % (
                ', '.join('0x%03x bit %d %s %.0f%%' % (o, b, 'same' if s else 'inverted', 100 * sc)
                          for sc, o, b, s in hits[:20]) or 'none'), flush=True)
        for o, name in WATCH:
            print('%-16s %s' % (name, ' '.join('%x' % s[o] for s in snaps[:24])), flush=True)
        for r, v in NTSC.items():
            g.wr(r, v)
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
