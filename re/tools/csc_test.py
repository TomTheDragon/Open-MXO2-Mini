#!/usr/bin/env python3
"""Does the AD9398 colour converter setting fix the mangled chroma? Captures one frame per variant.

MVKAVCIO FUN_00014c10 (re-run by a monitor loop on Windows): if STATUS REG 3 (0x81) bits 6:5
(AVI colour space: 0 RGB, 1 YCbCr 4:2:2, 2 YCbCr 4:4:4) are 0, set GENERAL SETUP 2 (0x34) bit 1,
else clear it; then FUN_00014ce4 pulses GENERAL SETUP 3 (0x59) bits 1,2. Our load_mode() does this
only once at load time, maybe before the InfoFrame arrived.
Variants: as loaded / driver logic now / 0x34 bit1 = 1 / 0x34 bit1 = 0. For each: AD9398 status +
output/CSC registers, capture a few frames into on-board 0, read it through BAR1 window 0
(notes/csc-frames/<variant>.raw) and print a chroma smoothness score (mean |difference| between
neighbouring chroma samples; plain YUYV vs. the "rotl1^0x80" fix – lower = more plausible).
Run as root, mxo2 module unloaded, HDMI camera on:
    sudo python3 -I csc_test.py [ad9398_mode] [shadow_log]
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
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_TS1  # noqa: E402

MEM_TARGET = 0x028
FRAME = 1920 * 1080 * 2
OUT = os.path.join(HERE, '..', 'notes', 'csc-frames')
FIX = bytes((((b << 1) | (b >> 7)) & 0xff) ^ 0x80 for b in range(256))
SHOW = [0x24, 0x25, 0x26, 0x27, 0x34, 0x35, 0x59, 0x5b, 0x2f, 0x81, 0x82, 0x84]


def regs(bus):
    return ' '.join('%02x=%02x' % (r, bus.read(RX, r)[0]) for r in SHOW)


def chroma_score(data, fix):
    t = n = 0
    for y in range(20, 470, 15):     # first 1.8 MB: inside the picture for 720p and 1080p
        row = data[y * 3840:(y + 1) * 3840]
        for lane in (1, 3):
            c = row[lane::4]
            if fix:
                c = c.translate(FIX)
            t += sum(abs(a - b) for a, b in zip(c, c[1:]))
            n += len(c) - 1
    return t / n


def grab(g, bar, ctl, name):
    ts0 = g.rd(WDMA_TS1)
    for _ in range(4):
        g.wr(WDMA_ADDR, 0)
        g.wr(WDMA_CTL, ctl)
        time.sleep(1 / 30)
    time.sleep(0.05)
    frames = g.rd(WDMA_TS1) != ts0
    data = array.array('I', (bar[i] for i in range(FRAME // 4))).tobytes()  # 32-bit reads only
    with open(os.path.join(OUT, name + '.raw'), 'wb') as f:
        f.write(data)
    print('   frame %s, chroma score plain %.1f / fixed %.1f, luma mean %.0f' % (
        'captured' if frames else 'NOT captured (old data)', chroma_score(data, False),
        chroma_score(data, True), sum(data[0::2][::97]) / len(data[0::2][::97])), flush=True)


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
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
        st3_at_load = bus.read(RX, 0x81)[0]
        load_mode(bus, rx_mode)
        print('STATUS3 at load time %02x' % st3_at_load, flush=True)
        g.wr(VIN_CONTROL, 0xd02)
        for off in SYSTEM + INPUT:
            g.wr(off, shadow[off][0])
        g.wr(WDMA_CTL, 0)
        g.wr(MEM_TARGET, 0)
        ctl = shadow[WDMA_CTL][0]
        time.sleep(0.5)

        def reset_pulse():
            gs3 = bus.read(RX, 0x59)[0]
            bus.write(RX, 0x59, gs3 | 6)
            bus.write(RX, 0x59, gs3 & ~6 & 0xff)

        def driver_logic():
            st3, gs2 = bus.read(RX, 0x81)[0], bus.read(RX, 0x34)[0]
            bus.write(RX, 0x34, gs2 | 2 if (st3 & 0x60) == 0 else gs2 & ~2 & 0xff)
            reset_pulse()

        def force(bit):
            def f():
                gs2 = bus.read(RX, 0x34)[0]
                bus.write(RX, 0x34, gs2 | 2 if bit else gs2 & ~2 & 0xff)
                reset_pulse()
            return f

        for name, action in (('as-loaded', None), ('driver-logic', driver_logic),
                             ('gs2-bit1-on', force(1)), ('gs2-bit1-off', force(0))):
            if action:
                action()
                time.sleep(0.5)
            st3 = bus.read(RX, 0x81)[0]
            print('== %-13s %s  (AVI colour space %d: 0 RGB, 1 YCbCr422, 2 YCbCr444)' % (
                name, regs(bus), (st3 >> 5) & 3), flush=True)
            grab(g, bar, ctl, name)
    finally:
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
