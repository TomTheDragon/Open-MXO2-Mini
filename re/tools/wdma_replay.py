#!/usr/bin/env python3
"""Replay the Windows driver's register values (kd shadow log) and see whether the video WDMA starts.

Values come from a mxo2-regs log (default notes/win/2-preview.txt, 1080p30 HDMI preview).
Stages, each followed by a 1 s watch of VideoWDmaStatus, VideoWDmaTimeStamp1/2, Video2Crc16,
InterruptStatus0, VinStatusHDMI and the SystemClock rate:
  0 baseline   Gaia defaults, AD9398 mode, VinControl 0xd02 (what earlier tests did)
  1 system     GpOEnable, MemoryTargetControl, CodecFlashAddr, genlock, PLL, system clock
  2 input      VinControl*, VideoWDmaControl2 / Address / Control (Control last)
  3 ring       step VideoWDmaAddress through 8 frame slots (k * 0x3f4800) once per frame
  4 ring+ctl   same, rewriting VideoWDmaControl after each address
Not touched: InterruptEnable0 (no IRQ handler on Linux), PCI DMA, LQ scaler, audio, outputs.
Run as root, mxo2 module unloaded, HDMI source on:
    sudo python3 -I wdma_replay.py [ad9398_mode] [shadow_log]
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import load_mode  # noqa: E402
from gaia import INT_STATUS0, VIN_STATUS_HDMI, Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402

INT_CLEAR0 = 0x088
VIDEO2_CRC = 0x064
SYSTEM_CLOCK = 0x188
VIN_CONTROL = 0x780
WDMA_CTL, WDMA_CTL2, WDMA_ADDR = 0x580, 0x584, 0x588
WDMA_STATUS, WDMA_TS1, WDMA_TS2 = 0x910, 0x914, 0x918
FRAME = 1920 * 1080 * 2              # 0x3f4800, ring slot size seen in the Windows shadows

SYSTEM = [0x024, 0x028, 0x074, 0x03c, 0x038,             # GpOEnable, MTC, CodecFlashAddr, Vcxo, AudioPll
          0x150, 0x154,                                  # PllControl, PllControl2
          0x104, 0x108, 0x10c, 0x100,                    # GenlockPreload, FWControl, SourceControl, Control
          0x190, 0x184, 0x18c]                           # SystemClockReload, ClockLoadCount, Control
INPUT = [0x784, 0x78c, 0x790, VIN_CONTROL, WDMA_CTL2, WDMA_ADDR, WDMA_CTL]
WATCHED = [('WDmaStatus', WDMA_STATUS), ('TS1', WDMA_TS1), ('TS2', WDMA_TS2),
           ('V2Crc', VIDEO2_CRC), ('VinHDMI', VIN_STATUS_HDMI)]


def fmt(vals):
    s = sorted(set(vals))
    return '%08x' % s[0] if len(s) == 1 else '%d values %08x..%08x' % (len(s), s[0], s[-1])


def watch(g, label, secs=1.0, step=None):
    """Sample the status registers; `step(i)` is called once per 1/30 s (ring stepping)."""
    g.wr(INT_CLEAR0, 0xffffffff)
    g.wr(INT_CLEAR0, 0)
    first_int = g.rd(INT_STATUS0)
    int_or = 0
    seen = {n: [] for n, _ in WATCHED}
    c0, t0 = g.rd(SYSTEM_CLOCK), time.monotonic()
    nxt, i = t0, 0
    while time.monotonic() - t0 < secs:
        if step and time.monotonic() >= nxt:
            step(i)
            i += 1
            nxt += 1 / 30
        int_or |= g.rd(INT_STATUS0)
        for n, off in WATCHED:
            seen[n].append(g.rd(off))
        time.sleep(0.002)
    c1, dt = g.rd(SYSTEM_CLOCK), time.monotonic() - t0
    live = [n for n, _ in WATCHED[:4] if len(set(seen[n])) > 1]
    print('%-9s Int0 %08x OR %08x | SysClk %.0f/s | %s' % (
        label, first_int, int_or, ((c1 - c0) & 0xffffffff) / dt,
        ' | '.join('%s %s' % (n, fmt(seen[n])) for n, _ in WATCHED)), flush=True)
    print('          → %s' % ('LIVE: ' + ', '.join(live) if live else 'nothing moves'), flush=True)


def apply(g, shadow, offs, label):
    for off in offs:
        if off in shadow:
            g.wr(off, shadow[off][0])
    print('-- %s: %s' % (label, ' '.join('%03x=%08x' % (o, shadow[o][0]) for o in offs if o in shadow)),
          flush=True)


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
    if len(shadow) < 100:
        sys.exit('only %d registers in %s' % (len(shadow), log))
    print('shadow log %s (%d registers), AD9398 mode %d' % (log, len(shadow), rx_mode))
    g = Gaia(write=True)
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        g.wr(VIN_CONTROL, 0xd02)
        time.sleep(0.3)
        print('AD9398 STATUS1=%02x (67 = HDMI locked)' % bus.read(0x98, 0x2f)[0], flush=True)
        watch(g, '0 base')

        apply(g, shadow, SYSTEM, 'system')
        time.sleep(0.2)
        watch(g, '1 system')

        g.wr(WDMA_CTL, 0)
        apply(g, shadow, INPUT, 'input')
        time.sleep(0.1)
        watch(g, '2 input')

        ctl = shadow[WDMA_CTL][0]
        watch(g, '3 ring', step=lambda i: g.wr(WDMA_ADDR, (i % 8) * FRAME))

        def ring_ctl(i):
            g.wr(WDMA_ADDR, (i % 8) * FRAME)
            g.wr(WDMA_CTL, ctl)
        watch(g, '4 ring+c', step=ring_ctl)
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        g.close()


if __name__ == '__main__':
    main()
