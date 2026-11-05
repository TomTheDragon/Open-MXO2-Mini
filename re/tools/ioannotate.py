#!/usr/bin/env python3
"""Annotate Gaia I/O-object register fields (FUN_0002b2f8) in MVKAVBUS.c functions.

The I/O object (Gaia+0x328) stores register objects at fixed offsets. Methods get either the
object itself or its second interface (object+8), so a field X shows up as param_1 + X or X-8.
Usage: ioannotate.py decomp/MVKAVBUS.c SHIFT FUN_xxxx...  (SHIFT 0 = object, 8 = object+8) (prints the functions with /*name*/ added)
"""
import re
import sys

FIELDS = {0x190: 'VinStatusAnalog', 0x198: 'VinStatusHDMI', 0x180: 'GenlockFWControl',
          0x178: 'GenlockSourceControl', 0x1a0: 'GenlockStatus', 0x1a8: 'AudioInputCtl',
          0x1b0: 'AudioOutputCtl', 0x1b8: 'VinControl', 0x1c0: 'VoutControl', 0x188: 'Watchdog',
          0x1c8: 'VcxoDacValue', 0x1d0: 'MiscControl', 0x1d8: 'VinClippersControl',
          0x1e0: 'Vout1ClippersControl', 0x1e8: 'Vout2ClippersControl',
          0x1f0: 'VAnalogVoutControl', 0x1f8: 'VAnalogVoutCRTC0', 0x200: 'VAnalogVoutCRTC1',
          0x208: 'VAnalogVoutCRTC2', 0x210: 'VAnalogVoutCRTC3', 0x218: 'VinControlAnalog',
          0x220: 'HDMIProcAmpGainOffset', 0x228: 'HDMIProcAmpHueSatAB',
          0x230: 'HDMIProcAmpHueSatCD', 0x238: 'VoutControlHDMI', 0x240: 'HDMIHsyncControl',
          0x248: 'HDMIVsyncControl', 0x250: 'HDMIHalfLine', 0x258: 'PCICount',
          0x260: 'BramLutRed', 0x268: 'BramLutGreen', 0x270: 'BramLutBlue'}
src = open(sys.argv[1]).read()
shift = int(sys.argv[2], 0)
names = sys.argv[3:]
for name in names:
    m = re.search(r'^// ==== %s @ .*?(?=^// ==== )' % name, src, re.S | re.M)
    body = m.group(0)

    def sub(mm):
        off = int(mm.group(1), 0) + shift
        return mm.group(0) + ('/*%s*/' % FIELDS[off] if off in FIELDS else '')
    print(re.sub(r'param_1 \+ (0x[0-9a-f]+|\d+)\)', sub, body))
