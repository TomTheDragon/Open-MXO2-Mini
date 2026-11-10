#!/usr/bin/env python3
"""Read-only probe of the Matrox MXO2 PCIe card (102b:8350).

Must run as root. Reads PCI config space and dumps BAR0 (registers) and the
start of BAR1. Nothing is written to the card except temporarily turning on
memory decoding in the PCI command register (restored afterwards).

Usage: sudo python3 -I probe.py [PCI address, default: found by ID] > probe.txt
"""
import mmap
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gaia import check_lockdown, find_dev  # noqa: E402

DEV = sys.argv[1] if len(sys.argv) > 1 else find_dev()
SYS = '/sys/bus/pci/devices/' + DEV
HERE = os.path.dirname(os.path.abspath(__file__))
REGS = os.path.join(HERE, '..', 'notes', 'gaia.regs')


def names():
    m = {}
    for line in open(REGS):
        off, n = line.split()
        m[int(off, 16)] = n
    return m


def cfg():
    with open(SYS + '/config', 'rb') as f:
        return bytearray(f.read())


def set_cmd(v):
    with open(SYS + '/config', 'r+b') as f:
        f.seek(4)
        f.write(struct.pack('<H', v))


def dump_bar(idx, length):
    path = '%s/resource%d' % (SYS, idx)
    size = os.path.getsize(path)
    length = min(length, size)
    fd = os.open(path, os.O_RDONLY | os.O_SYNC)
    try:
        mm = mmap.mmap(fd, length, mmap.MAP_SHARED, mmap.PROT_READ)
        words = []
        mv = memoryview(mm).cast('I')  # 32-bit accesses only
        for i in range(length // 4):
            words.append(mv[i])
        mv.release()
        mm.close()
    finally:
        os.close(fd)
    return size, words


def main():
    check_lockdown()
    c = cfg()
    print('# device %s' % DEV)
    print('# config space (%d bytes):' % len(c))
    for i in range(0, len(c), 16):
        print('  %03x: %s' % (i, ' '.join('%02x' % b for b in c[i:i + 16])))
    old_cmd = struct.unpack_from('<H', c, 4)[0]
    print('# PCI command was 0x%04x' % old_cmd)
    if not old_cmd & 2:
        set_cmd(old_cmd | 2)  # memory space enable only, no bus mastering
    try:
        reg = names()
        size, w = dump_bar(0, 0x2000)
        print('\n# BAR0 size 0x%x, named registers:' % size)
        for off in sorted(reg):
            if off // 4 < len(w):
                print('  0x%04x %-24s 0x%08x' % (off, reg[off], w[off // 4]))
        print('\n# BAR0 raw (lines that are all-zero skipped):')
        for i in range(0, len(w), 8):
            row = w[i:i + 8]
            if any(row):
                print('  %04x: %s' % (i * 4, ' '.join('%08x' % x for x in row)))
        size, w = dump_bar(1, 0x1000)
        print('\n# BAR1 size 0x%x, first 4 KiB (all-zero lines skipped):' % size)
        for i in range(0, len(w), 8):
            row = w[i:i + 8]
            if any(row):
                print('  %04x: %s' % (i * 4, ' '.join('%08x' % x for x in row)))
    finally:
        if not old_cmd & 2:
            set_cmd(old_cmd)
            print('\n# PCI command restored to 0x%04x' % old_cmd)


if __name__ == '__main__':
    main()
