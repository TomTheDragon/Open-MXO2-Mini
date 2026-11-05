#!/usr/bin/env python3
"""Join notes/probe.txt BAR0 values with the Gaia register map."""
import re, sys
probe = sys.argv[1] if len(sys.argv) > 1 else 'notes/probe.txt'
vals = {}
inbar0 = False
for l in open(probe):
    if l.startswith('# BAR0 raw'): inbar0 = True; continue
    if l.startswith('# BAR1'): inbar0 = False
    m = re.match(r'\s+(\w{4}): ((?:[0-9a-f]{8} ?){8})$', l)
    if inbar0 and m and int(m.group(1), 16) < 0x1000:
        for i, w in enumerate(m.group(2).split()):
            vals[int(m.group(1), 16) + 4 * i] = w
tab = False
for l in open('notes/MVKAVBUS.regmap.txt'):
    if l.startswith('#'): tab = '0x4e730' in l; continue
    if tab:
        f = l.split(); off = int(f[0], 16)
        v = vals.pop(off, '00000000')
        print('%s f%s reset=%s %-24s = %s' % (f[0], f[1], f[2][6:], f[-1], v))
for off, v in sorted(vals.items()):
    if v != '00000000': print('%04x unnamed = %s' % (off, v))
