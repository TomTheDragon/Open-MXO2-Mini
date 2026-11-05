#!/usr/bin/env python3
"""Audio probe 2: chain one-video-frame audio blocks like Windows and check for gaps.

audio_probe.py showed: 48 kHz, 8 ch x 32-bit words, 16-bit sample in the upper half; a block starts at the
next video-frame boundary after arming (AudioWDmaTimeStamp1 changes ~10 ms after arming, any size).
Hypothesis (Windows: blocks of exactly one frame, 1600 samples @ 30 fps): arming block k+1 while block k
records queues it for the next frame boundary -> gapless audio.
This probe, for two arming strategies, records 12 blocks of 48000/fps samples each into on-board
0x1f00000 + k * size, reads them through BAR1 and measures, for the tone on the loudest channel:
  - the time between block starts (AudioWDmaTimeStamp1 deltas, in 1/240 s ticks, and wall clock)
  - the phase jump at each block boundary, converted to the most likely gap in whole frames
    (needs a tone whose period does not divide one frame: 997 Hz works, 1000 Hz does not)
Strategies:  A = arm the next block as soon as the current one has started (TS1 changed)
             B = arm the next block only after the current one should be finished (TS1 + one frame)
Run as root, mxo2 unloaded, laptop on HDMI playing 997 Hz on the LEFT channel only:
    speaker-test -D hdmi -t sine -f 997 -c 2 -s 1
    sudo python3 -I audio_probe2.py [ad9398_mode] [shadow_log]
"""
import array
import math
import mmap
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from audio_probe import A_ADDR, A_CTL, A_INCTL, A_TS1, A_TS2, MEM_TARGET, WINDOW, ctl_value  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_CTL  # noqa: E402

VIC_FPS = {16: 60, 4: 60, 31: 50, 19: 50, 34: 30, 33: 25, 32: 24}
# Test area inside BAR1 window 3 (0x1800000..0x1ffffff): 12 blocks of up to 2000 samples x 32 B fit below
# 0x2000000 (the Windows ring at 0x1fb0800 is too close to the window end for 12 blocks).
BASE = 0x1f00000
BLOCKS = 12
OUT = os.path.join(HERE, '..', 'notes', 'audio-raw2-%s.bin')


def fit(seg, f, off=0, rate=48000):
    w = 2 * math.pi * f / rate
    s = sum(v * math.sin(w * (i + off)) for i, v in enumerate(seg))
    c = sum(v * math.cos(w * (i + off)) for i, v in enumerate(seg))
    return math.atan2(c, s), 2 * math.hypot(s, c) / max(1, len(seg))


def estimate_freq(seg, rate=48000):
    """Coarse from zero crossings, refined by maximising the fitted amplitude."""
    mean = sum(seg) / len(seg)
    zc = sum(1 for a, b in zip(seg, seg[1:]) if (a - mean) < 0 <= (b - mean))
    f0 = zc * rate / len(seg)
    best = max((fit(seg, f0 + d / 10)[1], f0 + d / 10) for d in range(-30, 31))
    return best[1]


def record(g, size, frame_s, strategy):
    ts_list, wall = [], []
    ts = g.rd(A_TS1)
    t0 = time.monotonic()
    g.wr(A_ADDR, BASE)
    g.wr(A_CTL, ctl_value(size))
    for k in range(1, BLOCKS + 1):
        deadline = time.monotonic() + 0.5
        while g.rd(A_TS1) == ts and time.monotonic() < deadline:
            time.sleep(0.0002)
        ts = g.rd(A_TS1)
        ts_list.append(ts)
        wall.append(time.monotonic() - t0)
        if k == BLOCKS:
            break
        if strategy == 'B':
            time.sleep(frame_s * 1.05)
        g.wr(A_ADDR, BASE + k * size)
        g.wr(A_CTL, ctl_value(size))
    # wait for the last block to finish
    time.sleep(frame_s * 2.5)
    g.wr(A_CTL, 0x48000000)
    return ts_list, wall


def read_blocks(g, bar, size):
    assert BASE + BLOCKS * size <= (WINDOW + 1) * (8 << 20), 'blocks leave the BAR1 window'
    g.wr(MEM_TARGET, WINDOW)
    start = (BASE - WINDOW * (8 << 20)) // 4
    words = array.array('I', (bar[start + i] for i in range(BLOCKS * size // 4)))
    g.wr(MEM_TARGET, 0)
    return words.tobytes()


def analyse(raw, spf, name):
    ints = struct.unpack('<%di' % (len(raw) // 4), raw)
    chans = [[x >> 16 for x in ints[c::8]] for c in range(8)]
    rms = [math.sqrt(sum(v * v for v in ch) / len(ch)) for ch in chans]
    loud = max(range(8), key=lambda c: rms[c])
    print('  channel RMS: %s -> tone on ch%d' % (' '.join('%d:%.0f' % (c, r) for c, r in enumerate(rms)), loud),
          flush=True)
    ch = chans[loud]
    f = estimate_freq(ch[:spf])
    print('  tone %.2f Hz (period %.2f samples; one frame = %d samples = %.2f periods)' % (
        f, 48000 / f, spf, spf * f / 48000), flush=True)
    period = 48000 / f
    frac = (spf / period) % 1
    if min(frac, 1 - frac) < 0.05:
        print('  WARNING: tone period nearly divides a frame - gaps of whole frames are invisible', flush=True)
    gaps = []
    for b in range(1, BLOCKS):
        p_end, _ = fit(ch[(b - 1) * spf:b * spf], f)            # phase at block b-1 start
        p_next, _ = fit(ch[b * spf:(b + 1) * spf], f)            # phase at block b start
        # if block b started g frames after block b-1 ended, its start phase is
        # p_end + 2*pi*f*(1 + g)*spf/48000
        best = min(range(0, 4), key=lambda g: abs(math.remainder(
            p_next - (p_end + 2 * math.pi * f * (1 + g) * spf / 48000), 2 * math.pi)))
        resid = math.remainder(p_next - (p_end + 2 * math.pi * f * (1 + best) * spf / 48000), 2 * math.pi)
        gaps.append(best)
        print('   boundary %2d: gap %d frame(s), residual %+.2f samples' % (
            b, best, resid / (2 * math.pi) * period), flush=True)
    print('  %s: %d of %d boundaries gapless' % (name, gaps.count(0), len(gaps)), flush=True)


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
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
        load_mode(bus, rx_mode)
        g.wr(VIN_CONTROL, 0xd02)
        for off in SYSTEM + INPUT:
            g.wr(off, shadow[off][0])
        g.wr(WDMA_CTL, 0)
        g.wr(A_INCTL, 0x00000001)
        g.wr(A_CTL, 0x48000000)
        time.sleep(0.3)
        vic = bus.read(RX, 0x84)[0]
        fps = VIC_FPS.get(vic, 50)
        spf = 48000 // fps
        size = spf * 32
        print('VIC %d -> %d fps, %d samples per frame, block %d bytes' % (vic, fps, spf, size), flush=True)
        for strategy in ('A', 'B'):
            print('# strategy %s' % strategy, flush=True)
            ts_list, wall = record(g, size, 1 / fps, strategy)
            d_ts = [b - a for a, b in zip(ts_list, ts_list[1:])]
            d_wall = [(b - a) * 1000 for a, b in zip(wall, wall[1:])]
            print('  TS1 deltas (ticks of 1/240 s, one frame = %.1f): %s' % (240 / fps, ' '.join(map(str, d_ts))),
                  flush=True)
            print('  wall deltas (ms, one frame = %.1f): %s' % (1000 / fps, ' '.join('%.0f' % d for d in d_wall)),
                  flush=True)
            print('  TS2 now %08x' % g.rd(A_TS2), flush=True)
            raw = read_blocks(g, bar, size)
            with open(OUT % strategy, 'wb') as f:
                f.write(raw)
            analyse(raw, spf, strategy)
    finally:
        g.wr(A_CTL, 0x48000000)
        g.wr(VIN_CONTROL, 0xd00)
        g.wr(MEM_TARGET, 0)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
