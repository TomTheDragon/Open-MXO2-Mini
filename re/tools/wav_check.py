#!/usr/bin/env python3
"""Check a recorded test-tone WAV for level, frequency and gaps (no root needed).

For each channel: RMS, tone frequency; then the recording is cut into 10 ms pieces and the phase of
each piece is compared with the phase predicted from the previous one. A jump means samples were lost
or duplicated; it is reported with its position and size in samples (exact for a steady sine tone).
Usage: python3 -I wav_check.py file.wav [more.wav ...]
"""
import math
import struct
import sys
import wave


def fit(seg, f, rate):
    w = 2 * math.pi * f / rate
    s = sum(v * math.sin(w * i) for i, v in enumerate(seg))
    c = sum(v * math.cos(w * i) for i, v in enumerate(seg))
    return math.atan2(c, s), 2 * math.hypot(s, c) / max(1, len(seg))


def estimate_freq(seg, rate):
    mean = sum(seg) / len(seg)
    zc = sum(1 for a, b in zip(seg, seg[1:]) if (a - mean) < 0 <= (b - mean))
    f = zc * rate / len(seg)
    for step in (1.0, 0.1, 0.01):
        f = max((fit(seg, f + d * step, rate)[1], f + d * step) for d in range(-10, 11))[1]
    return f


def check(path):
    w = wave.open(path)
    rate, nch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    data = w.readframes(n)
    if width != 2:
        print('%s: only 16-bit supported' % path)
        return
    vals = struct.unpack('<%dh' % (len(data) // 2), data)
    print('%s: %d Hz, %d ch, %.2f s' % (path, rate, nch, n / rate))
    chans = [vals[c::nch] for c in range(nch)]
    for c, ch in enumerate(chans):
        rms = math.sqrt(sum(v * v for v in ch) / max(1, len(ch)))
        print('  ch%d: RMS %.0f, peak %d' % (c, rms, max((abs(v) for v in ch), default=0)))
    loud = max(range(nch), key=lambda c: sum(v * v for v in chans[c]))
    ch = chans[loud]
    if max((abs(v) for v in ch), default=0) < 1000:
        print('  no tone found (silence?)')
        return
    first = ch[rate // 10: rate // 10 + rate // 5]
    f = estimate_freq(first, rate)
    period = rate / f
    print('  tone on ch%d: %.2f Hz' % (loud, f))
    piece = rate // 100
    jumps = []
    prev = None
    for k in range(len(ch) // piece):
        seg = ch[k * piece:(k + 1) * piece]
        ph, amp = fit(seg, f, rate)
        if amp < 0.3 * max(1, max(abs(v) for v in first)):
            prev = None
            jumps.append((k, None))
            continue
        if prev is not None:
            expect = prev + 2 * math.pi * f * piece / rate
            d = math.remainder(ph - expect, 2 * math.pi) / (2 * math.pi) * period
            if abs(d) > 1.0:
                jumps.append((k, d))
        prev = ph
    quiet = sum(1 for _, d in jumps if d is None)
    real = [(k, d) for k, d in jumps if d is not None]
    print('  %d phase jumps (> 1 sample), %d quiet 10 ms pieces' % (len(real), quiet))
    for k, d in real[:15]:
        print('    at %.2f s: %+.1f samples (mod one period of %.1f)' % (k * piece / rate, d, period))
    print('  verdict: %s' % ('gapless' if not real and not quiet else 'GAPS / glitches'))


if __name__ == '__main__':
    for p in sys.argv[1:]:
        check(p)
