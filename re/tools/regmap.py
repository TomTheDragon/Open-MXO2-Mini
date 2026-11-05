#!/usr/bin/env python3
"""Extract Matrox register description tables: {u32 idx; u32 off; u32 reset; u32 flags; char *name} (0x18 bytes)."""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pe import PE

pe = PE(sys.argv[1])
d = pe.data
rdata = [s for s in pe.sections if s[0] in ('.rdata', '.data')]

def in_rdata(va):
    r = va - pe.base
    return any(s[1] <= r < s[1] + s[2] for s in rdata)

def s_at(va):
    try:
        x = pe.cstr(va - pe.base)
    except Exception:
        return None
    return x if x and all(32 <= ord(c) < 127 for c in x) else None

def entry(va):
    o = pe.rva2off(va - pe.base)
    idx, off, reset, flags, name = struct.unpack_from('<IIIIQ', d, o)
    if not in_rdata(name) or s_at(name) is None or flags & 0x0fffffff or off & 3:
        return None
    return s_at(name), idx, off, reset, flags >> 28

tables = []
for name, va, vs, raw, rs in rdata:
    a = pe.base + va
    end = a + vs - 0x18
    while a < end:
        e = entry(a)
        if e:
            t = [(a, e)]
            b = a + 0x18
            while b < end and entry(b):
                t.append((b, entry(b))); b += 0x18
            if len(t) >= 4:
                tables.append(t); a = b; continue
        a += 8

acc = {1: '1 ', 2: '2 ', 3: '3 '}  # meaning not yet known
for t in tables:
    print('# table @0x%x, %d entries' % (t[0][0], len(t)))
    for va, (n, idx, off, reset, fl) in t:
        print('  0x%04x  %-2s reset=0x%08x  idx=%3d  %s' % (off, acc.get(fl, '?%d' % fl), reset, idx, n))
