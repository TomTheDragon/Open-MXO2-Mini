#!/usr/bin/env python3
"""Watch read-only/RW Gaia registers and print every change (read-only, run as root).

Plug and unplug video sources while it runs. Ctrl+C to stop.
    sudo python3 -I watch.py [seconds] [0000:02:00.0]
"""
import mmap
import os
import struct
import sys
import time

SECS = float(sys.argv[1]) if len(sys.argv) > 1 else 120
DEV = sys.argv[2] if len(sys.argv) > 2 else '0000:02:00.0'
SYS = '/sys/bus/pci/devices/' + DEV
HERE = os.path.dirname(os.path.abspath(__file__))

# Registers that are readable (access 1 or 3) and not free-running counters.
SKIP = {'SystemClock', 'ClockCounter'}


def readable():
    regs = []
    tab = False
    for line in open(os.path.join(HERE, '..', 'notes', 'MVKAVBUS.regmap.txt')):
        if line.startswith('#'):
            tab = '0x4e730' in line
            continue
        if tab:
            f = line.split()
            off, acc, name = int(f[0], 16), int(f[1]), f[-1]
            if acc in (1, 3) and name not in SKIP:
                regs.append((off, name))
    return regs


def main():
    with open(SYS + '/config', 'r+b') as f:
        f.seek(4)
        old = struct.unpack('<H', f.read(2))[0]
        if not old & 2:
            f.seek(4)
            f.write(struct.pack('<H', old | 2))
    fd = os.open(SYS + '/resource0', os.O_RDONLY | os.O_SYNC)
    mm = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ)
    mv = memoryview(mm).cast('I')
    regs = readable()
    last = {off: mv[off // 4] for off, _ in regs}
    t0 = time.monotonic()
    print('# start values')
    for off, name in regs:
        print('  0x%03x %-22s 0x%08x' % (off, name, last[off]))
    print('# changes (time in s)', flush=True)
    try:
        while time.monotonic() - t0 < SECS:
            for off, name in regs:
                v = mv[off // 4]
                if v != last[off]:
                    print('%7.2f 0x%03x %-22s 0x%08x -> 0x%08x' %
                          (time.monotonic() - t0, off, name, last[off], v), flush=True)
                    last[off] = v
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        mv.release()
        mm.close()
        os.close(fd)
        if not old & 2:
            with open(SYS + '/config', 'r+b') as f:
                f.seek(4)
                f.write(struct.pack('<H', old))


if __name__ == '__main__':
    main()
