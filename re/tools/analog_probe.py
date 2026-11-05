#!/usr/bin/env python3
"""First analog (composite) capture probe: ADV7181 decoder + FPGA analog input.

Windows (re/NOTES.md, "Analog inputs"): analog input -> AD9398 powered down (0x26 bit0), ADV7181 powered up
(0x0f &= ~0x22), reset (0x0f |= 0x80, 20 ms), mode table, ADI hidden-register script; FPGA VinControl
bits 1:0 = 0 (analog), VinControlAnalog bit16 only for component.
Composite is input 1 or 4 (ADV7181 INSEL x5 or x6): this probe loads modes 3/4 (PAL, inputs 1/4) and 0/1
(NTSC) and reports decoder lock + detected standard for each; with the best one it then
  - arms the video WDMA (one field per arm, as for 1080i) with VinControl codes 0x0d / 0x11 / 0x10 and
    counts VideoWDmaTimeStamp1 changes per second (50 or 60 = fields arrive),
  - weaves two fields with pitch 1440 (scan mode 0/1 by InterruptStatus0 bit 24) and saves
    notes/analog/frame.raw (720 x 576 or 480 YUYV, view with tools/raw2png.py ... plain).
Run as root, mxo2 unloaded, composite source connected:
    sudo python3 -I analog_probe.py
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from adv7181_modes import MODES  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, read, wait_ts  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402

DEC = 0x42
VIN_CONTROL_ANALOG, VIN_STATUS_ANALOG, INT_STATUS0 = 0x784, 0x7c4, 0x080
HIDDEN_SD = [(0x3e, 0x64), (0x3f, 0x9e), (0x0e, 0x80), (0x52, 0x46), (0x54, 0x00), (0x7f, 0xff), (0x81, 0x30),
             (0x90, 0xc9), (0x91, 0x40), (0x92, 0x3c), (0x93, 0xca), (0x94, 0xd5), (0xb1, 0xff), (0xb6, 0x08),
             (0xc0, 0x9a), (0xcf, 0x50), (0xd6, 0xdd), (0xd7, 0xe2), (0xe5, 0x51), (0x0e, 0x00)]
STD = ['NTSC M/J', 'NTSC 4.43', 'PAL M', 'PAL 60', 'PAL B/G/H/I/D', 'SECAM', 'PAL Comb N', 'SECAM 525']
OUT = os.path.join(HERE, '..', 'notes', 'analog')


def load_decoder(bus, mode):
    pm = bus.read(DEC, 0x0f)[0]
    bus.write(DEC, 0x0f, (pm & ~0x22 & 0xff) | 0x80)       # power up + reset
    time.sleep(0.02)
    for reg, val in MODES[mode]:
        bus.write(DEC, reg, val)
    if mode in (0, 1, 3, 4):
        for reg, val in HIDDEN_SD:
            bus.write(DEC, reg, val)


def dec_status(bus):
    s1, s2, s3 = bus.read(DEC, 0x10)[0], bus.read(DEC, 0x12)[0], bus.read(DEC, 0x13)[0]
    return s1, s2, s3, ' lock %s, fsc %s, detected %s | Status1 %02x Status2 %02x Status3 %02x' % (
        'yes' if s1 & 1 else 'NO', 'yes' if s1 & 4 else 'no', STD[(s1 >> 4) & 7], s1, s2, s3)


def main():
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
        for off in SYSTEM:
            g.wr(off, shadow[off][0])
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)                                   # AD9398 as at chip creation ...
        bus.write(RX, 0x26, bus.read(RX, 0x26)[0] | 1)      # ... then powered down (FUN_00014d58(rx, 0))
        print('ADV7181 ident %02x' % bus.read(DEC, 0x11)[0], flush=True)

        best = None
        for mode, what in ((3, 'PAL input 1'), (4, 'PAL input 4'), (0, 'NTSC input 1'), (1, 'NTSC input 4')):
            load_decoder(bus, mode)
            time.sleep(0.6)
            s1, s2, s3, txt = dec_status(bus)
            print('mode %d (%s):%s' % (mode, what, txt), flush=True)
            score = (s1 & 1) * 2 + (s1 >> 2 & 1)
            if best is None or score > best[0]:
                best = (score, mode, (s1 >> 4) & 7)
        score, mode, std = best
        pal = std in (4, 5, 6) or (mode in (3, 4) and score == 0)
        lines = 576 if pal else 480
        if mode in (0, 1) and pal:
            mode += 3
        elif mode in (3, 4) and not pal:
            mode -= 3
        load_decoder(bus, mode)
        time.sleep(0.6)
        print('using mode %d (%s, %d lines):%s' % (mode, 'PAL' if pal else 'NTSC', lines, dec_status(bus)[3]),
              flush=True)

        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)                # bit16 = 0: not component
        g.wr(WDMA_CTL, 0)
        g.wr(MEM_TARGET, 0)
        good = None
        for code in (0x0d, 0x11, 0x10):
            g.wr(VIN_CONTROL, code << 8)                    # bits 1:0 = 0: analog input
            time.sleep(0.3)
            g.wr(WDMA_CTL2, 0x41)
            ts, n, t0 = g.rd(WDMA_TS1), 0, time.monotonic()
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, 0x82000000)
            while time.monotonic() - t0 < 1.0:
                v = g.rd(WDMA_TS1)
                if v != ts:
                    ts, n = v, n + 1
                    g.wr(WDMA_ADDR, 0)
                    g.wr(WDMA_CTL, 0x82000000)
                time.sleep(0.0005)
            print('VinControl %04x: VinStatusAnalog %08x, %d WDMA captures/s, Int0 %08x' % (
                code << 8, g.rd(VIN_STATUS_ANALOG), n, g.rd(INT_STATUS0)), flush=True)
            if n:
                good = code
        g.wr(WDMA_CTL, 0)

        # weave with the last code that captured
        if good is None:
            print('no VinControl code captured anything', flush=True)
            return
        code = good
        g.wr(VIN_CONTROL, code << 8)
        g.wr(WDMA_CTL2, 0x41 | (1440 // 4) << 16)
        time.sleep(0.2)
        ts = g.rd(WDMA_TS1)
        top = bool(g.rd(INT_STATUS0) >> 24 & 1)
        g.wr(WDMA_ADDR, 0)
        g.wr(WDMA_CTL, 0x80000000 | (0 if top else 1) << 24)
        for _ in range(6):
            ts = wait_ts(g, ts)
            if ts is None:
                print('weave: capture stopped', flush=True)
                break
            top = bool(g.rd(INT_STATUS0) >> 24 & 1)
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, 0x80000000 | (0 if top else 1) << 24)
        wait_ts(g, ts)
        g.wr(WDMA_CTL, 0)
        time.sleep(0.03)
        raw = read(bar, 0, 1440 * lines)
        with open(os.path.join(OUT, 'frame.raw'), 'wb') as f:
            f.write(raw)
        print('saved notes/analog/frame.raw (720x%d) with VinControl %04x' % (lines, code << 8), flush=True)
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
