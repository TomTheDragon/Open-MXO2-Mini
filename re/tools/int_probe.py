#!/usr/bin/env python3
"""Which InterruptStatus0 bits fire per captured frame / per PCI DMA, and how does clearing work?

The status register latches events even with InterruptEnable0 = 0, so this needs no IRQ handler.
Windows: valid mask 0xf0ddd0b3, enable written per event bit, InterruptClear0 pulsed (0xffffffff then 0).
Phases (1 s each, status sampled every ~1 ms and cleared after every sample):
  idle       input selected, no WDMA
  capture    WDMA re-armed at every VideoWDmaTimeStamp1 change (like the driver)
  dma        one PCI DMA of on-board slot 0 into huge pages (as pcidma_test.py)
Per bit: how many samples showed it set, and for 'capture' how that compares to the frame count.
Bits still set right after a clear are reported as level / not clearable.
Clear test: with several bits latched, write one bit to InterruptClear0 (then 0) and see what clears.
Run as root, mxo2 unloaded, HDMI source on, huge pages reserved (run-int-probe.sh does that):
    sudo python3 -I int_probe.py [ad9398_mode] [shadow_log]
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from pcidma_test import (DMA_CMD, DMA_CMDCNT, DMA_FRAME, DMA_PITCH, DMA_SGCNT, DMA_SGHI,  # noqa: E402
                         DMA_SGLO, build_sg, huge_map)
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_TS1  # noqa: E402

INT_STATUS, INT_ENABLE, INT_CLEAR = 0x080, 0x084, 0x088
WIN_MASK = 0xf0ddd0b3


def clear(g, bits=0xffffffff):
    g.wr(INT_CLEAR, bits)
    g.wr(INT_CLEAR, 0)


def bits(v):
    return ' '.join(str(b) for b in range(32) if v >> b & 1) or '-'


def sample(g, secs, step=None):
    """Sample+clear for `secs`; step(ts_changed) runs every loop. Returns (counts, sticky, samples)."""
    counts = [0] * 32
    sticky = 0xffffffff
    n = 0
    ts = g.rd(WDMA_TS1)
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        v = g.rd(INT_STATUS)
        clear(g)
        sticky &= g.rd(INT_STATUS)
        for b in range(32):
            if v >> b & 1:
                counts[b] += 1
        n += 1
        new_ts = g.rd(WDMA_TS1)
        if step:
            step(new_ts != ts)
        ts = new_ts
        time.sleep(0.001)
    return counts, sticky, n


def report(name, counts, sticky, n, frames=None):
    print('== %s: %d samples%s, still set right after clear: %s' % (
        name, n, ', %d frames' % frames if frames is not None else '', bits(sticky)), flush=True)
    for b in range(32):
        if counts[b]:
            print('   bit %2d %s  set in %4d samples%s' % (
                b, 'W' if WIN_MASK >> b & 1 else ' ', counts[b],
                '  (= %.2f per frame)' % (counts[b] / frames) if frames else ''), flush=True)


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
    sg = huge_map(1)
    frame_mm = huge_map(2)
    g = Gaia(write=True)
    cmd_before = g._cmd()
    try:
        entries, sg_phys = build_sg(sg, frame_mm)
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        g.wr(VIN_CONTROL, 0xd02)
        for off in SYSTEM + INPUT:
            g.wr(off, shadow[off][0])
        g.wr(WDMA_CTL, 0)
        g.wr(INT_ENABLE, 0)
        ctl = shadow[WDMA_CTL][0]
        print('AD9398 STATUS1=%02x STATUS3=%02x; W = bit in Windows valid mask %08x' % (
            bus.read(RX, 0x2f)[0], bus.read(RX, 0x81)[0], WIN_MASK), flush=True)
        time.sleep(0.2)
        clear(g)
        report('idle', *sample(g, 1.0))

        frames = [0]
        slot = [0]

        def rearm(changed):
            if changed:
                frames[0] += 1
                slot[0] = (slot[0] + 1) % 3
                g.wr(WDMA_ADDR, slot[0] * 1920 * 1080 * 2)
                g.wr(WDMA_CTL, ctl)
        g.wr(WDMA_ADDR, 0)
        g.wr(WDMA_CTL, ctl)
        counts, sticky, n = sample(g, 1.0, rearm)
        report('capture', counts, sticky, n, frames[0])
        g.wr(WDMA_CTL, 0)
        time.sleep(0.1)

        # one PCI DMA
        g._set_cmd(cmd_before | 0x6)
        clear(g)
        before = g.rd(INT_STATUS)
        cnt = g.rd(DMA_CMDCNT)
        g.wr(DMA_FRAME, 0)
        g.wr(DMA_SGLO, sg_phys & 0xffffffff)
        g.wr(DMA_SGHI, sg_phys >> 32)
        g.wr(DMA_SGCNT, len(entries) * 3)
        g.wr(DMA_PITCH, 0x3c0)
        g.wr(DMA_CMD, 0x10000000)
        g.wr(DMA_CMD, 0x90000000)
        t0 = time.monotonic()
        while g.rd(DMA_CMDCNT) == cnt and time.monotonic() - t0 < 0.2:
            pass
        time.sleep(0.005)
        after = g.rd(INT_STATUS)
        print('== dma: status before %08x, after %08x → new bits %s (%.1f ms)' % (
            before, after, bits(after & ~before), (time.monotonic() - t0) * 1000), flush=True)

        # clear semantics: clear only bit 20 (if set), see what remains
        if after & (1 << 20):
            g.wr(INT_CLEAR, 1 << 20)
            g.wr(INT_CLEAR, 0)
            v = g.rd(INT_STATUS)
            print('== clear test: wrote bit 20 only → status %08x (bit 20 %s, other bits %s)' % (
                v, 'cleared' if not v >> 20 & 1 else 'STILL SET',
                'kept' if (v | 1 << 20) & after == after else 'also cleared'), flush=True)
            clear(g)
            print('   full clear → %08x' % g.rd(INT_STATUS), flush=True)
        # bit-without-pulse test: write bit 20 and leave it, then another DMA
        clear(g)
        print('== status after everything %08x' % g.rd(INT_STATUS), flush=True)
    finally:
        g.wr(DMA_CMD, 0)
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        g.wr(INT_ENABLE, 0)
        time.sleep(0.05)
        g._set_cmd(cmd_before)
        g.close()


if __name__ == '__main__':
    main()
