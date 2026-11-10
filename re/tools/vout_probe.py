#!/usr/bin/env python3
"""Output probe 1: bring up HDMI OUT (AD9889B) and the analog OUT (ADV7341) with an NTSC composite picture.

Everything as Windows does it for an NTSC composite input (re/NOTES.md "Video outputs"):
- chips: the per-mode tables from MVKAVCIO (tools/output_modes.py): ADV7341 mode 0 then 1 (480 lines),
  AD9889B mode 0 then 1 (480i); AD9889B power-down bit (0x41 bit 6) cleared when a TV is plugged in (0x42 bit 6,
  FUN_000195b4);
- FPGA: the output registers from notes/win/analog/2-preview.txt (VoutControl, HDMI sync, analog CRTC, LUTs,
  clippers, proc amp, VideoRDma1/2 Control2/Chroma/YA);
- picture: VideoRDma2 reads a woven 720 x 486 frame that the capture wrote into card memory.
Steps (each held while you look at the TV, then you type what you saw):
  1 chips + FPGA set up, read DMA off      2 still frame, Control written once
  3 still frame, Control re-written every field (bit 24 alternating as in the Windows dumps)
  4 live: capture into 3 slots, the output reads the newest
Run as root, mxo2 unloaded, NTSC composite source playing, TV on HDMI OUT and/or composite OUT:
  sudo sh run-vout-probe.sh
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from analog_audio_probe import setup, teardown  # noqa: E402
from analog_probe5 import grab  # noqa: E402
from i2c import BUSES, I2C  # noqa: E402
from output_modes import ENC_MODES, TX_MODES  # noqa: E402
from regshadow import load as load_shadow  # noqa: E402
from wdma_replay import WDMA_CTL2  # noqa: E402

TX, ENC = 0x72, 0x54
RDMA2_CTL, RDMA2_CTL2, RDMA2_ADDR = 0x400, 0x404, 0x408
VIDEO2_CRC, RDMA2_STATUS = 0x064, 0x92c
INT_STATUS0 = 0x080
# output registers Windows wrote (VideoRDma1/2 without the RDma2 Control/Address, Video1VH*, Vout*)
OUTPUT_REGS = [o for o in range(0x380, 0x420, 4) if o not in (RDMA2_CTL, RDMA2_ADDR)] + \
    list(range(0x700, 0x718, 4)) + list(range(0x800, 0x85c, 4))
SLOT = 0x3f4800                                  # ring stride (Windows and the driver)
BASE = 0x400000
RDMA_ON = 0xa0000168                             # Windows analog preview: enable, 360 dwords per line
FIELD_BIT = 1 << 24


def load_table(bus, dev, table):
    for reg, val in table:
        bus.write(dev, reg, val)


def tx_status(bus):
    st = bus.read(TX, 0x42)[0]
    return st, 'TV %s, rx sense %s' % ('plugged in' if st & 0x40 else 'NOT detected', 'yes' if st & 0x20 else 'no')


def snapshot(g, bus, label):
    crcs = []
    for _ in range(4):
        crcs.append(g.rd(VIDEO2_CRC))
        time.sleep(0.02)
    st, txt = tx_status(bus)
    print('[%s] AD9889B 0x41 %02x 0x42 %02x (%s), ADV7341 0x00 %02x 0x02 %02x; Video2Crc16 %s, RDma2 status %08x' % (
        label, bus.read(TX, 0x41)[0], st, txt, bus.read(ENC, 0x00)[0], bus.read(ENC, 0x02)[0],
        ' '.join('%04x' % c for c in crcs), g.rd(RDMA2_STATUS)), flush=True)


def ask(step, what):
    print('\n>>> Step %s: %s' % (step, what), flush=True)
    print('    Look at the TV(s) now (HDMI OUT and composite OUT).', flush=True)
    try:
        note = input('    What do they show? (e.g. "hdmi: no signal, composite: black") > ')
    except EOFError:
        note = '(no answer)'
    print('NOTE step %s: %s' % (step, note), flush=True)


def hold(g, seconds, toggle):
    """Keep the read DMA running; toggle = re-write Control every field with bit 24 alternating."""
    t_end = time.monotonic() + seconds
    field = 0
    while time.monotonic() < t_end:
        if toggle:
            g.wr(RDMA2_CTL, RDMA_ON | (FIELD_BIT if field else 0))
            field ^= 1
        time.sleep(1 / 59.94)


def main():
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', 'analog', '2-preview.txt'))
    g, bar, mm, fd = setup(0)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        snapshot(g, bus, 'before')

        # 1: chips and FPGA output path as Windows, read DMA off
        load_table(bus, ENC, ENC_MODES[0])
        load_table(bus, ENC, ENC_MODES[1])
        load_table(bus, TX, TX_MODES[0])
        load_table(bus, TX, TX_MODES[1])
        st, txt = tx_status(bus)
        v = bus.read(TX, 0x41)[0]
        bus.write(TX, 0x41, v & ~0x40 if st & 0x40 else v | 0x40)
        print('AD9889B: %s -> power %s' % (txt, 'up' if st & 0x40 else 'down'), flush=True)
        for o in OUTPUT_REGS:
            if o in shadow:
                g.wr(o, shadow[o][0])
        g.wr(RDMA2_CTL, 0x20000000)
        time.sleep(1)
        snapshot(g, bus, 'step 1')
        ask(1, 'output chips and FPGA output set up as Windows (NTSC), no picture source yet')

        # still frame: weave one frame from the composite input into card memory
        g.wr(WDMA_CTL2, shadow.get(WDMA_CTL2, (0x01680101,))[0])
        ok = grab(g, BASE, 0x80000000, n=1) and grab(g, BASE, 0x81000000, n=1)
        print('captured a frame at %#x: %s' % (BASE, 'ok' if ok else 'TIMEOUT (no composite signal?)'), flush=True)

        # 2: read DMA on, Control written once
        g.wr(RDMA2_ADDR, BASE)
        g.wr(RDMA2_CTL, RDMA_ON)
        time.sleep(1)
        snapshot(g, bus, 'step 2')
        hold(g, 2, False)
        ask(2, 'still frame from the composite input, read DMA started once')

        # 3: Control re-written every field
        hold(g, 1, True)
        snapshot(g, bus, 'step 3')
        print('    (holding the frame for 10 s while you look)', flush=True)
        hold(g, 10, True)
        ask(3, 'same still frame, read DMA re-armed every field')

        # 4: live: capture slot k, show the previous one
        print('    live for 15 s ...', flush=True)
        t_end = time.monotonic() + 15
        k, frames, field = 0, 0, 0
        while time.monotonic() < t_end:
            addr = BASE + k * SLOT
            if not (grab(g, addr, 0x80000000, n=1) and grab(g, addr, 0x81000000, n=1)):
                print('capture timeout', flush=True)
                break
            g.wr(RDMA2_ADDR, addr)
            g.wr(RDMA2_CTL, RDMA_ON | (FIELD_BIT if field else 0))
            field ^= 1
            k = (k + 1) % 3
            frames += 1
        print('live: %d frames' % frames, flush=True)
        snapshot(g, bus, 'step 4')
        ask(4, 'live composite picture (it ran for the last 15 s; describe what you saw then)')
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
