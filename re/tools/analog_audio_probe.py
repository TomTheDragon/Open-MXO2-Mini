#!/usr/bin/env python3
"""Analog audio probe: how the card captures the analog audio inputs (composite source + audio connected).

Windows composite preview (notes/win-regshadow-analog.txt): AudioInputCtl 0x40001, AudioWDmaControl 0xc8000c84 =
go | 0x48000000 | ch_code 0 | 0xc84 dwords = 1602 samples x 2 words (stereo, 8 B per sample) = one 29.97 Hz frame.
HDMI uses ch_code 2 (8 ch, 32 B per sample). The driver so far used the HDMI layout for analog.
A: blocks/s and data per word for AudioInputCtl x ch_code (block 400 samples).
B: samples per field: block sizes around 48000 / field rate; a block longer than a field makes the next one wait a
   field (block rate halves) -> largest size that keeps one block per field.
C: one long block (30000 samples, 0.62 s) -> notes/analog-audio-long.wav + tone frequency (play a steady tone to
   check the sample rate); D: 5 s of field-sized blocks back to back -> notes/analog-audio-blocks.wav (listen for
   clicks / gaps).
Run as root, mxo2 unloaded:  sudo python3 -I analog_audio_probe.py
"""
import mmap
import os
import struct
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from analog_probe import OUT, VIN_CONTROL_ANALOG, dec_status, load_decoder, DEC  # noqa: E402
from analog_probe7 import DEFAULT  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from interlace_probe2 import MEM_TARGET, read  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_CTL2, WDMA_TS1  # noqa: E402

A_CTL, A_ADDR, A_TS1, A_INPUT, A_PLL = 0x200, 0x204, 0x900, 0x480, 0x038
RING, SLOT = 0x1f00000, 0x20000
NOTES = os.path.join(HERE, '..', 'notes')


class Cap:
    def __init__(self, g, bar):
        self.g, self.bar = g, bar
        self.vts = g.rd(WDMA_TS1)
        g.wr(WDMA_ADDR, 0x100000)
        g.wr(WDMA_CTL, 0x82000000)

    def video(self):
        """Keep the video WDMA armed (packed field into 0x100000), as the driver does. Returns True on a field."""
        t = self.g.rd(WDMA_TS1)
        if t == self.vts:
            return False
        self.vts = t
        self.g.wr(WDMA_ADDR, 0x100000)
        self.g.wr(WDMA_CTL, 0x82000000)
        return True

    def run(self, secs, samples, ch_code, frame_bytes, keep=0):
        """Blocks of `samples` back to back (next armed when one starts). Returns (blocks/s, fields/s, starts,
        list of (slot, samples) of the last `keep` finished blocks)."""
        g = self.g
        g.wr(A_CTL, 0x48000000)
        words = (samples * frame_bytes // 4) & 0xfffe
        ctl = 0x80000000 | 0x48000000 | ch_code << 21 | words
        ats = g.rd(A_TS1)
        armed = 0
        g.wr(A_ADDR, RING + (armed % 8) * SLOT)
        g.wr(A_CTL, ctl)
        armed += 1
        starts, fields, done = [], 0, []
        t0 = time.monotonic()
        while time.monotonic() - t0 < secs:
            if self.video():
                fields += 1
            t = g.rd(A_TS1)
            if t != ats:
                ats = t
                starts.append((time.monotonic(), t))
                if len(starts) >= 2:
                    done.append(((len(starts) - 2) % 8, samples))
                g.wr(A_ADDR, RING + (armed % 8) * SLOT)
                g.wr(A_CTL, ctl)
                armed += 1
            time.sleep(0.0002)
        g.wr(A_CTL, 0x48000000)
        el = time.monotonic() - t0
        return len(starts) / el, fields / el, starts, done[-keep:] if keep else []

    def block(self, slot, samples, frame_bytes):
        return read(self.bar, RING + slot * SLOT, samples * frame_bytes, self.g)


def words_stats(data, frame_bytes):
    """Peak of the upper 16 bits per 32-bit word position inside a sample frame."""
    n = frame_bytes // 4
    peaks = [0] * n
    for i in range(0, len(data) - frame_bytes + 1, frame_bytes):
        for w in range(n):
            v = struct.unpack_from('<i', data, i + 4 * w)[0] >> 16
            peaks[w] = max(peaks[w], abs(v))
    return peaks


def to_stereo(data, frame_bytes):
    out = bytearray()
    for i in range(0, len(data) - frame_bytes + 1, frame_bytes):
        l, r = struct.unpack_from('<ii', data, i)
        out += struct.pack('<hh', l >> 16, r >> 16)
    return bytes(out)


def save_wav(path, pcm):
    with wave.open(path, 'wb') as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(pcm)


def tone(pcm):
    """Frequency of the left channel from rising zero crossings (Hz at 48 kHz), and its peak."""
    s = [struct.unpack_from('<h', pcm, i)[0] for i in range(0, len(pcm), 4)]
    peak = max(abs(v) for v in s) if s else 0
    if peak < 300:
        return 0.0, peak
    th = peak // 8
    ups, armed = [], False
    for i in range(1, len(s)):
        if s[i] < -th:
            armed = True
        elif armed and s[i] >= 0 and s[i - 1] < 0:
            ups.append(i - s[i] / (s[i] - s[i - 1]))
            armed = False
    if len(ups) < 3:
        return 0.0, peak
    return 48000 * (len(ups) - 1) / (ups[-1] - ups[0]), peak


def setup(mode=0):
    """Card set up for composite capture as in the driver (decoder table + overrides, analog system values).
    mode: ADV7181 table, 0 = NTSC composite, 3 = PAL composite."""
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', 'analog', '2-preview.txt'))
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(mm).cast('I')
    for off, flags, reset, _ in init.gaia_table():
        if flags in (2, 3):
            g.wr(off, reset)
    for off in SYSTEM:
        g.wr(off, shadow[off][0])
    bus = I2C(g, *BUSES['I2CCtl'])
    load_mode(bus, 0)
    bus.write(RX, 0x26, bus.read(RX, 0x26)[0] | 1)
    load_decoder(bus, mode)
    for r, v in sorted(DEFAULT.items()):
        bus.write(DEC, r, v)
    time.sleep(0.8)
    print('decoder mode %d:%s' % (mode, dec_status(bus)[3]), flush=True)
    g.wr(MEM_TARGET, 0)
    g.wr(VIN_CONTROL, 0x0d00)
    g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
    g.wr(WDMA_CTL2, 0x101 | 360 << 16)
    g.wr(A_PLL, 2)
    g.wr(A_INPUT, 0x40001)
    return g, bar, mm, fd


def teardown(g, bar, mm, fd):
    g.wr(A_CTL, 0x48000000)
    g.wr(WDMA_CTL, 0)
    g.wr(WDMA_CTL2, 0x41)
    g.wr(VIN_CONTROL, 0xd00)
    bar.release()
    mm.close()
    os.close(fd)
    g.close()


def main():
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', 'analog', '2-preview.txt'))
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
        load_decoder(bus, 0)
        for r, v in sorted(DEFAULT.items()):
            bus.write(DEC, r, v)
        time.sleep(0.8)
        print('decoder:%s' % dec_status(bus)[3], flush=True)
        g.wr(MEM_TARGET, 0)
        g.wr(VIN_CONTROL, 0x0d00)
        g.wr(VIN_CONTROL_ANALOG, 0xc0000000)
        g.wr(WDMA_CTL2, 0x101 | 360 << 16)
        g.wr(A_PLL, 2)
        cap = Cap(g, bar)

        print('--- A: AudioInputCtl x channel layout, 400-sample blocks, 1.5 s each', flush=True)
        for ictl in (0x40001, 0x00001, 0x40000):
            g.wr(A_INPUT, ictl)
            for ch_code, fb in ((0, 8), (2, 32)):
                rate, fps, starts, done = cap.run(1.5, 400, ch_code, fb, keep=3)
                peaks = [0] * (fb // 4)
                for slot, n in done:
                    peaks = [max(a, b) for a, b in zip(peaks, words_stats(cap.block(slot, n, fb), fb))]
                print('InputCtl %05x ch_code %d (%2d B/sample): %5.1f blocks/s, %5.1f fields/s, word peaks %s' % (
                    ictl, ch_code, fb, rate, fps, peaks), flush=True)

        g.wr(A_INPUT, 0x40001)
        print('--- B: samples per field (ch_code 0, 8 B/sample): block rate vs block size', flush=True)
        _, fps, _, _ = cap.run(1.0, 400, 0, 8)
        nominal = int(48000 / fps) if fps else 800
        fit = None
        for n in range(nominal - 12, nominal + 13, 2):
            rate, f, starts, _ = cap.run(1.2, n, 0, 8)
            one = f and rate > 0.9 * f
            print('  %4d samples: %5.1f blocks/s (fields %5.1f) %s' % (n, rate, f, 'one per field' if one else
                                                                            'SKIPS fields'), flush=True)
            if one:
                fit = n
        for n in (fit, (fit or nominal) + 1):
            if n:
                rate, f, starts, _ = cap.run(1.2, n, 0, 8)
                print('  %4d samples: %5.1f blocks/s (fields %5.1f)' % (n, rate, f), flush=True)
                if f and rate > 0.9 * f:
                    fit = n
        if len(starts) > 10:
            per = (starts[-1][0] - starts[1][0]) / (len(starts) - 2)
            print('  field period from block starts: %.4f ms = %.3f Hz -> %.2f samples per field at 48 kHz' % (
                per * 1000, 1 / per, 48000 * per), flush=True)
        print('  largest block that keeps one block per field: %s samples' % fit, flush=True)

        print('--- C: one long block (30000 samples)', flush=True)
        g.wr(A_CTL, 0x48000000)
        g.wr(A_ADDR, RING)
        g.wr(A_CTL, 0x80000000 | 0x48000000 | (30000 * 2 & 0xfffe))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.9:
            cap.video()
            time.sleep(0.0005)
        g.wr(A_CTL, 0x48000000)
        long_pcm = to_stereo(read(bar, RING, 30000 * 8, g), 8)
        save_wav(os.path.join(NOTES, 'analog-audio-long.wav'), long_pcm)
        f, peak = tone(long_pcm)
        print('  saved notes/analog-audio-long.wav: left peak %d, tone %s' % (
            peak, '%.2f Hz' % f if f else 'none found'), flush=True)

        print('--- D: 5 s of field-sized blocks (%s samples) back to back' % fit, flush=True)
        n = fit or nominal - 2
        pcm = bytearray()
        g.wr(A_CTL, 0x48000000)
        ctl = 0x80000000 | 0x48000000 | (n * 2 & 0xfffe)
        ats = g.rd(A_TS1)
        g.wr(A_ADDR, RING)
        g.wr(A_CTL, ctl)
        armed, started, copied = 1, 0, 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5:
            cap.video()
            t = g.rd(A_TS1)
            if t != ats:
                ats = t
                started += 1
                g.wr(A_ADDR, RING + (armed % 8) * SLOT)
                g.wr(A_CTL, ctl)
                armed += 1
            while copied < started - 1:     # block `copied` is finished once the next one started
                pcm += to_stereo(read(bar, RING + (copied % 8) * SLOT, n * 8, g), 8)
                copied += 1
            time.sleep(0.0002)
        g.wr(A_CTL, 0x48000000)
        save_wav(os.path.join(NOTES, 'analog-audio-blocks.wav'), bytes(pcm))
        f, peak = tone(bytes(pcm))
        print('  saved notes/analog-audio-blocks.wav: %d blocks, %.2f s of audio in 5 s, left peak %d, tone %s' % (
            copied, len(pcm) / 4 / 48000, peak, '%.2f Hz' % f if f else 'none'), flush=True)
    finally:
        g.wr(A_CTL, 0x48000000)
        g.wr(WDMA_CTL, 0)
        g.wr(WDMA_CTL2, 0x41)
        g.wr(VIN_CONTROL, 0xd00)
        bar.release()
        mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
