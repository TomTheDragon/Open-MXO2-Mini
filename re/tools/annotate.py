#!/usr/bin/env python3
"""Annotate decompiled C: register lookups `(*this->vt[0x20])(this, N)` get the Gaia name."""
import re, sys
names = {}
tab = False
for l in open('notes/MVKAVBUS.regmap.txt'):
    if l.startswith('#'): tab = (sys.argv[3] if len(sys.argv) > 3 else '0x4e730') in l; continue
    if tab:
        m = re.search(r'(0x[0-9a-f]+).*idx=\s*(\d+)\s+(\S+)', l); names[int(m.group(2))] = (m.group(3), m.group(1))
rx = re.compile(r'\+ 0x20\)\)\s*\(([^,()]+(?:\([^()]*\))?[^,()]*),\s*(0x[0-9a-f]+|\d+)\)')
def sub(m):
    n = int(m.group(2), 0)
    if n in names:
        return m.group(0) + ' /*%s@%s*/' % names[n]
    return m.group(0)
src, dst = sys.argv[1], sys.argv[2]
with open(dst, 'w') as w:
    for line in open(src):
        w.write(rx.sub(sub, line))
