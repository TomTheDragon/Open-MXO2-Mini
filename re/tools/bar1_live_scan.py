#!/usr/bin/env python3
"""Look for captured frames in BAR1 while the video WDMA runs (read-only on BAR1).

Sets up capture like wdma_replay.py stages 0–2 (Windows preview values), then a background thread
re-arms VideoWDmaControl every 1/30 s with VideoWDmaAddress fixed at slot 0 (on-board 0).
For each MemoryTargetControl value it samples 2048 BAR1 words (one per 4 KiB) twice with capture
running and twice with capture paused:
    live  = words that changed while capturing
    noise = words that changed while paused
live >> noise means BAR1 shows the frame buffer. The 4 best candidates (+ MTC 0 and 0xd) get the
first 1080p frame (0x3f4800 bytes) saved to notes/bar1-frames/mtc-XXXXXXXX.raw for yuv2ppm.py.
Needs a MOVING picture on the HDMI source (a still image makes every frame identical).
Run as root, mxo2 module unloaded:
    sudo python3 -I bar1_live_scan.py [ad9398_mode] [shadow_log]
"""
import array
import mmap
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import load_mode  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_TS1  # noqa: E402

MEM_TARGET = 0x028
FRAME = 1920 * 1080 * 2
BAR1_SIZE = 8 << 20
STRIDE = 4096
OUT = os.path.join(HERE, '..', 'notes', 'bar1-frames')
MTCS = list(range(0x20)) + [0x100, 0x10000, 0x80000000, 0x8000000d]


class Rearm(threading.Thread):
    def __init__(self, g, ctl):
        super().__init__(daemon=True)
        self.g, self.ctl = g, ctl
        self.run_flag, self.stop_flag = threading.Event(), threading.Event()

    def run(self):
        while not self.stop_flag.is_set():
            if self.run_flag.is_set():
                self.g.wr(WDMA_ADDR, 0)
                self.g.wr(WDMA_CTL, self.ctl)
            time.sleep(1 / 30)


def sample(bar):
    return [bar[i] for i in range(0, BAR1_SIZE // 4, STRIDE // 4)]


def changed(a, b):
    return sum(x != y for x, y in zip(a, b))


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, BAR1_SIZE, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    rearm = None
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
        for off in SYSTEM + INPUT:
            g.wr(off, shadow[off][0])
        mtc_default = shadow[MEM_TARGET][0]
        rearm = Rearm(g, shadow[WDMA_CTL][0])
        rearm.start()

        t0 = time.monotonic()
        sample(bar)
        print('one sample pass (%d words) takes %.0f ms' % (BAR1_SIZE // STRIDE,
                                                           (time.monotonic() - t0) * 1000), flush=True)
        print('%-8s %-8s %5s %5s %6s %6s  %s' % ('mtc', 'readback', 'live', 'noise', 'frames',
                                                'zero', 'first words'), flush=True)
        results = []
        for mtc in MTCS:
            g.wr(MEM_TARGET, mtc)
            rb = g.rd(MEM_TARGET)
            rearm.run_flag.set()
            ts0 = g.rd(WDMA_TS1)
            time.sleep(0.15)
            a = sample(bar)
            time.sleep(0.15)
            b = sample(bar)
            ts1 = g.rd(WDMA_TS1)
            rearm.run_flag.clear()
            time.sleep(0.1)
            c = sample(bar)
            time.sleep(0.15)
            d = sample(bar)
            live, noise = changed(a, b), changed(c, d)
            results.append((live - noise, mtc))
            print('%08x %08x %5d %5d %6s %6d  %s' % (
                mtc, rb, live, noise, 'yes' if ts1 != ts0 else 'NO', a.count(0),
                ' '.join('%08x' % x for x in a[:4])), flush=True)

        os.makedirs(OUT, exist_ok=True)
        best = [m for s, m in sorted(results, reverse=True)[:4] if s > 0]
        for mtc in dict.fromkeys(best + [0, mtc_default]):
            g.wr(MEM_TARGET, mtc)
            rearm.run_flag.set()
            time.sleep(0.2)
            rearm.run_flag.clear()          # freeze the buffer while it is read
            time.sleep(0.05)
            t0 = time.monotonic()
            data = array.array('I', (bar[i] for i in range(FRAME // 4))).tobytes()  # 32-bit reads only
            path = os.path.join(OUT, 'mtc-%08x.raw' % mtc)
            with open(path, 'wb') as f:
                f.write(data)
            print('saved %s (%.1f s, %d distinct of first 4096 bytes)' % (
                path, time.monotonic() - t0, len(set(data[:4096]))), flush=True)
    finally:
        if rearm:
            rearm.stop_flag.set()
            rearm.join(1)
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        g.wr(MEM_TARGET, 0)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
