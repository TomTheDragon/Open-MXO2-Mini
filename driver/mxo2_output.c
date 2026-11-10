// SPDX-License-Identifier: GPL-2.0
/*
 * Matrox MXO2 - video outputs: pass-through of the selected input to HDMI OUT (AD9889B HDMI transmitter,
 * I2C 0x72) and the analog OUT (ADV7341 encoder, I2C 0x54), as the Windows driver does while its capture
 * software is open (re/NOTES.md "Video outputs").
 * The capture thread writes every frame / field into on-board slots; after each one the output read DMA
 * (VideoRDma2) is pointed at it, with a control word that mirrors the capture's (0x20000000 | capture control
 * | line pitch for analog). Chips: the per-format tables of the Windows driver (output_modes.h); FPGA: the
 * output registers Windows programmed. Known for NTSC and PAL analog inputs and HDMI 1080p30 so far; other
 * formats leave the outputs dark.
 * With `output=1` (default) the capture runs whenever the selected input has a supported signal, so the
 * outputs show it also while no app captures.
 */

#include <linux/delay.h>
#include <linux/module.h>

#include "mxo2.h"
#include "gaia_regs.h"
#include "output_modes.h"

static int output = 1;
module_param(output, int, 0644);
MODULE_PARM_DESC(output, "Video outputs (HDMI OUT, analog OUT): 0 = off, 1 = show the selected input (default; "
		 "NTSC / PAL analog inputs and HDMI 1080p30 so far)");

static int output_analog;
module_param(output_analog, int, 0644);
MODULE_PARM_DESC(output_analog, "Analog OUT for SD formats: 0 = composite + S-video (default), 1 = component YPbPr "
		 "(HD formats are always component); applied at the next capture start");

static uint output_rdma;
module_param(output_rdma, uint, 0644);
MODULE_PARM_DESC(output_rdma, "Experiment: VideoRDma2Control value to use instead of the computed one (0 = auto)");

#define TX_ADDR  0x72
#define ENC_ADDR 0x54
#define TX_POWER      0x41      /* bit 6 = power down */
#define TX_STATUS     0x42      /* bit 6 = hot plug (TV connected), bit 5 = receiver sense */
#define TX_HPD        BIT(6)
#define TX_POWER_DOWN BIT(6)
#define ENC_POWER     0x00

#define RDMA2_CONTROL 0x400
#define RDMA2_ADDRESS 0x408
#define RDMA_OFF      0x20000000        /* Windows idle */
#define RDMA_BASE     0x20000000

enum { OUT_NTSC, OUT_PAL, OUT_1080P30, OUT_FORMATS };
static const char *const out_names[OUT_FORMATS] = { "NTSC 480i", "PAL 576i", "1080p30" };
static const u8 tx_mode[OUT_FORMATS] = { 1, 3, 11 };
/*
 * ADV7341 modes per format: [0] composite + S-video, [1] component. Datasheet ADV7342/7343 table 37: SD modes 0 / 6
 * route DAC1 = CVBS, DAC2/3 = S-video Y/C, DAC4-6 = YPbPr (0x02 bit 5 = 1, 0x82 = 0x4a); modes 1 / 7 put YPbPr on
 * DAC1-3 and switch the colour burst off (0x84 bit 5) - composite then has no colour (re/notes/vout-probe2.txt).
 * Windows switches all six DACs on (0x00 = 0xfc) after modes 0 and 6 (FUN_0001d698).
 */
static const u8 enc_mode[OUT_FORMATS][2] = { { 0, 1 }, { 6, 7 }, { 23, 23 } };

/*
 * FPGA output registers as Windows programmed them during preview with an NTSC composite, PAL composite and
 * HDMI 1080p30 input (re/notes/win/{analog,pal,.}/2-preview.txt). VideoRDma2Control / Address are set per frame.
 */
static const struct { u16 off; u32 val[OUT_FORMATS]; } out_regs[] = {
	{ 0x380, { 0x20000000, 0x20000000, 0x20000000 } },	/* VideoRDma1Control */
	{ 0x384, { 0x00000101, 0x00000111, 0x00000041 } },	/* VideoRDma1Control2 */
	{ 0x388, { 0x00000000, 0x00000000, 0x00000000 } },	/* VideoRDma1Address */
	{ 0x390, { 0x82008200, 0x82008200, 0x82008200 } },	/* VideoRDma1Chroma */
	{ 0x394, { 0x80008040, 0x80008040, 0x80008040 } },	/* VideoRDma1YA */
	{ 0x398, { 0x00000000, 0x00000000, 0x00000000 } },	/* VideoRDma1Resolution */
	{ 0x404, { 0x00000101, 0x00000111, 0x00000041 } },	/* VideoRDma2Control2 */
	{ 0x410, { 0x00000000, 0x00000000, 0x00000000 } },	/* VideoRDma2Chroma */
	{ 0x414, { 0x00000000, 0x00000000, 0x00000000 } },	/* VideoRDma2YA */
	{ 0x700, { 0x00000000, 0x00000000, 0x00000000 } },	/* Video1VHActive */
	{ 0x704, { 0x00000000, 0x00000000, 0x00000000 } },	/* Video1VHBorder */
	{ 0x708, { 0x00000000, 0x00000000, 0x00000000 } },	/* Video1VHResolution */
	{ 0x70c, { 0x00000000, 0x00000000, 0x00000000 } },	/* Video1VHPosition */
	{ 0x710, { 0x00000000, 0x00000000, 0x00000000 } },	/* Video1VHTotal */
	{ 0x714, { 0x00000000, 0x00000000, 0x00000000 } },	/* Video1VHSyncLength */
	{ 0x800, { 0x00001303, 0x00001303, 0x00001303 } },	/* VoutControl */
	{ 0x804, { 0x00000002, 0x00000002, 0x00000004 } },	/* VoutControlHDMI */
	{ 0x810, { 0x80000000, 0x80000000, 0x80100000 } },	/* VAnalogVoutControl */
	{ 0x814, { 0x00000040, 0x00000030, 0x08980028 } },	/* VAnalogVoutCRTC0 */
	{ 0x818, { 0x00000040, 0x00000040, 0x0000002c } },	/* VAnalogVoutCRTC1 */
	{ 0x81c, { 0x00000000, 0x00000000, 0x00000840 } },	/* VAnalogVoutCRTC2 */
	{ 0x820, { 0x44030000, 0x33020000, 0x05050084 } },	/* VAnalogVoutCRTC3 */
	{ 0x824, { 0x201fffff, 0x201fffff, 0x201fffff } },	/* BramLutRed */
	{ 0x828, { 0x001fffff, 0x001fffff, 0x001fffff } },	/* BramLutGreen */
	{ 0x82c, { 0x001fffff, 0x001fffff, 0x001fffff } },	/* BramLutBlue */
	{ 0x830, { 0x04000040, 0x04000040, 0x04000040 } },	/* HDMIProcAmpGainOffset */
	{ 0x834, { 0x0000e200, 0x00003a00, 0x0000fa00 } },	/* HDMIProcAmpHueSatAB */
	{ 0x838, { 0x0200e000, 0x02003800, 0x0200f800 } },	/* HDMIProcAmpHueSatCD */
	{ 0x840, { 0x00000003, 0x00000003, 0x00000003 } },	/* Vout1ClippersControl */
	{ 0x844, { 0x00000003, 0x00000003, 0x00000003 } },	/* Vout2ClippersControl */
	{ 0x850, { 0x00a00024, 0x00940016, 0x00820056 } },	/* HDMIHsyncControl */
	{ 0x854, { 0x00070004, 0x00060003, 0x000a0005 } },	/* HDMIVsyncControl */
	{ 0x858, { 0x0000037e, 0x00000377, 0x0000075e } },	/* HDMIHalfLine */
};

/* Output format for the running capture, or -1 = not supported (yet) */
static int out_format(struct mxo2 *m)
{
	if (m->cap_analog)
		return m->cap_pal ? OUT_PAL : OUT_NTSC;
	if (m->in_width == 1920 && m->in_height == 1080 && !m->in_interlaced && m->in_fps == 30)
		return OUT_1080P30;
	return -1;
}

bool mxo2_output_wanted(struct mxo2 *m)
{
	if (!READ_ONCE(output) || !m->in_width)
		return false;
	if (m->input != MXO2_INPUT_HDMI)
		return true;
	return m->in_width == 1920 && m->in_height == 1080 && !m->in_interlaced && m->in_fps == 30;
}

static int write_table(struct mxo2 *m, u8 dev, const u8 (*t)[2], size_t n)
{
	size_t i;
	int ret = 0;

	for (i = 0; i < n && !ret; i++)
		ret = mxo2_i2c_write(m, dev, t[i][0], t[i][1]);
	return ret;
}

static int tx_rmw(struct mxo2 *m, u8 reg, u8 clear, u8 set)
{
	u8 v;
	int ret = mxo2_i2c_read(m, TX_ADDR, reg, &v);

	return ret ? ret : mxo2_i2c_write(m, TX_ADDR, reg, (v & ~clear) | set);
}

/*
 * The mode tables leave the transmitter sending YCbCr while its AVI info frame says RGB (green tint on the TV,
 * re/notes/vout-probe.txt). Switch to RGB output with the colour converter on, as Windows FUN_000197e8 does for
 * RGB: 0xa1 = 0x2c around it, 0x3b bit 0 = CSC on, 0x16 bits 7:6 = RGB out, AVI 0x45 bits 5:4 = 0. rx_lock held.
 */
static int tx_rgb(struct mxo2 *m)
{
	int ret = mxo2_i2c_write(m, TX_ADDR, 0xa1, 0x2c);

	if (!ret)
		ret = tx_rmw(m, 0x3b, 0, 0x01);
	if (!ret)
		ret = tx_rmw(m, 0x16, 0xc0, 0);
	if (!ret)
		ret = tx_rmw(m, 0x45, 0x30, 0);
	if (!ret)
		ret = mxo2_i2c_write(m, TX_ADDR, 0xa1, 0x00);
	return ret;
}

/* HDMI transmitter on only with a TV connected (Windows FUN_000195b4). rx_lock held. */
static void tx_power_by_hpd(struct mxo2 *m, bool log)
{
	u8 st, pwr;
	bool hpd;

	if (mxo2_i2c_read(m, TX_ADDR, TX_STATUS, &st) || mxo2_i2c_read(m, TX_ADDR, TX_POWER, &pwr))
		return;
	hpd = st & TX_HPD;
	mxo2_i2c_write(m, TX_ADDR, TX_POWER, hpd ? pwr & ~TX_POWER_DOWN : pwr | TX_POWER_DOWN);
	if (log || hpd != m->out_hpd)
		dev_info(&m->pdev->dev, "HDMI OUT: %s\n", hpd ? "TV connected" : "no TV connected");
	m->out_hpd = hpd;
}

/* Capture thread starting (cap_lock held): set the outputs up for its format. */
void mxo2_output_start(struct mxo2 *m)
{
	size_t i;
	int f, em, ret;

	m->out_format = -1;
	mxo2_wr(m, RDMA2_CONTROL, RDMA_OFF);
	if (!READ_ONCE(output))
		return;
	f = out_format(m);
	if (f < 0) {
		dev_info(&m->pdev->dev, "outputs: no pass-through for %ux%u%s @ %u fps yet\n", m->in_width,
			 m->in_height, m->in_interlaced ? "i" : "p", m->in_fps);
		return;
	}
	em = enc_mode[f][READ_ONCE(output_analog) ? 1 : 0];
	mutex_lock(&m->rx_lock);
	ret = write_table(m, ENC_ADDR, adv7341_modes[0], ARRAY_SIZE(adv7341_modes[0]));
	if (!ret)
		ret = write_table(m, ENC_ADDR, adv7341_modes[em], ARRAY_SIZE(adv7341_modes[0]));
	if (!ret && (em == 0 || em == 6))
		ret = mxo2_i2c_write(m, ENC_ADDR, ENC_POWER, 0xfc);
	if (!ret)
		ret = write_table(m, TX_ADDR, ad9889b_modes[0], ARRAY_SIZE(ad9889b_modes[0]));
	if (!ret)
		ret = write_table(m, TX_ADDR, ad9889b_modes[tx_mode[f]], ARRAY_SIZE(ad9889b_modes[0]));
	if (!ret)
		ret = tx_rgb(m);
	if (!ret)
		tx_power_by_hpd(m, true);
	mutex_unlock(&m->rx_lock);
	if (ret) {
		dev_err(&m->pdev->dev, "outputs: chip setup failed (%d)\n", ret);
		return;
	}
	for (i = 0; i < ARRAY_SIZE(out_regs); i++)
		mxo2_wr(m, out_regs[i].off, out_regs[i].val[f]);
	m->out_format = f;
	dev_info(&m->pdev->dev, "outputs: showing the %s input as %s\n", mxo2_input_names[m->input], out_names[f]);
}

/* Capture thread stopped: read DMA off (outputs black). */
void mxo2_output_stop(struct mxo2 *m)
{
	mxo2_wr(m, RDMA2_CONTROL, RDMA_OFF);
	m->out_format = -1;
}

/*
 * Capture thread: a frame (progressive) or field (interlaced) landed in `slot`, written with capture control
 * word `wdma_ctl`. Show it.
 */
void mxo2_output_frame(struct mxo2 *m, unsigned int slot, u32 wdma_ctl)
{
	u32 ctl = READ_ONCE(output_rdma);

	if (m->out_format < 0)
		return;
	if (!READ_ONCE(output)) {
		mxo2_output_stop(m);
		return;
	}
	if (!ctl)
		ctl = RDMA_BASE | (wdma_ctl & 0xff000000) | (m->cap_analog ? (m->cap_line_bytes / 4) & 0x7ff : 0);
	mxo2_wr(m, RDMA2_ADDRESS, (slot * MXO2_FRAME_BYTES) & 0x3fffffff);
	mxo2_wr(m, RDMA2_CONTROL, ctl);
}

/* Monitor (rx_lock held, every 500 ms): follow the TV being plugged in / out. */
void mxo2_output_monitor_locked(struct mxo2 *m)
{
	u8 st;

	if (m->out_format < 0 || mxo2_i2c_read(m, TX_ADDR, TX_STATUS, &st))
		return;
	if (!!(st & TX_HPD) == m->out_hpd)
		return;
	if ((st & TX_HPD) &&    /* a TV that was just plugged in gets the whole setup again */
	    !write_table(m, TX_ADDR, ad9889b_modes[tx_mode[m->out_format]], ARRAY_SIZE(ad9889b_modes[0])))
		tx_rgb(m);
	tx_power_by_hpd(m, false);
}
