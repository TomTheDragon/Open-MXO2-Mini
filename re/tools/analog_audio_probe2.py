#!/usr/bin/env python3
"""Analog audio probe 2: block sizes inside the working window (probe 1: 792..802 samples per 60.10 Hz field).

For each size: 4 s of blocks back to back (next armed as soon as one starts, finished ones copied), as the driver
would do. Prints blocks/s vs fields/s, the audio delivered per second (48000 = gapless), and a click metric: mean
sample step across block boundaries / mean step inside blocks (~1 = continuous, >> 1 = gaps or jumps). Also tries
two-field blocks (Windows uses one block per 29.97 Hz frame). WAVs -> notes/analog-audio2-<size>.wav.
Play steady audio (a tone, or music) into the analog audio inputs.
Run as root, mxo2 unloaded:  sudo python3 -I analog_audio_probe2.py
"""
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_audio_probe import (A_ADDR, A_CTL, A_TS1, NOTES, RING, SLOT, Cap, save_wav, setup, teardown,  # noqa: E402
                                to_stereo, tone)
from interlace_probe2 import read  # noqa: E402


def capture(cap, g, bar, n, secs):
    pcm, bounds = bytearray(), []
    g.wr(A_CTL, 0x48000000)
    ctl = 0x80000000 | 0x48000000 | (n * 2 & 0xfffe)
    ats = g.rd(A_TS1)
    g.wr(A_ADDR, RING)
    g.wr(A_CTL, ctl)
    armed, started, copied, fields = 1, 0, 0, 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < secs:
        if cap.video():
            fields += 1
        t = g.rd(A_TS1)
        if t != ats:
            ats = t
            started += 1
            g.wr(A_ADDR, RING + (armed % 8) * SLOT)
            g.wr(A_CTL, ctl)
            armed += 1
        if copied < started - 1:
            pcm += to_stereo(read(bar, RING + (copied % 8) * SLOT, n * 8, g), 8)
            bounds.append(len(pcm) // 4)
            copied += 1
        time.sleep(0.0002)
    g.wr(A_CTL, 0x48000000)
    el = time.monotonic() - t0
    return bytes(pcm), bounds, started / el, fields / el, el


def clicks(pcm, bounds):
    s = [struct.unpack_from('<h', pcm, i)[0] for i in range(0, len(pcm), 4)]
    if len(s) < 100 or len(bounds) < 3:
        return float('nan')
    inner = sum(abs(s[i] - s[i - 1]) for i in range(1, len(s))) / (len(s) - 1)
    edge = sum(abs(s[b] - s[b - 1]) for b in bounds[:-1] if 0 < b < len(s)) / max(1, len(bounds) - 1)
    return edge / inner if inner else float('nan')


def main():
    g, bar, mm, fd = setup()
    try:
        cap = Cap(g, bar)
        print('size  blocks/s fields/s  audio/s  clicks  tone', flush=True)
        for n in (790, 792, 794, 796, 797, 798, 799, 800, 802, 1590, 1596, 1600):
            pcm, bounds, rate, fps, el = capture(cap, g, bar, n, 4)
            f, peak = tone(pcm)
            save_wav(os.path.join(NOTES, 'analog-audio2-%d.wav' % n), pcm)
            print('%4d  %7.1f  %7.1f  %7.0f  %6.2f  %s (peak %d)' % (
                n, rate, fps, len(pcm) / 4 / el, clicks(pcm, bounds), '%.1f Hz' % f if f else '-', peak), flush=True)
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
