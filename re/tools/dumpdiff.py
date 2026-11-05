#!/usr/bin/env python3
"""Decode BAR0 dumps taken under Windows (RWEverything, 4 KiB binary) and compare them.

Prints every Gaia register that is readable (flag 1 or 3) with its value in each dump and
marks differences; write-only registers (flag 2) read as 0 and are skipped.
Usage: python3 -I dumpdiff.py idle.bin capture1.bin [capture2.bin ...]
       python3 -I dumpdiff.py DIR   (all *.bin in a mxo2-capture.ps1 folder, except pci-config.bin)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import init  # noqa: E402


def main():
    paths = []
    for a in sys.argv[1:]:
        if os.path.isdir(a):
            paths += sorted(os.path.join(a, f) for f in os.listdir(a)
                            if f.endswith('.bin') and f != 'pci-config.bin')
        else:
            paths.append(a)
    dumps = []
    for p in paths:
        d = open(p, 'rb').read()
        if len(d) < 0x1000:
            sys.exit('%s: %d bytes, need 4096' % (p, len(d)))
        if d[:4] == bytes.fromhex('2b105083'):
            bar0 = struct.unpack_from('<I', d, 0x10)[0] & ~0xf
            sys.exit('%s is PCI config space (vendor 102b, device 8350), not BAR0.\n'
                     'Dump memory at BAR0 = 0x%08x instead (4 KiB covers all Gaia registers).'
                     % (p, bar0))
        words = struct.unpack_from('<1024I', d)
        # Sanity check: under Linux BoardStatus = 0x0501xxxx and GaiaVersion = 0xf.
        if words[0] >> 16 != 0x0501 or words[2] != 0xf:
            print('WARNING %s: BoardStatus=%08x GaiaVersion=%08x (expected 0501xxxx / 0000000f) -> '
                  'not a 32-bit read of the Gaia registers (wrong address or byte access?)'
                  % (p, words[0], words[2]), file=sys.stderr)
        dumps.append(words)
    names = [os.path.basename(p) for p in paths]
    print('%-5s %-26s %s' % ('off', 'register', '  '.join('%-10s' % n[:10] for n in names)))
    known = set()
    for off, flags, reset, name in init.gaia_table():
        known.add(off)
        if flags == 2:
            continue
        vals = [d[off // 4] for d in dumps]
        mark = ' *' if len(set(vals)) > 1 else ''
        print('%03x   %-26s %s%s' % (off, name[:26], '  '.join('%08x  ' % v for v in vals), mark))
    extra = [o * 4 for o in range(1024) if o * 4 not in known and any(d[o] for d in dumps)]
    if extra:
        print('\n# non-zero words at offsets not in the Gaia table')
        for off in extra:
            print('%03x   %-26s %s' % (off, '?', '  '.join('%08x  ' % d[off // 4] for d in dumps)))


if __name__ == '__main__':
    main()
