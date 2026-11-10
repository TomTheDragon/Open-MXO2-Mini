#!/usr/bin/env python3
"""Output probe 2: colours. Probe 1 (notes/vout-probe.txt) showed the composite picture on both outputs, but
HDMI OUT green-tinted and composite OUT without colour.

Same set-up as probe 1 step 2 (NTSC chips + FPGA as Windows, a still frame from the composite input), then:
  H1 HDMI RGB output: Windows FUN_000197e8 for "not YCbCr": 0xa1 = 0x2c, CSC on (0x3b bit 0), RGB output
     (0x16 bits 7:6 = 0), AVI 0x45 bits 5:4 = 0, 0xa1 = 0
  H2 HDMI YCbCr output (FUN_000197e8 with 3): CSC off, 0x16 |= 0xc0, AVI 0x45 bit 5 off, bit 4 on
     (the mode table itself has YCbCr out with the AVI saying RGB - the likely cause of the green tint)
  E0/E1/E2 analog encoder: the three NTSC tables Windows picks by its "analog output type" setting (mode 0,
     1 = default, 2) with all six DACs on (0x00 = 0xfc, as Windows does after mode 0). They differ in 0x02
     (YPbPr / RGB output select), 0x82 (SD DAC outputs) and 0x0b.
After each step you type what the TV shows. Ends with HDMI RGB and the analog mode you liked best.
Run as root, mxo2 unloaded, NTSC composite source playing:  sudo sh run-vout-probe2.sh
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
from vout_probe import BASE, ENC, OUTPUT_REGS, RDMA2_ADDR, RDMA2_CTL, RDMA_ON, TX, load_table, snapshot, tx_status  # noqa: E402
from wdma_replay import WDMA_CTL2  # noqa: E402


def ask(step, what, where):
    print('\n>>> Step %s: %s' % (step, what), flush=True)
    print('    Look at %s.' % where, flush=True)
    try:
        note = input('    What do you see (colours)? > ')
    except EOFError:
        note = '(no answer)'
    print('NOTE step %s: %s' % (step, note), flush=True)
    return note


def rmw(bus, reg, clear, set_):
    v = bus.read(TX, reg)[0]
    bus.write(TX, reg, (v & ~clear) | set_)


def tx_colour(bus, ycbcr):
    """Windows FUN_000197e8: YCbCr (3) or RGB output."""
    bus.write(TX, 0xa1, 0x2c)
    if ycbcr:
        rmw(bus, 0x3b, 0x01, 0)
        rmw(bus, 0x16, 0, 0xc0)
        rmw(bus, 0x45, 0x20, 0x10)
    else:
        rmw(bus, 0x3b, 0, 0x01)
        rmw(bus, 0x16, 0xc0, 0)
        rmw(bus, 0x45, 0x30, 0)
    bus.write(TX, 0xa1, 0x00)
    print('    AD9889B 0x16 %02x 0x3b %02x 0x45 %02x' % tuple(bus.read(TX, r)[0] for r in (0x16, 0x3b, 0x45)),
          flush=True)


def enc_mode(bus, mode):
    load_table(bus, ENC, ENC_MODES[mode])
    bus.write(ENC, 0x00, 0xfc)
    print('    ADV7341 mode %d: 0x00 %02x 0x02 %02x 0x0b %02x 0x82 %02x' % (
        mode, *(bus.read(ENC, r)[0] for r in (0x00, 0x02, 0x0b, 0x82))), flush=True)


def main():
    shadow = load_shadow(os.path.join(HERE, '..', 'notes', 'win', 'analog', '2-preview.txt'))
    g, bar, mm, fd = setup(0)
    try:
        bus = I2C(g, *BUSES['I2CCtl'])
        load_table(bus, ENC, ENC_MODES[0])
        load_table(bus, ENC, ENC_MODES[1])
        load_table(bus, TX, TX_MODES[0])
        load_table(bus, TX, TX_MODES[1])
        st, txt = tx_status(bus)
        v = bus.read(TX, 0x41)[0]
        bus.write(TX, 0x41, v & ~0x40 if st & 0x40 else v | 0x40)
        print('AD9889B: %s' % txt, flush=True)
        for o in OUTPUT_REGS:
            if o in shadow:
                g.wr(o, shadow[o][0])
        g.wr(WDMA_CTL2, shadow.get(WDMA_CTL2, (0x01680101,))[0])
        ok = grab(g, BASE, 0x80000000, n=1) and grab(g, BASE, 0x81000000, n=1)
        print('captured a frame: %s' % ('ok' if ok else 'TIMEOUT (no composite signal?)'), flush=True)
        g.wr(RDMA2_ADDR, BASE)
        g.wr(RDMA2_CTL, RDMA_ON)
        time.sleep(1)
        snapshot(g, bus, 'start')
        ask(0, 'same as probe 1 (still frame)', 'HDMI OUT and composite OUT')

        tx_colour(bus, False)
        ask('H1', 'HDMI OUT switched to RGB', 'HDMI OUT')
        tx_colour(bus, True)
        ask('H2', 'HDMI OUT switched to YCbCr (with matching info frame)', 'HDMI OUT')
        tx_colour(bus, False)

        for mode in (0, 1, 2):
            enc_mode(bus, mode)
            ask('E%d' % mode, 'analog encoder NTSC mode %d, all DACs on' % mode,
                'composite OUT (and S-video / component OUT if you have cables)')
        try:
            pick = int(input('\nWhich analog step looked best (0, 1 or 2)? > ').strip() or 1)
        except (EOFError, ValueError):
            pick = 1
        enc_mode(bus, pick if pick in (0, 1, 2) else 1)
        print('NOTE best analog mode: %d' % pick, flush=True)
        snapshot(g, bus, 'end')
    finally:
        teardown(g, bar, mm, fd)


if __name__ == '__main__':
    main()
