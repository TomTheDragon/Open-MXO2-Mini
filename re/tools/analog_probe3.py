#!/usr/bin/env python3
"""Analog probe 3: do the FPGA's general-purpose outputs gate the decoder -> FPGA path?

Probes 1/2: ADV7181 locks on composite (input 1), but no FPGA capture with any VinControl code or
VinControlAnalog high bits. Windows' BAR0 dumps showed GpIO (0x020) = 1 while we leave it 0; GpOEnable
0x7f makes pins 0..6 outputs (MVKAVBUS FUN_00029f90). The input select also calls an unresolved board
method vt+0x230(4, 1) for analog inputs.
For GpIO in (0, 1, each single bit, 0x7f): decoder lock, AD9398 still answering, and WDMA captures per
second with VinControl 0x0d00 (analog) and VinControlAnalog 0xc0000000 / 0; first capture -> woven frame3.
GpIO is restored to 0 at the end.
Run as root, mxo2 unloaded, composite source on:  sudo python3 -I analog_probe3.py [decoder_mode]
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from analog_probe import INT_STATUS0, OUT, VIN_CONTROL_ANALOG, dec_status, load_decoder  # noqa: E402
from analog_probe2 import measure  # noqa: E402
from gaia import GPIO, Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, read, wait_ts  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402


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
        g.wr(VIN_CONTROL, 0x0d00)
        found = None
        print('GpIO  readback  AD9398 rev  decoder                         captures/s (VinCtlAnalog c0000000 / 0)',
              flush=True)
        for gp in (0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x7f):
            g.wr(GPIO, gp)
            time.sleep(0.3)
            rev = bus.read(RX, 0x00)[0]
            lock = dec_status(bus)[3].split('|')[0].strip()
            res = []
            for vca in (0xc0000000, 0x00000000):
                g.wr(VIN_CONTROL_ANALOG, vca)
                time.sleep(0.1)
                n, _, _ = measure(g, 0.6)
                res.append(n)
                if n and found is None:
                    found = (gp, vca)
            print('0x%02x  %08x  %02x          %-31s %d / %d' % (gp, g.rd(GPIO), rev, lock, res[0], res[1]),
                  flush=True)
        if found is None:
            print('nothing captured', flush=True)
            return
        gp, vca = found
        g.wr(GPIO, gp)
        g.wr(VIN_CONTROL_ANALOG, vca)
        g.wr(WDMA_CTL2, 0x41 | (1440 // 4) << 16)
        time.sleep(0.3)
        ts = g.rd(WDMA_TS1)
        for _ in range(7):
            top = bool(g.rd(INT_STATUS0) >> 24 & 1)
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, 0x80000000 | (0 if top else 1) << 24)
            ts = wait_ts(g, ts)
            if ts is None:
                break
        g.wr(WDMA_CTL, 0)
        time.sleep(0.03)
        with open(os.path.join(OUT, 'frame3.raw'), 'wb') as f:
            f.write(read(bar, 0, 1440 * lines))
        print('saved notes/analog/frame3.raw (720x%d) with GpIO 0x%02x, VinControlAnalog %08x' % (lines, gp, vca),
              flush=True)
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(WDMA_CTL2, 0x41)
        g.wr(GPIO, 0)
        g.wr(VIN_CONTROL, 0xd00)
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
