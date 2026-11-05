#!/usr/bin/env python3
"""First audio capture probe: does the audio write DMA run, how fast, and what does the data look like?

Audio capture (MVKAVBUS "ACaptur_" class, FUN_000112a8 start):
  AudioWDmaAddress 0x204 = on-board address & 0x3fffffff
  AudioWDmaControl 0x200 = 0x80000000 (go) | 0x48000000 | ch_code << 21 | (bytes / 4 & 0xfffe)
                           ch_code: 2 ch 0, 4 ch 1, 8 ch 2, 16 ch 3; bytes <= 0x40000
  Windows preview: 0xc8403200 = 8 channels, 51200 bytes (1600 samples x 8 ch x 4 B = 30 fps @ 48 kHz),
  blocks at 0x1fb0800 + k * 0xc800, AudioInputCtl 0x00000001, idle Control 0x48000000.
Steps:
  1 receiver audio status (AD9398 packet / channel status / N, CTS / audio InfoFrame)
  2 timing: arm one block of 25600 / 51200 / 102400 bytes and time it until AudioWDmaTimeStamp1 changes
    (continuous 8 ch x 32 bit @ 48 kHz would take 16.7 / 33.3 / 66.7 ms), with and without video capture
  3 content: chain 6 blocks of 51200 bytes back to back, read them through BAR1 (MemoryTargetControl 3 =
    on-board 0x1800000..0x1ffffff) to notes/audio-raw.bin, print per-channel statistics and the tone
    frequency of channels 0/1 (zero crossings, assuming 48 kHz)
Best with a known tone: laptop on HDMI, audio output set to HDMI, playing a 1 kHz sine, e.g.
    speaker-test -D hdmi -t sine -f 1000 -c 2     (or any 1 kHz test tone video)
Run as root, mxo2 unloaded:  sudo python3 -I audio_probe.py [ad9398_mode] [shadow_log]
"""
import array
import mmap
import os
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_TS1  # noqa: E402

A_CTL, A_ADDR, A_INCTL = 0x200, 0x204, 0x480
A_TS1, A_TS2, A_TBC, A_DBG, A_CRC = 0x900, 0x904, 0x908, 0x924, 0x068
MEM_TARGET = 0x028
BASE = 0x1fb0800
BLOCK = 51200
WINDOW = 3                              # 8 MiB window holding BASE
OUT = os.path.join(HERE, '..', 'notes', 'audio-raw.bin')


def ctl_value(nbytes, channels=8):
    code = {2: 0, 4: 1, 8: 2, 16: 3}[channels]
    return 0x80000000 | 0x48000000 | code << 21 | ((nbytes >> 2) & 0xfffe)


class VideoRearm(threading.Thread):
    def __init__(self, g, ctl):
        super().__init__(daemon=True)
        self.g, self.ctl, self.on, self.stop = g, ctl, threading.Event(), threading.Event()

    def run(self):
        while not self.stop.is_set():
            if self.on.is_set():
                self.g.wr(WDMA_ADDR, 0)
                self.g.wr(WDMA_CTL, self.ctl)
            time.sleep(1 / 60)


def wait_change(g, reg, old, timeout=1.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        v = g.rd(reg)
        if v != old:
            return v, time.monotonic() - t0
        time.sleep(0.0003)
    return None, timeout


def tone_freq(samples, rate=48000):
    if len(samples) < 100:
        return 0
    mean = sum(samples) / len(samples)
    crossings = sum(1 for a, b in zip(samples, samples[1:]) if (a - mean) < 0 <= (b - mean))
    return crossings * rate / len(samples)


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    video = None
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

        r = lambda reg: bus.read(RX, reg)[0]  # noqa: E731
        n = (r(0x7d) & 0x0f) << 16 | r(0x7e) << 8 | r(0x7f)
        cts = r(0x7b) << 12 | r(0x7c) << 4 | r(0x7d) >> 4
        print('# 1 receiver: STATUS1=%02x STATUS3=%02x packets=%02x HDMI MODE=%02x | channel status %02x '
              'cat %02x src %02x freq %02x wordlen %02x | N=%d CTS=%d | audio InfoFrame %s' % (
                  r(0x2f), r(0x81), r(0x5a), r(0x5b), r(0x5e), r(0x5f), r(0x60), r(0x61), r(0x62), n, cts,
                  ' '.join('%02x' % r(a) for a in range(0x90, 0x96))), flush=True)
        print('  (N/CTS: fs = TMDS clock * N / (128 * CTS); N 6144 = 48 kHz, 6272 = 44.1 kHz, 4096 = 32 kHz)',
              flush=True)

        video = VideoRearm(g, shadow[WDMA_CTL][0])
        video.start()
        print('# 2 timing (time until AudioWDmaTimeStamp1 changes after arming one block)', flush=True)
        for with_video in (False, True):
            if with_video:
                video.on.set()
                time.sleep(0.2)
            for nbytes in (25600, 51200, 102400):
                ts = g.rd(A_TS1)
                g.wr(A_ADDR, BASE)
                g.wr(A_CTL, ctl_value(nbytes))
                v, dt = wait_change(g, A_TS1, ts)
                print('  video %-3s %6d bytes: %s after %5.1f ms (continuous would be %4.1f ms) | TS2 %08x '
                      'TBC %08x dbg %08x crc %04x' % (
                          'on' if with_video else 'off', nbytes, 'TS1 changed' if v is not None else 'NO change',
                          dt * 1000, nbytes / (8 * 4 * 48000) * 1000, g.rd(A_TS2), g.rd(A_TBC), g.rd(A_DBG),
                          g.rd(A_CRC) & 0xffff), flush=True)
                g.wr(A_CTL, 0x48000000)
                time.sleep(0.05)

        print('# 3 content: 6 chained blocks of %d bytes from %#x (video on)' % (BLOCK, BASE), flush=True)
        times = []
        ts = g.rd(A_TS1)
        t0 = time.monotonic()
        for k in range(6):
            g.wr(A_ADDR, BASE + k * BLOCK)
            g.wr(A_CTL, ctl_value(BLOCK))
            ts, _ = wait_change(g, A_TS1, ts)
            times.append(time.monotonic() - t0)
            if ts is None:
                print('  block %d: no TS change, stopping' % k, flush=True)
                break
        g.wr(A_CTL, 0x48000000)
        video.on.clear()
        print('  block done times (ms): %s' % ' '.join('%.1f' % (t * 1000) for t in times), flush=True)

        g.wr(MEM_TARGET, WINDOW)
        start = (BASE - WINDOW * (8 << 20)) // 4
        words = array.array('I', (bar[start + i] for i in range(6 * BLOCK // 4)))
        g.wr(MEM_TARGET, 0)
        raw = words.tobytes()
        with open(OUT, 'wb') as f:
            f.write(raw)
        print('  saved %s (%d bytes); first 4 sample frames (8 x 32 bit):' % (OUT, len(raw)), flush=True)
        for fr in range(4):
            print('   ' + ' '.join('%08x' % words[fr * 8 + c] for c in range(8)), flush=True)
        ints = struct.unpack('<%di' % (len(raw) // 4), raw)
        for c in range(8):
            ch = ints[c::8]
            nz = sum(1 for x in ch if x)
            lo, hi = min(ch), max(ch)
            print('  ch%d: nonzero %5d/%d  min %11d  max %11d  low byte always 0: %-5s  tone %6.0f Hz' % (
                c, nz, len(ch), lo, hi, all((x & 0xff) == 0 for x in ch), tone_freq(ch)), flush=True)
        for b in range(1, 6):          # continuity at block boundaries, channel 0
            i = b * BLOCK // 32
            print('  boundary %d, ch0 around it: %s' % (b, ' '.join('%d' % (x >> 8) for x in ints[(i - 3) * 8:(i + 3) * 8:8])),
                  flush=True)
    finally:
        if video:
            video.stop.set()
            video.join(1)
        g.wr(A_CTL, 0x48000000)
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        g.wr(MEM_TARGET, 0)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
