#!/usr/bin/env python3
"""Convert a raw 4:2:2 8-bit frame dump to PPM images (no root, no external libs).

Writes one PPM per guessed byte order (UYVY, YUYV) so the right one can be picked by eye, plus
"mxo2": YUYV with each chroma byte rotated left by 1 bit and bit 7 flipped – what the Gaia WDMA
currently stores with AD9398 mode 10 (notes/NOTES.md, BAR1 live scan).
Usage: python3 -I yuv2ppm.py frame.raw [width height pitch]
"""
import sys


MXO2_CHROMA = bytes((((b << 1) | (b >> 7)) & 0xff) ^ 0x80 for b in range(256))


def convert(data, w, h, pitch, order, fix=False):
    iu, iy0, iv, iy1 = order
    out = bytearray(w * h * 3)
    o = 0
    for y in range(h):
        row = data[y * pitch:y * pitch + w * 2]
        for x in range(0, w * 2, 4):
            u, v = row[x + iu], row[x + iv]
            if fix:
                u, v = MXO2_CHROMA[u], MXO2_CHROMA[v]
            u, v = u - 128, v - 128
            for yy in (row[x + iy0], row[x + iy1]):
                for c in (yy + 1.5748 * v, yy - 0.1873 * u - 0.4681 * v, yy + 1.8556 * u):
                    out[o] = 0 if c < 0 else 255 if c > 255 else int(c)
                    o += 1
    return out


def main():
    path = sys.argv[1]
    w, h = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1920, 1080)
    pitch = int(sys.argv[4]) if len(sys.argv) > 4 else w * 2
    data = open(path, 'rb').read()
    h = min(h, len(data) // pitch)
    for name, order, fix in (('uyvy', (0, 1, 2, 3), False), ('yuyv', (1, 0, 3, 2), False),
                             ('mxo2', (1, 0, 3, 2), True)):
        out = path.rsplit('.', 1)[0] + '-%s.ppm' % name
        with open(out, 'wb') as f:
            f.write(b'P6\n%d %d\n255\n' % (w, h) + convert(data, w, h, pitch, order, fix))
        print(out)


if __name__ == '__main__':
    main()
