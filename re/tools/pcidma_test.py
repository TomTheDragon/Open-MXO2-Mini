#!/usr/bin/env python3
"""First PCI DMA test: copy one captured frame from on-board memory into host RAM.

Format (MVKAVBUS FUN_0003f800 = GTfrCtl submit, MVKTRANSFER FUN_00019fe0 = SG builder, see NOTES.md):
  PciDmaFrame 0x304   = on-board address (& 0x3fffffff, 32-byte aligned)
  PciDmaSGAddress     = host physical address of the SG list (lower 0x308 / upper 0x30c)
  PciDmaSGCount 0x310 = SG list size in dwords (entries * 3), max 0x1fff
  PciDmaPitchRes 0x314 = pitch << 16 | width (Windows: 0x3c0 for a 1080p frame, no pitch)
  PciDmaCommand 0x300 = flags, then flags | 0x80000000 (go). Windows capture: 0x90000000.
  SG entry = 12 packed bytes: u64 physical address, u32 length; each length < 0x20000 (split at 0x1f000).
Memory: 2 MiB huge pages (physically contiguous; no IOMMU on this machine): one for the SG list,
two for the frame. The rest of the first 64 KiB of the SG page is padded with entries pointing at a
trash area inside the same huge page, so a misread count can only write into our own memory.
Steps: capture a camera frame into on-board 0 (WDMA, as before) and stop; read 64 KiB + one word per
4 KiB of it through BAR1 (ground truth); bus mastering on; DMA it to host; compare; save
notes/pcidma-frame.raw; then time 10 more DMAs. Bus mastering is restored at the end.
Run as root, mxo2 unloaded, HDMI source on, huge pages reserved (run-pcidma.sh does that):
    sudo python3 -I pcidma_test.py [ad9398_mode] [shadow_log]
"""
import ctypes
import mmap
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ad9398 import RX, load_mode  # noqa: E402
from gaia import Gaia  # noqa: E402
import init  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import INPUT, SYSTEM, VIN_CONTROL, WDMA_ADDR, WDMA_CTL, WDMA_TS1  # noqa: E402

MEM_TARGET = 0x028
INT_STATUS0 = 0x080
DMA_CMD, DMA_FRAME, DMA_SGLO, DMA_SGHI, DMA_SGCNT, DMA_PITCH = 0x300, 0x304, 0x308, 0x30c, 0x310, 0x314
DMA_CMDCNT, DMA_FIFO = 0x318, 0x31c
HUGE = 2 << 20
MAP_HUGETLB = 0x40000
FRAME = 1920 * 1080 * 2
CHUNK = 0x1f000
TRASH_OFF, TRASH_LEN = 1 << 20, 0x1000
PAD_BYTES = 64 << 10
FILL = 0xa5


def huge_map(n):
    mm = mmap.mmap(-1, n * HUGE, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS | MAP_HUGETLB,
                   mmap.PROT_READ | mmap.PROT_WRITE)
    for i in range(n):                      # fault every huge page in
        mm[i * HUGE] = 0
    return mm


def phys(mm, offset):
    """Physical address of mm[offset] via /proc/self/pagemap (root needed for the PFN)."""
    virt = ctypes.addressof(ctypes.c_char.from_buffer(mm, offset))
    with open('/proc/self/pagemap', 'rb') as f:
        f.seek((virt // 4096) * 8)
        entry = struct.unpack('<Q', f.read(8))[0]
    if not entry >> 63 & 1 or entry & ((1 << 55) - 1) == 0:
        raise RuntimeError('page not present / no PFN (entry %016x)' % entry)
    return (entry & ((1 << 55) - 1)) * 4096 + virt % 4096


def build_sg(sg, frame_mm):
    entries = []
    for page in range(len(frame_mm) // HUGE):
        base = phys(frame_mm, page * HUGE)
        for off in range(0, HUGE, CHUNK):
            if page * HUGE + off >= FRAME:
                break
            length = min(CHUNK, HUGE - off, FRAME - page * HUGE - off)
            entries.append((base + off, length))
    trash = phys(sg, TRASH_OFF)
    pad = struct.pack('<QI', trash, TRASH_LEN)
    data = b''.join(struct.pack('<QI', a, n) for a, n in entries)
    data += pad * ((PAD_BYTES - len(data)) // 12)
    sg[:len(data)] = data
    assert sum(n for _, n in entries) == FRAME
    return entries, phys(sg, 0)


def main():
    rx_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    log = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, '..', 'notes', 'win', '2-preview.txt')
    shadow = load_shadow(log)
    sg = huge_map(1)
    frame_mm = huge_map(2)
    g = Gaia(write=True)
    fd = os.open(g.sys + '/resource1', os.O_RDONLY | os.O_SYNC)
    bar_mm = mmap.mmap(fd, 8 << 20, mmap.MAP_SHARED, mmap.PROT_READ)
    bar = memoryview(bar_mm).cast('I')
    cmd_before = g._cmd()
    try:
        entries, sg_phys = build_sg(sg, frame_mm)
        print('SG list at phys %#x, %d entries (SGCount %#x dwords), frame pages %s' % (
            sg_phys, len(entries), len(entries) * 3,
            ' '.join('%#x' % phys(frame_mm, i * HUGE) for i in range(2))), flush=True)

        for off, flags, reset, _ in init.gaia_table():
            if flags in (2, 3):
                g.wr(off, reset)
        bus = I2C(g, *BUSES['I2CCtl'])
        load_mode(bus, 0)
        load_mode(bus, rx_mode)
        g.wr(VIN_CONTROL, 0xd02)
        for off in SYSTEM + INPUT:
            g.wr(off, shadow[off][0])
        g.wr(WDMA_CTL, 0)
        g.wr(MEM_TARGET, 0)
        ctl = shadow[WDMA_CTL][0]
        print('AD9398 STATUS1=%02x STATUS3=%02x' % (bus.read(RX, 0x2f)[0], bus.read(RX, 0x81)[0]),
              flush=True)
        ts0 = g.rd(WDMA_TS1)
        for _ in range(4):
            g.wr(WDMA_ADDR, 0)
            g.wr(WDMA_CTL, ctl)
            time.sleep(1 / 30)
        time.sleep(0.05)
        print('frame captured into on-board 0: %s' % ('yes' if g.rd(WDMA_TS1) != ts0 else 'NO'), flush=True)

        head = [bar[i] for i in range(16384)]                       # first 64 KiB
        samples = [bar[i] for i in range(0, FRAME // 4, 1024)]       # one word per 4 KiB

        frame_mm[:FRAME] = bytes([FILL]) * FRAME
        g._set_cmd(cmd_before | 0x6)                                 # memory + bus master
        print('PCI command %04x → %04x; DMA regs before: CmdCount %08x FifoCount %08x Int0 %08x' % (
            cmd_before, g._cmd(), g.rd(DMA_CMDCNT), g.rd(DMA_FIFO), g.rd(INT_STATUS0)), flush=True)

        def dma():
            g.wr(DMA_FRAME, 0)
            g.wr(DMA_SGLO, sg_phys & 0xffffffff)
            g.wr(DMA_SGHI, sg_phys >> 32)
            g.wr(DMA_SGCNT, len(entries) * 3)
            g.wr(DMA_PITCH, 0x3c0)
            g.wr(DMA_CMD, 0x10000000)
            g.wr(DMA_CMD, 0x90000000)

        t0 = time.monotonic()
        dma()
        last = FRAME - 4
        while time.monotonic() - t0 < 1.0 and frame_mm[last:last + 4] == bytes([FILL]) * 4:
            time.sleep(0.0005)
        dt = time.monotonic() - t0
        time.sleep(0.05)
        host = memoryview(frame_mm)[:FRAME].cast('I')
        untouched = bytes(frame_mm[:FRAME]).count(bytes([FILL]) * 64)
        head_ok = sum(host[i] == head[i] for i in range(16384))
        samp_ok = sum(host[i * 1024] == v for i, v in enumerate(samples))
        trash_used = any(b != 0 for b in sg[TRASH_OFF:TRASH_OFF + TRASH_LEN])
        print('DMA: last word %s after %.1f ms | CmdCount %08x FifoCount %08x Int0 %08x' % (
            'written' if frame_mm[last:last + 4] != bytes([FILL]) * 4 else 'NOT written', dt * 1000,
            g.rd(DMA_CMDCNT), g.rd(DMA_FIFO), g.rd(INT_STATUS0)), flush=True)
        print('compare with BAR1: first 64 KiB %d/16384 words equal, samples %d/%d equal; '
              '64-byte runs still 0x%02x: %d; trash area %s' % (
                  head_ok, samp_ok, len(samples), FILL, untouched,
                  'WRITTEN (count misread?)' if trash_used else 'untouched'), flush=True)
        host.release()
        with open(os.path.join(HERE, '..', 'notes', 'pcidma-frame.raw'), 'wb') as f:
            f.write(frame_mm[:FRAME])

        if frame_mm[last:last + 4] != bytes([FILL]) * 4:
            times = []
            for _ in range(10):
                frame_mm[last:last + 4] = bytes([FILL]) * 4
                t0 = time.monotonic()
                dma()
                while time.monotonic() - t0 < 1.0 and frame_mm[last:last + 4] == bytes([FILL]) * 4:
                    pass
                times.append(time.monotonic() - t0)
            avg = sum(times) / len(times)
            print('10 more DMAs: %.2f ms average → %.0f MB/s, %.0f frames/s max' % (
                avg * 1000, FRAME / avg / 1e6, 1 / avg), flush=True)
    finally:
        g.wr(DMA_CMD, 0)
        g.wr(WDMA_CTL, 0)
        g.wr(VIN_CONTROL, 0xd00)
        time.sleep(0.05)
        g._set_cmd(cmd_before)
        bar.release()
        bar_mm.close()
        os.close(fd)
        g.close()


if __name__ == '__main__':
    main()
