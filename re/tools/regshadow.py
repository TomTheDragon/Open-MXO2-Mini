#!/usr/bin/env python3
"""Decode windows/mxo2-regs.cmd output (kd logs with "REG idx off … value … pending …" lines).

Prints every Gaia register with the value the Windows driver last wrote, one column per log,
marking registers that differ between logs and write-only ones (flag 2, invisible in BAR0 dumps).
Usage: python3 -I regshadow.py DIR_OR_FILES...   (e.g. notes/win/regs)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import init  # noqa: E402

RX = re.compile(r'REG\s+(\d+) off ([0-9a-f]{8}) flags ([0-9a-f]{8}) value ([0-9a-f]{8}) '
                r'pending ([0-9a-f]{8})', re.I)


def load(path):
    regs = {}
    for line in open(path, errors='replace'):
        m = RX.search(line)
        if m:
            off = int(m.group(2), 16)
            regs[off] = (int(m.group(4), 16), int(m.group(5), 16), int(m.group(3), 16))
    return regs


def main():
    paths = []
    for a in sys.argv[1:]:
        if os.path.isdir(a):
            paths += sorted(os.path.join(a, f) for f in os.listdir(a) if f.endswith('.txt'))
        else:
            paths.append(a)
    logs = [(os.path.basename(p)[:-4], load(p)) for p in paths]
    for name, regs in logs:
        if not regs:
            print('WARNING %s: no REG lines (see RESULT line in the log)' % name, file=sys.stderr)
    table = {off: (flags, reset, n) for off, flags, reset, n in init.gaia_table()}
    offs = sorted(set(table) | {o for _, r in logs for o in r})
    print('%-4s %-26s %-2s %-9s %s' % ('off', 'register', 'fl', 'reset',
                                       ' '.join('%-10s' % n[:10] for n, _ in logs)))
    for off in offs:
        flags, reset, name = table.get(off, (0, 0, '?'))
        vals = [r.get(off) for _, r in logs]
        shown = ['%08x  ' % v[0] if v else '--------  ' for v in vals]
        for v, s in zip(vals, range(len(shown))):
            if v and v[1] != v[0]:
                shown[s] = shown[s][:8] + '!'
        diff = len({v[0] for v in vals if v}) > 1
        nondefault = any(v and v[0] != reset for v in vals)
        mark = (' *' if diff else '') + (' W' if flags == 2 and nondefault else '')
        print('%03x  %-26s %-2d %08x  %s%s' % (off, name[:26], flags, reset, ''.join(shown), mark))
    print('\n* = differs between logs, W = write-only and not at its reset value, '
          '! = pending value differs from last written')


if __name__ == '__main__':
    main()
