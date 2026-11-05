#!/usr/bin/env python3
"""Extract I2C chip register maps from decomp/MVKAVCIO.c.

Chip init functions build register objects in sequence:
  FUN_0001503c(obj, chip, ADDR, SIZE, "NAME")        read-only
  FUN_00015078(obj, chip, ADDR, SIZE, x, "NAME")     read/write
  FUN_00019a24(obj, chip, ADDR, SIZE, x, "NAME")     write-only (shares its address with a status register)
  FUN_00014f50(chip, IDX, obj)                       store as index IDX
Code later fetches them with chip->vt[0x20](chip, IDX).
Usage: chipregs.py decomp/MVKAVCIO.c > notes/MVKAVCIO.chipregs.txt
"""
import re
import sys

src = open(sys.argv[1]).read()
funcs = re.split(r'^// ==== (FUN_[0-9a-f]+) @ [0-9a-f]+$', src, flags=re.M)
N = r'(0x[0-9a-f]+|\d+)'
C = r'\s*,\s*'
ctor = re.compile(r'FUN_000(?:150(3c|78)|19a(24))\(\s*\w+' + C + 'param_1' + C + N + C + N + C +
                  r'[^"]*?"([^"]*)"\s*\)', re.S)
store = re.compile(r'FUN_00014f50\(\s*\(longlong\)param_1' + C + N + C)
for name, body in zip(funcs[1::2], funcs[2::2]):
    events = sorted([(m.start(), 'c', m) for m in ctor.finditer(body)] +
                    [(m.start(), 's', m) for m in store.finditer(body)], key=lambda e: e[0])
    if not any(e[1] == 'c' for e in events):
        continue
    print('# %s' % name)
    pending = None
    for _, kind, m in events:
        if kind == 'c':
            pending = m
        elif pending is not None:
            rw = 'WO' if pending[2] else 'RO' if pending[1] == '3c' else 'RW'
            print('  idx=0x%02x  reg=0x%03x  size=%d  %s  %s' % (
                int(m[1], 0), int(pending[3], 0), int(pending[4], 0), rw, pending[5]))
            pending = None
