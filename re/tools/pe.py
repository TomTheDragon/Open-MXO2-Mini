#!/usr/bin/env python3
"""Minimal PE32+ helper for the Matrox .sys drivers (stdlib only).

Usage:
  pe.py info  <file>            sections, entry point, imports, exports
  pe.py dis   <file> [out.asm]  objdump disassembly with import names annotated
"""
import re
import struct
import subprocess
import sys


class PE:
    def __init__(self, path):
        self.path = path
        self.data = open(path, 'rb').read()
        d = self.data
        pe_off = struct.unpack_from('<I', d, 0x3c)[0]
        assert d[pe_off:pe_off + 4] == b'PE\0\0'
        coff = pe_off + 4
        self.nsec = struct.unpack_from('<H', d, coff + 2)[0]
        opt_size = struct.unpack_from('<H', d, coff + 16)[0]
        opt = coff + 20
        magic = struct.unpack_from('<H', d, opt)[0]
        assert magic == 0x20b, 'expected PE32+'
        self.entry = struct.unpack_from('<I', d, opt + 16)[0]
        self.base = struct.unpack_from('<Q', d, opt + 24)[0]
        ndirs = struct.unpack_from('<I', d, opt + 108)[0]
        self.dirs = [struct.unpack_from('<II', d, opt + 112 + 8 * i) for i in range(ndirs)]
        self.sections = []
        so = opt + opt_size
        for i in range(self.nsec):
            name = d[so:so + 8].rstrip(b'\0').decode('latin1')
            vsize, va, rsize, raw = struct.unpack_from('<IIII', d, so + 8)
            self.sections.append((name, va, vsize, raw, rsize))
            so += 40
        self.imports = self._imports()
        self.exports = self._exports()

    def rva2off(self, rva):
        for name, va, vs, raw, rs in self.sections:
            if va <= rva < va + max(vs, rs):
                return rva - va + raw
        return None

    def cstr(self, rva):
        o = self.rva2off(rva)
        e = self.data.index(b'\0', o)
        return self.data[o:e].decode('latin1')

    def u32(self, rva):
        return struct.unpack_from('<I', self.data, self.rva2off(rva))[0]

    def u64(self, rva):
        return struct.unpack_from('<Q', self.data, self.rva2off(rva))[0]

    def _imports(self):
        """Return {iat_va: 'dll!name'}."""
        res = {}
        rva, size = self.dirs[1]
        if not rva:
            return res
        while True:
            olt, _, _, name_rva, iat = struct.unpack_from('<IIIII', self.data, self.rva2off(rva))
            if not name_rva:
                break
            dll = self.cstr(name_rva)
            lookup = olt or iat
            i = 0
            while True:
                ent = self.u64(lookup + 8 * i)
                if not ent:
                    break
                if ent & (1 << 63):
                    fn = 'ord%d' % (ent & 0xffff)
                else:
                    fn = self.cstr((ent & 0x7fffffff) + 2)
                res[self.base + iat + 8 * i] = '%s!%s' % (dll, fn)
                i += 1
            rva += 20
        return res

    def _exports(self):
        res = {}
        rva, size = self.dirs[0]
        if not rva:
            return res
        o = self.rva2off(rva)
        (nfuncs, nnames, funcs, names, ords) = struct.unpack_from('<IIIII', self.data, o + 20)
        for i in range(nnames):
            nm = self.cstr(self.u32(names + 4 * i))
            ordi = struct.unpack_from('<H', self.data, self.rva2off(ords + 2 * i))[0]
            res[self.base + self.u32(funcs + 4 * ordi)] = nm
        return res


def info(pe):
    print('image base 0x%x  entry 0x%x' % (pe.base, pe.base + pe.entry))
    for s in pe.sections:
        print('  %-8s va=0x%06x vsize=0x%06x raw=0x%06x rsize=0x%06x' % s)
    print('imports:')
    for a, n in sorted(pe.imports.items()):
        print('  0x%x %s' % (a, n))
    if pe.exports:
        print('exports:')
        for a, n in sorted(pe.exports.items()):
            print('  0x%x %s' % (a, n))


def dis(pe, out):
    p = subprocess.run(['objdump', '-d', '-M', 'intel', '--no-show-raw-insn', pe.path],
                       capture_output=True, text=True)
    names = dict(pe.imports)
    names.update(pe.exports)
    names[pe.base + pe.entry] = 'ENTRY'
    rx = re.compile(r'(?:#|<)\s*0x([0-9a-f]+)')
    rx2 = re.compile(r'\b(?:call|jmp)\s+([0-9a-f]+) ')
    with open(out, 'w') as f:
        for line in p.stdout.splitlines():
            m = re.match(r'\s*([0-9a-f]+):', line)
            if m and int(m.group(1), 16) in names:
                f.write('\n;;;; %s\n' % names[int(m.group(1), 16)])
            for m in rx.finditer(line):
                a = int(m.group(1), 16)
                if a in names:
                    line += '    ; ' + names[a]
            m = rx2.search(line)
            if m and int(m.group(1), 16) in names:
                line += '    ; ' + names[int(m.group(1), 16)]
            f.write(line + '\n')


if __name__ == '__main__':
    cmd, path = sys.argv[1], sys.argv[2]
    pe = PE(path)
    if cmd == 'info':
        info(pe)
    elif cmd == 'dis':
        dis(pe, sys.argv[3] if len(sys.argv) > 3 else path + '.asm')
