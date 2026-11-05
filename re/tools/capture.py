#!/usr/bin/env python3
"""First capture attempt: HDMI → Gaia video write DMA → on-board memory → read via BAR1.

Register meaning (MVKAVBUS, see NOTES.md):
  VinControl        0x780  bits 1:0 = input select (FUN_0002bf54): 3 = off, 0 = analog,
                           2 = HDMI (input code 0x40). Reset value 0xd00 (bits 12:8 meaning unknown).
  VideoWDmaAddress  0x588  on-board address & 0x3fffffff
  VideoWDmaControl2 0x584  bits 26:16 = pitch / 4
  VideoWDmaControl  0x580  bit31 enable | b26 | b25:24 | b23 10-bit | b22:20   (FUN_00014a14)
Before: Gaia defaults + AD9398 mode tables (ad9398.py).
For each Control variant BAR1 is sampled (4 dwords per 64 KiB) before and after; slow reads abort.

Run as root with the mxo2 module unloaded and an HDMI source plugged in:
    sudo python3 -I capture.py [ad9398_mode] [vincontrol]     (defaults 10, 0xd02)
Writes ../notes/capture.txt-style output to stdout and ../notes/frame-<n>.raw (convert with yuv2ppm.py) for changed variants.
"""
import mmap
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import load_mode  # noqa: E402
from gaia import INT_STATUS0, VIN_STATUS_HDMI, Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402

VIN_CONTROL, VIN_CONTROL_HDMI = 0x780, 0x78c
WDMA_CTL, WDMA_CTL2, WDMA_ADDR, WDMA_STATUS = 0x580, 0x584, 0x588, 0x910
W, H = 1920, 1080
PITCH = W * 2                      # 8-bit 4:2:2
CHUNK = 0x10000
BAR1_SIZE = 8 << 20

# FUN_00014a14 param_7 → (uVar2 bits 22:20, uVar3 bit 26)
VARIANTS = [('p7=1/3', 6, 1), ('p7=6', 4, 1), ('p7=5', 0, 0), ('p7=0x35', 1, 0)]


class SlowBar(Exception):
    pass


def fingerprint(bar):
    """Sample 4 dwords per 64 KiB chunk (a full read of BAR1 hung the machine on 2026-11-03)."""
    words = memoryview(bar).cast('I')
    fp = []
    try:
        for o in range(0, BAR1_SIZE, CHUNK):
            t0 = time.monotonic()
            fp.append(tuple(words[(o + d) // 4] for d in (0, 0x40, 0x4000, 0xc000)))
            if time.monotonic() - t0 > 0.002:
                raise SlowBar('BAR1 read at %#x took %.1f ms' % (o, (time.monotonic() - t0) * 1e3))
    finally:
        words.release()
    return fp


def save_raw(bar, path, size=PITCH * H):
    """Copy `size` bytes from BAR1 offset 0 with dword reads, pausing so the system stays usable.

    Convert afterwards without root: python3 -I tools/yuv2ppm.py <file.raw>
    """
    words = memoryview(bar).cast('I')
    out = bytearray(size)
    ov = memoryview(out).cast('I')
    try:
        t0 = time.monotonic()
        for i in range(size // 4):
            ov[i] = words[i]
            if i & 0xfff == 0xfff:
                time.sleep(0.001)
                if time.monotonic() - t0 > 60:
                    raise SlowBar('frame copy took over 60 s, stopped at %#x' % (i * 4))
    finally:
        ov.release()
        words.release()
    with open(path, 'wb') as f:
        f.write(out)
    return time.monotonic() - t0


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    vinctl = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0xd02
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDWR | os.O_SYNC)
    bar = mmap.mmap(fd, BAR1_SIZE, mmap.MAP_SHARED, mmap.PROT_READ)
    try:
        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        time.sleep(0.5)
        st = bus.read(0x98, 0x2f)[0]
        print('AD9398 mode %d, STATUS1=%02x (67 = locked), VinStatusHDMI=%08x' %
              (rx_mode, st, g.rd(VIN_STATUS_HDMI)))
        g.wr(VIN_CONTROL, vinctl)
        time.sleep(0.1)
        print('VinControl=%08x  VinStatusHDMI=%08x' % (vinctl, g.rd(VIN_STATUS_HDMI)), flush=True)
        notes = os.path.join(HERE, '..', 'notes')
        for n, (name, u2, u3) in enumerate(VARIANTS):
            g.wr(WDMA_CTL, 0)
            time.sleep(0.1)
            before = fingerprint(bar)
            ctl = 0x80000000 | u3 << 26 | u2 << 20
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL2, (PITCH // 4) << 16)
            g.wr(WDMA_CTL, ctl)
            s = []
            for _ in range(5):
                time.sleep(0.05)
                s.append('%08x' % g.rd(WDMA_STATUS))
            after = fingerprint(bar)
            changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
            print('%-8s Control=%08x  WDmaStatus %s  Int0=%08x  changed 64K chunks: %d%s' % (
                name, ctl, ' '.join(s), g.rd(INT_STATUS0), len(changed),
                (' (first %#x, last %#x)' % (changed[0] * CHUNK, changed[-1] * CHUNK)
                 if changed else '')), flush=True)
            if changed:
                path = os.path.join(notes, 'frame-%d.raw' % n)
                dt = save_raw(bar, path)
                print('   saved %s (%.1f s)' % (os.path.abspath(path), dt), flush=True)
        g.wr(WDMA_CTL, 0)
    except SlowBar as e:
        print('ABORT:', e)
        g.wr(WDMA_CTL, 0)
    finally:
        bar.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
