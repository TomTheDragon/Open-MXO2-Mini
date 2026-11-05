#!/usr/bin/env python3
"""Analog probe 2: which FPGA settings let the decoder's video reach the capture DMA?

Probe 1: ADV7181 composite = input 1 (mode 0 NTSC / 3 PAL) locks; FPGA analog input (VinControl bits 1:0 = 0,
VinControlAnalog 0xc0000000 = reset value) captured nothing with codes 0x0d / 0x11 / 0x10. VinStatusAnalog
read 0x898 (2200) or 0; Windows idle read 0x35a (858 = NTSC line).
Windows (MVKAVBUS FUN_0002bf54) only touches VinControl bits 1:0 and VinControlAnalog bit 16. Untested: the
VinControlAnalog high bits (31/30 may be disable/reset) and other mode codes.
For VinControl code in (0x12 480p, 0x10 480i, 0x0d, 0x00) x VinControlAnalog in (0x00000000, 0x40000000,
0x80000000, 0xc0000000): VinStatusAnalog samples, which InterruptStatus0 bits get set how often (cleared per
sample), and WDMA captures per second. First combination that captures: woven frame -> notes/analog/frame2.raw.
Run as root, mxo2 unloaded, composite source on:  sudo python3 -I analog_probe2.py [decoder_mode]
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from analog_probe import (DEC, INT_STATUS0, OUT, VIN_CONTROL_ANALOG, VIN_STATUS_ANALOG, dec_status,  # noqa: E402
                          load_decoder)
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, read, wait_ts  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402

INT_CLEAR0 = 0x088


def measure(g, secs=1.0):
    """WDMA captures (re-armed, packed fields), VinStatusAnalog values, Int0 bit counts."""
    counts = [0] * 32
    vsa = set()
    n = 0
    ts = g.rd(WDMA_TS1)
    g.wr(WDMA_ADDR, 0)
    g.wr(WDMA_CTL, 0x82000000)
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        v = g.rd(INT_STATUS0)
        g.wr(INT_CLEAR0, 0xffffffff)
        g.wr(INT_CLEAR0, 0)
        for b in range(32):
            counts[b] += v >> b & 1
        vsa.add(g.rd(VIN_STATUS_ANALOG))
        t = g.rd(WDMA_TS1)
        if t != ts:
            ts, n = t, n + 1
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, 0x82000000)
        time.sleep(0.001)
    g.wr(WDMA_CTL, 0)
    bits = ' '.join('%d:%d' % (b, c) for b, c in enumerate(counts) if c and b not in (24,))
    return n, sorted(vsa), bits


def main():
    mode = int(sys.argv[1]) if len(sys.argv) > 1 else 0
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
        load_mode(bus, 0)
        bus.write(RX, 0x26, bus.read(RX, 0x26)[0] | 1)
        load_decoder(bus, mode)
        time.sleep(0.6)
        print('decoder mode %d:%s' % (mode, dec_status(bus)[3]), flush=True)
        lines = 576 if mode in (3, 4, 5) else 480
        g.wr(MEM_TARGET, 0)
        g.wr(WDMA_CTL2, 0x41)
        found = None
        print('code  VinCtlAnalog  captures/s  VinStatusAnalog values          Int0 bits set (bit:samples, ~1000 '
              'samples/s; 24 = field id omitted)', flush=True)
        for code in (0x12, 0x10, 0x0d, 0x00):
            for vca in (0x00000000, 0x40000000, 0x80000000, 0xc0000000):
                g.wr(VIN_CONTROL_ANALOG, vca)
                g.wr(VIN_CONTROL, code << 8)
                time.sleep(0.2)
                n, vsa, bits = measure(g)
                print('0x%02x  %08x      %3d        %-32s %s' % (
                    code, vca, n, ' '.join('%x' % v for v in vsa[:6]), bits), flush=True)
                if n and found is None:
                    found = (code, vca)
        if found is None:
            print('nothing captured', flush=True)
            return
        code, vca = found
        g.wr(VIN_CONTROL_ANALOG, vca)
        g.wr(VIN_CONTROL, code << 8)
        g.wr(WDMA_CTL2, 0x41 | (1440 // 4) << 16)
        time.sleep(0.2)
        ts = g.rd(WDMA_TS1)
        for _ in range(7):
            top = bool(g.rd(INT_STATUS0) >> 24 & 1)
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, 0x80000000 | (0 if top else 1) << 24)
            ts = wait_ts(g, ts)
            if ts is None:
                print('weave: capture stopped', flush=True)
                break
        g.wr(WDMA_CTL, 0)
        time.sleep(0.03)
        with open(os.path.join(OUT, 'frame2.raw'), 'wb') as f:
            f.write(read(bar, 0, 1440 * lines))
        print('saved notes/analog/frame2.raw (720x%d) with code 0x%02x, VinControlAnalog %08x' % (
            lines, code, vca), flush=True)
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
