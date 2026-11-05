#!/usr/bin/env python3
"""Analog probe 4: replay the Windows composite-capture register values (notes/win/analog).

The Windows analog run (notes/win-regshadow-analog.txt, NTSC composite) differs from what probes 1-3 did:
  VideoWDmaControl2 0x01680101 (bit 8, width 360 dwords = 720 px) - we used 0x41 (bit 6, the HDMI value);
  VideoWDmaControl  0x80000000 / 0x81000000 (bit 25 clear, bit 24 toggles) - we used 0x82000000;
  GenlockControl 0x1000, SystemClockReload 0x8f48, VinControl 0x0d00, VinControlAnalog 0xc0000000.
Guess: Control2 bit 6 / bit 8 picks the WDMA source (HDMI / analog).
Sweep Control2 x Control -> captures/s; first combo that captures: two fields (Control bit 24 = 0 / 1) at
pitch 1440 -> notes/analog/field4-{0,1}.raw, and a weave at pitch 2880 -> notes/analog/frame4.raw.
Run as root, mxo2 unloaded, composite source on:  sudo python3 -I analog_probe4.py [decoder_mode]
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from analog_probe import INT_STATUS0, OUT, VIN_CONTROL_ANALOG, dec_status, load_decoder  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, read  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_STATUS, WDMA_TS1  # noqa: E402

INT_CLEAR0 = 0x088
W = 720
CTL2S = [0x01680101, 0x00000101, 0x01680141, 0x01680041, 0x00000041, 0x01680100, 0x01680001]
CTLS = [0x80000000, 0x82000000]


def measure(g, ctl, secs=0.6):
    """WDMA captures/s (TimeStamp1 changes, re-armed each time), Int0 bit counts, WDMA status values."""
    counts = [0] * 32
    st = set()
    n = 0
    ts = g.rd(WDMA_TS1)
    g.wr(WDMA_ADDR, 0)
    g.wr(WDMA_CTL, ctl)
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        v = g.rd(INT_STATUS0)
        g.wr(INT_CLEAR0, 0xffffffff)
        g.wr(INT_CLEAR0, 0)
        for b in range(32):
            counts[b] += v >> b & 1
        st.add(g.rd(WDMA_STATUS))
        t = g.rd(WDMA_TS1)
        if t != ts:
            ts, n = t, n + 1
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, ctl)
        time.sleep(0.001)
    g.wr(WDMA_CTL, 0)
    bits = ' '.join('%d:%d' % (b, c) for b, c in enumerate(counts) if c and b != 28)
    return n / secs, bits, ' '.join('%x' % s for s in sorted(st)[:4])


def grab(g, ctl, ts, timeout=0.5):
    """Arm one capture at address 0 and wait for TimeStamp1 to change; returns the new stamp or None."""
    g.wr(WDMA_ADDR, 0)
    g.wr(WDMA_CTL, ctl)
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        t = g.rd(WDMA_TS1)
        if t != ts:
            return t
        time.sleep(0.0005)
    return None


def main():
    mode = int(sys.argv[1]) if len(sys.argv) > 1 else 0
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
        print('system regs from the Windows analog preview: GenlockControl %08x SystemClockReload %08x'
              % (shadow[0x100][0], shadow[0x190][0]), flush=True)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        bus.write(RX, 0x26, bus.read(RX, 0x26)[0] | 1)      # AD9398 power down, as Windows for analog
        load_decoder(bus, mode)
        time.sleep(0.6)
        print('decoder mode %d:%s' % (mode, dec_status(bus)[3]), flush=True)
        lines = 576 if mode in (3, 4, 5) else 480
        g.wr(MEM_TARGET, 0)
        g.wr(VIN_CONTROL, 0x0d00)
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        found = None
        print('Control2   Control    captures/s  WDMA status  Int0 bits (count, not 28)', flush=True)
        for c2 in CTL2S:
            g.wr(WDMA_CTL2, c2)
            for ctl in CTLS:
                time.sleep(0.05)
                rate, bits, st = measure(g, ctl)
                print('%08x   %08x   %6.1f      %-12s %s' % (c2, ctl, rate, st, bits), flush=True)
                if rate and found is None:
                    found = (c2, ctl)
        if found is None:
            print('nothing captured', flush=True)
            return
        c2, ctl = found
        print('using Control2 %08x Control %08x' % found, flush=True)
        # Fields: one line pitch (Windows width), Control bit 24 = 0 / 1.
        g.wr(WDMA_CTL2, (c2 & 0xffff) | (W * 2 // 4) << 16)
        for p5 in (0, 1):
            ts = g.rd(WDMA_TS1)
            for _ in range(3):
                ts = grab(g, (ctl & ~0x01000000) | p5 << 24, ts)
                if ts is None:
                    break
            g.wr(WDMA_CTL, 0)
            time.sleep(0.03)
            name = 'field4-%d.raw' % p5
            with open(os.path.join(OUT, name), 'wb') as f:
                f.write(read(bar, 0, W * 2 * lines))
            print('saved notes/analog/%s (%dx%d, pitch 1440, %s)' % (name, W, lines, 'ok' if ts else 'timeout'),
                  flush=True)
        # Weave: pitch two lines, field from Int0 bit 24 as for HDMI 1080i.
        g.wr(WDMA_CTL2, (c2 & 0xffff) | (W * 4 // 4) << 16)
        ts = g.rd(WDMA_TS1)
        for _ in range(7):
            top = bool(g.rd(INT_STATUS0) >> 24 & 1)
            ts = grab(g, 0x80000000 | (0 if top else 1) << 24, ts)
            if ts is None:
                break
        g.wr(WDMA_CTL, 0)
        time.sleep(0.03)
        with open(os.path.join(OUT, 'frame4.raw'), 'wb') as f:
            f.write(read(bar, 0, W * 2 * lines))
        print('saved notes/analog/frame4.raw (%dx%d, pitch 2880 weave, %s)' % (W, lines, 'ok' if ts else 'timeout'),
              flush=True)
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(WDMA_CTL2, 0x41)
        g.wr(VIN_CONTROL, 0xd00)
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
