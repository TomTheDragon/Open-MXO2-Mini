#!/usr/bin/env python3
"""Interlace probe 2: line pitch for hardware weaving, and field parity.

Probe 1: with a 1080i input every WDMA arm captures ONE field (540 lines, packed, pitch 3840) for
p5 = 0/1/2 and VinControl code 0x0d or 0x05; code 0x03 captures nothing; BAR1 is not writable.
VideoWDmaControl2 bits 26:16 = line pitch / 4 (FUN_00014a14; Windows used 0 = packed).
Test 1 (pitch): capture a field packed into slot 0, then one with pitch 7680 (two lines). Rows changed by
  the second capture = rows it wrote: 0,2,4..1078 if the pitch works (camera noise changes every row).
Test 2 (parity): capture 6 consecutive fields packed into 6 areas of 2 MiB; per field record
  VideoWDmaStatus, TS1, TS2, InterruptStatus0 and AD9398 STATUS2; then decide for each pair of
  consecutive fields whether the second lies half a line BELOW (= it is the bottom field, the first the top)
  or ABOVE, by matching rows against the average of the neighbouring rows of the other field.
Run as root, mxo2 unloaded, 1080i source (static scene with fine horizontal detail is best):
    sudo python3 -I interlace_probe2.py [ad9398_mode]
"""
import array
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402

MEM_TARGET = 0x028
WDMA_STATUS, WDMA_TS2, INT_STATUS0 = 0x910, 0x918, 0x080
ROW = 3840
FIELD_ROWS = 540
CTL = 0x82000000
AREA = 0x200000
OUT = os.path.join(HERE, '..', 'notes', 'interlace')


def wait_ts(g, ts, timeout=0.5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = g.rd(WDMA_TS1)
        if v != ts:
            return v
        time.sleep(0.0003)
    return None


def read(bar, start, nbytes, g=None):
    """Read card memory at `start`; switches the 8 MiB BAR1 window (MemoryTargetControl) if needed."""
    win = start >> 23
    if g is not None:
        g.wr(MEM_TARGET, win)
    off = start - (win << 23)
    assert off + nbytes <= 8 << 20, 'read crosses an 8 MiB window'
    data = array.array('I', (bar[(off + i) // 4] for i in range(0, nbytes, 4))).tobytes()
    if g is not None:
        g.wr(MEM_TARGET, 0)
    return data


def luma_rows(raw, nrows, step=4):
    return [raw[i * ROW:(i + 1) * ROW:2][::step] for i in range(nrows)]


def rowdiff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def below_score(f_a, f_b):
    """Mean error of field b's rows predicted as lying half a line below / above field a's rows."""
    below = above = 0.0
    n = 0
    for i in range(20, FIELD_ROWS - 20, 3):
        mid_below = [(x + y) / 2 for x, y in zip(f_a[i], f_a[i + 1])]
        mid_above = [(x + y) / 2 for x, y in zip(f_a[i - 1], f_a[i])]
        below += rowdiff(f_b[i], mid_below)
        above += rowdiff(f_b[i], mid_above)
        n += 1
    return below / n, above / n


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt'))
    os.makedirs(OUT, exist_ok=True)
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        for off in SYSTEM + INPUT:
            g.wr(off, shadow[off][0])
        g.wr(VIN_CONTROL, 0x0d02)
        g.wr(WDMA_CTL, 0)
        g.wr(MEM_TARGET, 0)
        time.sleep(0.3)
        print('AD9398 VIC %d, STATUS1 %02x' % (bus.read(RX, 0x84)[0], bus.read(RX, 0x2f)[0]), flush=True)

        # ---- test 1: pitch, for every scan mode p5
        print('# test 1: line pitch 7680 (two lines) per scan mode p5', flush=True)
        for p5 in range(4):
            ctl = 0x80000000 | p5 << 24
            g.wr(WDMA_CTL2, 0x41)
            ts = g.rd(WDMA_TS1)
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, ctl)
            ts = wait_ts(g, ts)
            time.sleep(0.03)                    # field done
            before = read(bar, 0, ROW * 1080)
            g.wr(WDMA_CTL2, 0x41 | (2 * ROW // 4) << 16)
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, ctl)
            ok = wait_ts(g, ts) is not None
            time.sleep(0.03)
            after = read(bar, 0, ROW * 1080)
            changed = [i for i in range(1080) if before[i * ROW:(i + 1) * ROW] != after[i * ROW:(i + 1) * ROW]]
            even = sum(1 for i in changed if i % 2 == 0)
            print('  p5=%d: %s, %d rows changed, %d even / %d odd, first %s last %s' % (
                p5, 'captured' if ok else 'NO capture', len(changed), even, len(changed) - even,
                changed[:6], changed[-3:]), flush=True)
            with open(os.path.join(OUT, 'pitch7680-p%d.raw' % p5), 'wb') as f:
                f.write(after)
        g.wr(WDMA_CTL2, 0x41)

        # ---- test 2: parity
        print('# test 2: 6 consecutive fields', flush=True)
        g.wr(WDMA_CTL2, 0x41)
        info = []
        ts = g.rd(WDMA_TS1)
        g.wr(WDMA_ADDR, 0)
        g.wr(WDMA_CTL, CTL)
        for k in range(6):
            ts = wait_ts(g, ts)
            st2 = bus.read(RX, 0x30)[0]
            info.append((ts, g.rd(WDMA_TS2), g.rd(WDMA_STATUS), g.rd(INT_STATUS0), st2))
            if k < 5:
                g.wr(WDMA_ADDR, (k + 1) * AREA)
                g.wr(WDMA_CTL, CTL)
        time.sleep(0.03)
        fields = [luma_rows(read(bar, k * AREA, ROW * FIELD_ROWS, g), FIELD_ROWS) for k in range(6)]
        for k, (t1, t2, st, i0, s2) in enumerate(info):
            print('  field %d started: TS1 %08x TS2 %08x WDmaStatus %08x Int0 %08x AD9398 STATUS2 %02x' % (
                k, t1 or 0, t2, st, i0, s2), flush=True)
        print('  NOTE: field k is the capture armed before TS change k+1 (armed at change k)', flush=True)
        for k in range(5):
            b, a = below_score(fields[k], fields[k + 1])
            print('  field %d -> %d: error if next is half a line below %.2f, above %.2f -> next is %s' % (
                k, k + 1, b, a, 'BELOW (bottom)' if b < a else 'ABOVE (top)'), flush=True)
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
