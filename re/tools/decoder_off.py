#!/usr/bin/env python3
"""Is the analog decoder (ADV7181, I2C 0x42) fighting the HDMI receiver on the pixel bus?

csc_sweep/csc_timing: luma perfect, chroma bits wrong + noisy under every AD9398 timing setting.
The decoder is powered up and drives its outputs (dump: 0x0f PwrManagement = 00, 0x03 = 0c).
Windows, when HDMI is selected (MVKAVCIO FUN_000493d4 → FUN_0002a798(dec, 0)), writes
PwrManagement |= 0x22 (bit5 = power down).
Steps: known-colour check (csc_timing PATTERNS) with the decoder as found, then with
0x0f |= 0x22; then the mode table is reloaded and one camera frame is saved
(notes/csc-frames/decoder-off.raw) with its chroma smoothness score.
The decoder is left powered down (the Windows state for HDMI input).
Run as root, mxo2 unloaded, HDMI source on:
    sudo python3 -I decoder_off.py [ad9398_mode] [shadow_log]
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from csc_sweep import COEFF_MSB, set_coeff  # noqa: E402
from csc_test import grab  # noqa: E402
from csc_timing import PATTERNS, measure  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_CTL  # noqa: E402

DEC = 0x42
PWR = 0x0f
MEM_TARGET = 0x028


def report(label, res):
    ok, bad, stable = res
    print('%-14s %d/%d exact, %3.0f %% stable, wrong bits: %s' % (
        label, ok, len(PATTERNS), stable * 100,
        ' '.join('%d.%d' % (b // 8, b % 8) for b in range(32) if bad >> b & 1) or '-'), flush=True)


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    bus = None
    try:
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
        g.wr(MEM_TARGET, 0)
        ctl = shadow[WDMA_CTL][0]
        pwr = bus.read(DEC, PWR)[0]
        print('AD9398 STATUS1=%02x STATUS3=%02x | ADV7181 0x0f=%02x 0x03=%02x' % (
            bus.read(RX, 0x2f)[0], bus.read(RX, 0x81)[0], pwr, bus.read(DEC, 0x03)[0]), flush=True)

        for name in COEFF_MSB:
            set_coeff(bus, name, 0)
        bus.write(RX, 0x34, bus.read(RX, 0x34)[0] | 2)          # CSC on = colour generator
        report('decoder as is', measure(g, bus, bar, ctl))

        bus.write(DEC, PWR, pwr | 0x22)
        time.sleep(0.1)
        print('ADV7181 0x0f now %02x' % bus.read(DEC, PWR)[0], flush=True)
        report('decoder off', measure(g, bus, bar, ctl))

        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        time.sleep(0.3)
        print('camera frame, decoder off (plain score should now be the low one):', flush=True)
        os.makedirs(os.path.join(HERE, '..', 'notes', 'csc-frames'), exist_ok=True)
        grab(g, bar, ctl, 'decoder-off')
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        if bus:
            load_mode(bus, 0)
            load_mode(bus, rx_mode)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
