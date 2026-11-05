#!/usr/bin/env python3
"""YUYV frame dump → PNG (no external libs), optionally downscaled. No root needed.

Usage: python3 -I raw2png.py frame.raw out.png [width height [scale [plain|mxo2]]]
  defaults 1920 1080, scale 2, plain. Pitch = width * 2.
  mxo2 = chroma for YCbCr 4:2:2 HDMI input: stored byte = [junk, ~C7, C6..C1] → C = ((b << 1) | 1) ^ 0x80
         (7-bit chroma; RGB sources such as a PC need plain, see NOTES.md).
"""
import struct
import sys
import zlib

MXO2 = bytes((((b << 1) | 1) & 0xff) ^ 0x80 for b in range(256))


def clamp(c):
    return 0 if c < 0 else 255 if c > 255 else int(c)


def main():
    a = sys.argv[1:]
    src, dst = a[0], a[1]
    w, h = (int(a[2]), int(a[3])) if len(a) > 3 else (1920, 1080)
    scale = int(a[4]) if len(a) > 4 else 2
    mode = a[5] if len(a) > 5 else 'plain'
    data = open(src, 'rb').read()
    h = min(h, len(data) // (w * 2))
    W, H = w // scale, h // scale
    rows = bytearray()
    for y in range(H):
        rows.append(0)
        line = data[y * scale * w * 2:(y * scale + 1) * w * 2]
        for x in range(W):
            o = (x * scale // 2) * 4                     # YUYV pair containing this pixel
            yy = line[o + (2 if (x * scale) & 1 else 0)]
            u, v = line[o + 1], line[o + 3]
            if mode == 'mxo2':
                u, v = MXO2[u], MXO2[v]
            u, v = u - 128, v - 128
            rows += bytes((clamp(yy + 1.5748 * v), clamp(yy - 0.1873 * u - 0.4681 * v),
                           clamp(yy + 1.8556 * u)))

    def chunk(t, b):
        return struct.pack('>I', len(b)) + t + b + struct.pack('>I', zlib.crc32(t + b) & 0xffffffff)
    with open(dst, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(bytes(rows), 6)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    main()
