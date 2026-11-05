"""Shared helpers for poking the Gaia FPGA from user space (needs root, mxo2 module unloaded)."""
import mmap
import os
import struct
import time

DEFAULT_DEV = '0000:02:00.0'

GPIO = 0x020
GPOENABLE = 0x024
INT_STATUS0 = 0x080
VIN_STATUS_ANALOG = 0x7c4
VIN_STATUS_HDMI = 0x7cc
SRI_ADDRESS = 0x880
SRI_DATA = 0x888
SRI_FIFO = 0x890


class Gaia:
    def __init__(self, dev=DEFAULT_DEV, write=False):
        self.sys = '/sys/bus/pci/devices/' + dev
        self.old_cmd = self._cmd()
        if not self.old_cmd & 2:
            self._set_cmd(self.old_cmd | 2)
        flags = os.O_RDWR if write else os.O_RDONLY
        prot = mmap.PROT_READ | (mmap.PROT_WRITE if write else 0)
        self.fd = os.open(self.sys + '/resource0', flags | os.O_SYNC)
        self.mm = mmap.mmap(self.fd, 0x1000, mmap.MAP_SHARED, prot)
        self.mv = memoryview(self.mm).cast('I')

    def _cmd(self):
        with open(self.sys + '/config', 'rb') as f:
            f.seek(4)
            return struct.unpack('<H', f.read(2))[0]

    def _set_cmd(self, v):
        with open(self.sys + '/config', 'r+b') as f:
            f.seek(4)
            f.write(struct.pack('<H', v))

    def rd(self, off):
        return self.mv[off // 4]

    def wr(self, off, val):
        self.mv[off // 4] = val & 0xffffffff

    def sri_read(self, bank, addr, timeout=0.002):
        """Return (value, None) or (None, error text)."""
        self.wr(SRI_ADDRESS, (bank & 1) << 10 | (addr & 0x3ff))
        t0 = time.monotonic()
        while True:
            v = self.rd(SRI_DATA)
            if v & (1 << 30):
                return None, 'err 0x%08x' % v
            if v & (1 << 31) and ((v >> 16) & 0x3ff) == addr:
                return v & 0xff, None
            if time.monotonic() - t0 > timeout:
                return None, 'timeout 0x%08x' % v

    def sri_wait_free(self, need=1, timeout=0.05):
        """Wait until the SRI FIFO has `need` free slots (SriFifoCount = free slots)."""
        t0 = time.monotonic()
        while self.rd(SRI_FIFO) < need:
            if time.monotonic() - t0 > timeout:
                raise TimeoutError('SRI FIFO stays full (0x%x)' % self.rd(SRI_FIFO))

    def sri_write(self, bank, addr, val):
        """FIFO-mode write as in MVKAVBUS FUN_00048898 mode 1."""
        self.sri_wait_free(1)
        self.wr(SRI_ADDRESS, (bank & 1) << 10 | (addr & 0x3ff))
        self.wr(SRI_DATA, val & 0xff)

    def close(self):
        self.mv.release()
        self.mm.close()
        os.close(self.fd)
        if not self.old_cmd & 2:
            self._set_cmd(self.old_cmd)
