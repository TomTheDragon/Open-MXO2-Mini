#!/usr/bin/env python3
"""Frame-to-frame picture movement in a series of YUYV frames (vertical/horizontal jitter).

Each frame's row and column luma profiles are matched against the first frame: the integer shift with the
smallest mean difference is that frame's offset. Prints the offset sequence and a histogram.
Usage: python3 -I wobble.py FILE WIDTH HEIGHT   (FILE = concatenated frames, e.g. v4l2-ctl --stream-to)
"""
import collections
import sys


def profiles(data, w, h):
    r = w * 2
    rows = [sum(data[y * r:(y + 1) * r:8]) / (w / 4) for y in range(h)]
    step = max(1, h // 64)
    cols = [sum(data[y * r + 2 * x] for y in range(0, h, step)) / len(range(0, h, step)) for x in range(w)]
    return rows, cols


def best_shift(prof, ref, rng):
    best = None
    for s in range(-rng, rng + 1):
        pairs = [(prof[i], ref[i + s]) for i in range(len(prof)) if 0 <= i + s < len(ref)]
        d = sum(abs(a - b) for a, b in pairs) / len(pairs)
        if best is None or d < best[1]:
            best = (s, d)
    return best[0]


def content(rows, thr=24):
    hot = [i for i, v in enumerate(rows) if v > thr]
    return (hot[0], hot[-1]) if hot else (-1, -1)


def analyse(frames, w, h, label=''):
    """frames: list of bytes. Returns a summary line; prints the sequence."""
    ref_r, ref_c = profiles(frames[0], w, h)
    vs, hs, ext = [], [], collections.Counter()
    for f in frames:
        rws, cls = profiles(f, w, h)
        vs.append(best_shift(rws, ref_r, 6))
        hs.append(best_shift(cls, ref_c, 8))
        ext[content(rws)] += 1
    print('%s vertical  : %s' % (label, ' '.join('%+d' % v for v in vs)))
    print('%s horizontal: %s' % (label, ' '.join('%+d' % v for v in hs)))
    print('%s content rows (first, last): %s' % (label, dict(ext.most_common(4))))
    return '%s %d frames, vertical %s, horizontal %s' % (
        label, len(frames), dict(collections.Counter(vs)), dict(collections.Counter(hs)))


def main():
    path, w, h = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    data = open(path, 'rb').read()
    size = w * h * 2
    frames = [data[i:i + size] for i in range(0, len(data) - size + 1, size)]
    print(analyse(frames, w, h, path.rsplit('/', 1)[-1]))


if __name__ == '__main__':
    main()
