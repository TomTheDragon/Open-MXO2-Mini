#!/usr/bin/env python3
"""Find pointer tables referencing register-name strings; dump neighbouring words."""
import struct, sys
sys.path.insert(0, __import__('os').path.dirname(__file__))
from pe import PE
pe = PE(sys.argv[1])
d = pe.data
names = {}
for s in pe.sections:
    if s[0] not in ('.rdata', '.data'): continue
    name, va, vs, raw, rs = s
    i = raw
    while i < raw + rs:
        j = i
        while j < raw + rs and 32 <= d[j] < 127: j += 1
        if j - i >= 5 and d[j] == 0:
            names[pe.base + va + (i - raw)] = d[i:j].decode()
            i = j + 1
        else:
            i += 1
refs = []
for s in pe.sections:
    if s[0] not in ('.rdata', '.data'): continue
    name, va, vs, raw, rs = s
    for o in range(raw, raw + rs - 8, 8):
        p = struct.unpack_from('<Q', d, o)[0]
        if p in names:
            refs.append((pe.base + va + o - raw, o, names[p]))
prev = None
for va, o, n in refs:
    if prev is not None and va - prev != int(sys.argv[2] if len(sys.argv)>2 else 0) and len(sys.argv) > 2:
        print('----')
    prev = va
    words = struct.unpack_from('<8I', d, o)
    print('0x%x %-28s %s' % (va, n, ' '.join('%08x' % w for w in words[2:8])))
