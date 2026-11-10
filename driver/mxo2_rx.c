// SPDX-License-Identifier: GPL-2.0
/*
 * Matrox MXO2 - AD9398 HDMI receiver setup over the FPGA's SRI/I2C bridge.
 * Port of re/tools/{gaia,i2c,ad9398}.py (see re/NOTES.md, "SRI" and "MVKAVCIO" sections).
 *
 * SRI ("serial register interface", MVKAVBUS FUN_00048898 / FUN_00048aa8), bank 0 only:
 *   write: wait for a free FIFO slot (SriFifoCount), SriAddress = addr, SriData = byte
 *   read:  SriAddress = addr, poll SriData until bit 31 (valid) with the address echoed in
 *          bits 25:16; bit 30 = error
 * Small bus masters live behind SRI addresses (MVKAVCIO FUN_0003041c): the I2C bus with all
 * box chips has control = 3, data = 4. Control commands (FUN_0005cf2c / FUN_0005c99c):
 *   6, 7 = start; 5, 7 = stop; before each read byte 3 = ack or 7 = nack (last byte), then
 *   data = 0xff and read the data register once the FIFO is empty. There is no ACK check.
 *
 * AD9398 (I2C 0x98): mode 0 then the selected mode table, then (FUN_00014a5c) if HDMI MODE
 * (0x5b) bit 3 is set: CSC enable (0x34 bit 1) = input is RGB (STATUS3 0x81 bits 6:5 == 0),
 * followed by a pulse of 0x59 bits 1,2; otherwise 0x34 |= 2. The Windows driver repeats this
 * decision in a monitor loop; rx_monitor() does the same every 500 ms while capturing.
 */

#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/iopoll.h>
#include <linux/seq_file.h>
#include <linux/uaccess.h>

#include "ad9398_modes.h"
#include "adv7181_modes.h"
#include "gaia_regs.h"
#include "mxo2.h"

#define SRI_FIFO_SIZE     0x200
#define SRI_VALID         BIT(31)
#define SRI_ERROR         BIT(30)
#define SRI_I2C_CTRL      3
#define SRI_I2C_DATA      4
#define SRI_TIMEOUT_US    50000

#define AD9398_ADDR       0x98
#define AD9398_STATUS1    0x2f
#define AD9398_GEN_SETUP2 0x34       /* bit 1 = CSC enable */
#define AD9398_GEN_SETUP3 0x59       /* bits 1,2 pulsed after a mode change */
#define AD9398_HDMI_MODE  0x5b       /* bit 3 = HDMI (not DVI / no signal) */
#define AD9398_STATUS3    0x81       /* bits 6:5 = AVI colour space */
#define AD9398_VIC        0x84
#define AD9398_POWER      0x26       /* bit 0 = power down (Windows sets it for the analog inputs) */

#define ADV7181_ADDR      0x42
#define ADV7181_PWR       0x0f       /* bit 7 = reset, bits 5/1 = power down */
#define ADV7181_STATUS1   0x10       /* bit 0 = in lock, bits 6:4 = detected standard */
#define ADV7181_STATUS3   0x13       /* bit 6 = interlaced (field sequence found) */

static int rx_mode = 10;
module_param(rx_mode, int, 0644);
MODULE_PARM_DESC(rx_mode, "AD9398 mode table loaded at probe (0-11, default 10 = 1080p; "
		 "-1 = leave the receiver alone)");

static int input = -1;
module_param(input, int, 0444);
MODULE_PARM_DESC(input, "Default of the video device's Input control (the audio device follows the input): "
		 "-1 = auto (default, the one with a signal, HDMI first, else composite), 0 = HDMI, 1 = composite, "
		 "2 = S-video, 3 = component");

static int composite_interlaced = -1;
module_param(composite_interlaced, int, 0644);
MODULE_PARM_DESC(composite_interlaced, "Composite scan: -1 = auto (decoder field sequence flag), 0 = force 240p/288p, "
		 "1 = force 480i/576i");

/* ---- SRI ---- */

static int sri_wait_free(struct mxo2 *m, u32 need)
{
	u32 v;

	return read_poll_timeout(mxo2_rd, v, v >= need, 10, SRI_TIMEOUT_US, false,
				 m, GAIA_SRI_FIFO_COUNT);
}

static int sri_write(struct mxo2 *m, u32 addr, u8 val)
{
	int ret = sri_wait_free(m, 1);

	if (ret)
		return ret;
	mxo2_wr(m, GAIA_SRI_ADDRESS, addr & 0x3ff);
	mxo2_wr(m, GAIA_SRI_DATA, val);
	return 0;
}

static int sri_read(struct mxo2 *m, u32 addr, u8 *val)
{
	u32 v;
	int ret;

	mxo2_wr(m, GAIA_SRI_ADDRESS, addr & 0x3ff);
	ret = read_poll_timeout(mxo2_rd, v,
				(v & SRI_ERROR) || ((v & SRI_VALID) && ((v >> 16) & 0x3ff) == addr),
				10, SRI_TIMEOUT_US, false, m, GAIA_SRI_DATA);
	if (ret)
		return ret;
	if (v & SRI_ERROR)
		return -EIO;
	*val = v & 0xff;
	return 0;
}

/* ---- I2C over SRI ---- */

static int i2c_cmd(struct mxo2 *m, u8 a, u8 b)
{
	int ret = sri_write(m, SRI_I2C_CTRL, a);

	return ret ? ret : sri_write(m, SRI_I2C_CTRL, b);
}

static int i2c_write(struct mxo2 *m, u8 dev, u8 reg, u8 val)
{
	int ret;

	ret = i2c_cmd(m, 6, 7);                         /* start */
	if (!ret)
		ret = sri_write(m, SRI_I2C_DATA, dev & 0xfe);
	if (!ret)
		ret = sri_write(m, SRI_I2C_DATA, reg);
	if (!ret)
		ret = sri_write(m, SRI_I2C_DATA, val);
	if (!ret)
		ret = i2c_cmd(m, 5, 7);                 /* stop */
	return ret;
}

static int i2c_read(struct mxo2 *m, u8 dev, u8 reg, u8 *val)
{
	int ret;

	ret = i2c_cmd(m, 6, 7);
	if (!ret)
		ret = sri_write(m, SRI_I2C_DATA, dev & 0xfe);
	if (!ret)
		ret = sri_write(m, SRI_I2C_DATA, reg);
	if (!ret)
		ret = i2c_cmd(m, 6, 7);                 /* repeated start */
	if (!ret)
		ret = sri_write(m, SRI_I2C_DATA, dev | 1);
	if (!ret)
		ret = sri_write(m, SRI_I2C_CTRL, 7);    /* one byte: nack */
	if (!ret)
		ret = sri_write(m, SRI_I2C_DATA, 0xff);
	if (!ret)
		ret = sri_wait_free(m, SRI_FIFO_SIZE);  /* Windows waits for an empty FIFO */
	if (!ret)
		ret = sri_read(m, SRI_I2C_DATA, val);
	if (!ret)
		ret = i2c_cmd(m, 5, 7);
	return ret;
}

int mxo2_i2c_write(struct mxo2 *m, u8 dev, u8 reg, u8 val)
{
	return i2c_write(m, dev, reg, val);
}

int mxo2_i2c_read(struct mxo2 *m, u8 dev, u8 reg, u8 *val)
{
	return i2c_read(m, dev, reg, val);
}

static int rx_rd(struct mxo2 *m, u8 reg, u8 *val)
{
	return i2c_read(m, AD9398_ADDR, reg, val);
}

static int rx_wr(struct mxo2 *m, u8 reg, u8 val)
{
	return i2c_write(m, AD9398_ADDR, reg, val);
}

/* ---- AD9398 ---- */

/* CSC choice + reset pulse (FUN_00014c10 + FUN_00014ce4), or CSC on without HDMI (FUN_00014c90). */
static int rx_apply_csc(struct mxo2 *m)
{
	u8 hdmi, st3, gs2, gs3;
	int ret;

	ret = rx_rd(m, AD9398_HDMI_MODE, &hdmi);
	if (!ret)
		ret = rx_rd(m, AD9398_GEN_SETUP2, &gs2);
	if (ret)
		return ret;
	if (!(hdmi & BIT(3))) {
		m->rx_state = MXO2_RX_NO_HDMI;
		return rx_wr(m, AD9398_GEN_SETUP2, gs2 | BIT(1));
	}
	ret = rx_rd(m, AD9398_STATUS3, &st3);
	if (ret)
		return ret;
	if (st3 & 0x60)
		gs2 &= ~BIT(1);         /* YCbCr input: CSC off */
	else
		gs2 |= BIT(1);          /* RGB input: CSC converts to YCbCr */
	ret = rx_wr(m, AD9398_GEN_SETUP2, gs2);
	if (!ret)
		ret = rx_rd(m, AD9398_GEN_SETUP3, &gs3);
	if (!ret)
		ret = rx_wr(m, AD9398_GEN_SETUP3, gs3 | 0x06);
	if (!ret)
		ret = rx_wr(m, AD9398_GEN_SETUP3, gs3 & ~0x06);
	if (!ret) {
		m->rx_state = MXO2_RX_HDMI;
		m->rx_colorspace = (st3 >> 5) & 3;
	}
	return ret;
}

static int rx_load_mode(struct mxo2 *m, int mode)
{
	int i, ret;

	for (i = 0; i < AD9398_MODE_REGS; i++) {
		ret = rx_wr(m, ad9398_modes[mode][i][0], ad9398_modes[mode][i][1]);
		if (ret)
			return ret;
	}
	return rx_apply_csc(m);
}

/*
 * Configure the receiver (needs the Gaia table defaults written first, for SRI).
 * Returns 0 and does nothing when rx_mode is -1.
 */
int mxo2_rx_setup(struct mxo2 *m)
{
	u8 rev, st1;
	int ret;

	if (rx_mode < 0)
		return 0;
	if (rx_mode >= AD9398_NUM_MODES)
		return -EINVAL;
	mutex_lock(&m->rx_lock);
	m->rx_state = MXO2_RX_UNKNOWN;
	ret = rx_rd(m, 0x00, &rev);
	if (!ret && rev == 0xff)
		ret = -ENODEV;          /* nothing answering (no ACK check on this bus) */
	if (!ret)
		ret = rx_load_mode(m, 0);
	if (!ret)
		ret = rx_load_mode(m, rx_mode);
	if (!ret)
		ret = rx_rd(m, AD9398_STATUS1, &st1);
	if (!ret) {
		m->rx_mode = rx_mode;
		dev_info(&m->pdev->dev, "AD9398 rev %02x: mode %d loaded, STATUS1 %02x, %s\n", rev,
			 rx_mode, st1, m->rx_state == MXO2_RX_HDMI ? "HDMI input" : "no HDMI yet");
	} else {
		dev_err(&m->pdev->dev, "AD9398 setup failed: %d\n", ret);
	}
	mutex_unlock(&m->rx_lock);
	return ret;
}

/*
 * Input format from the receiver: the AVI VIC code when the source sends one, else the
 * HSYNCs-per-VSYNC counter (0x17[3:0]:0x18, about 1125 for 1080p, 750 for 720p).
 * Only progressive 1080p and 720p are supported; anything else reads as 0x0.
 */
/*
 * Supported input formats. Detection: the AVI VIC when the source sends one, else the HSYNCs-per-VSYNC
 * count (per field for interlaced formats). pclk = TMDS clock (for the audio rate check). table = AD9398
 * mode table (MVKAVCIO FUN_00014b28: 480i 1, 480p 2, 576i 3, 576p 4, 1080i 5; -1 = rx_mode, used by Windows
 * for 1080p and fine for 720p). SD interlaced formats are sent with 2x pixel repetition (1440 wide); the
 * AD9398 de-repeats them (0x30[3:0]), so the card sees 720 pixels per line.
 */
static const struct mxo2_fmt {
	u8 vic[2];
	u16 w, h, fps;
	u32 pclk_khz;
	bool interlaced;
	u16 lines_lo, lines_hi; /* HSYNCs per VSYNC as read from 0x17/0x18 */
	s8 table;
} formats[] = {
	{ { 16 }, 1920, 1080, 60, 148500, false, 1120, 1130, -1 },
	{ { 31 }, 1920, 1080, 50, 148500, false, 1120, 1130, -1 },
	{ { 34 }, 1920, 1080, 30, 74250, false, 1120, 1130, -1 },
	{ { 33 }, 1920, 1080, 25, 74250, false, 1120, 1130, -1 },
	{ { 32 }, 1920, 1080, 24, 74250, false, 1120, 1130, -1 },
	{ { 4 }, 1280, 720, 60, 74250, false, 745, 755, -1 },
	{ { 19 }, 1280, 720, 50, 74250, false, 745, 755, -1 },
	{ { 5 }, 1920, 1080, 30, 74250, true, 555, 570, 5 },             /* 1080i60 */
	{ { 20 }, 1920, 1080, 25, 74250, true, 555, 570, 5 },            /* 1080i50 */
	{ { 2, 3 }, 720, 480, 60, 27000, false, 518, 530, 2 },           /* 480p */
	{ { 17, 18 }, 720, 576, 50, 27000, false, 618, 630, 4 },         /* 576p */
	{ { 6, 7 }, 720, 480, 30, 27000, true, 258, 267, 1 },            /* 480i (1440 wide, 2x) */
	{ { 21, 22 }, 720, 576, 25, 27000, true, 308, 317, 3 },          /* 576i (1440 wide, 2x) */
};

/* Audio sample rate: IEC 60958 channel status (AD9398 0x61[3:0]) ... */
static u32 cs_rate(u8 code)
{
	switch (code & 0x0f) {
	case 0x0: return 44100;
	case 0x2: return 48000;
	case 0x3: return 32000;
	case 0x8: return 88200;
	case 0xa: return 96000;
	case 0xc: return 176400;
	case 0xe: return 192000;
	}
	return 0;
}

/* ... and from clock regeneration, fs = pixel clock * N / (128 * CTS), snapped to a standard rate. */
static u32 cts_rate(u32 pclk_khz, u32 n, u32 cts)
{
	static const u32 rates[] = { 32000, 44100, 48000, 88200, 96000, 176400, 192000 };
	u64 fs;
	size_t i;

	if (!pclk_khz || !n || !cts)
		return 0;
	fs = div64_u64((u64)pclk_khz * 1000 * n, 128ULL * cts);
	for (i = 0; i < ARRAY_SIZE(rates); i++)          /* within 1 % (covers 1000/1001 clocks) */
		if (fs * 100 > rates[i] * 99ULL && fs * 100 < rates[i] * 101ULL)
			return rates[i];
	return 0;
}

static void rx_detect_locked(struct mxo2 *m)
{
	u8 st1 = 0, hdmi, vic = 0, l_hi, l_lo, c1, c2, c3n1, n2, n3, csf, rep, avi2;
	u32 w = 0, h = 0, fps = 0, pclk = 0, lines, r_cs = 0, r_cts, rate;
	const struct mxo2_fmt *f = NULL;
	bool il = false;
	size_t i;

	if (rx_rd(m, AD9398_STATUS1, &st1) || rx_rd(m, AD9398_HDMI_MODE, &hdmi) ||
	    rx_rd(m, 0x17, &l_hi) || rx_rd(m, 0x18, &l_lo))
		goto out;
	/* audio clock regeneration: CTS 0x7b/0x7c/0x7d[7:4], N 0x7d[3:0]/0x7e/0x7f */
	if (!rx_rd(m, 0x7b, &c1) && !rx_rd(m, 0x7c, &c2) && !rx_rd(m, 0x7d, &c3n1) &&
	    !rx_rd(m, 0x7e, &n2) && !rx_rd(m, 0x7f, &n3)) {
		m->in_cts = c1 << 12 | c2 << 4 | c3n1 >> 4;
		m->in_n = (c3n1 & 0x0f) << 16 | n2 << 8 | n3;
	}
	lines = ((l_hi & 0xf) << 8) | l_lo;   /* HSYNCs per VSYNC: 1124 for 1080p, 562 per 1080i field */
	/* frame lines: per-field counts (262, 312, 562) mean an interlaced format */
	m->in_vtotal = (lines >= 258 && lines <= 267) || (lines >= 308 && lines <= 317) ||
		       (lines >= 555 && lines <= 570) ? 2 * lines + 1 : lines + 1;
	if (!rx_rd(m, 0x61, &csf))
		r_cs = cs_rate(csf);
	if (st1 != 0x67)                /* not locked */
		goto out;
	if (!rx_rd(m, 0x30, &rep))
		m->in_pixel_rep = (rep & 0x0f) + 1;
	if (hdmi & BIT(3))
		rx_rd(m, AD9398_VIC, &vic);
	for (i = 0; i < ARRAY_SIZE(formats) && !f; i++)         /* by VIC */
		if (vic && (formats[i].vic[0] == vic || formats[i].vic[1] == vic))
			f = &formats[i];
	for (i = 0; i < ARRAY_SIZE(formats) && !f; i++)         /* by line count (size only) */
		if (lines >= formats[i].lines_lo && lines <= formats[i].lines_hi)
			f = &formats[i];
	if (f) {
		w = f->w;
		h = f->h;
		il = f->interlaced;
		m->in_table = f->table;
		/* picture aspect: the second VIC of an SD pair is the 16:9 one (CEA-861); without a VIC
		 * use the AVI InfoFrame picture aspect bits (0x82[5:4]: 01 = 4:3, 10 = 16:9) */
		if (vic && f->vic[1])
			m->in_wide = vic == f->vic[1];
		else if (!rx_rd(m, 0x82, &avi2))
			m->in_wide = ((avi2 >> 4) & 3) == 2;
		if (vic) {      /* rate and clock only known with a VIC */
			fps = f->fps;
			pclk = f->pclk_khz;
		}
	}
out:
	/* clock regeneration wins (independent of what the source claims), channel status otherwise */
	r_cts = st1 == 0x67 ? cts_rate(pclk, m->in_n, m->in_cts) : 0;
	rate = r_cts ? r_cts : (st1 == 0x67 ? r_cs : 0);
	if (r_cts && r_cs && r_cts != r_cs && !m->in_rate_warned) {
		dev_warn(&m->pdev->dev, "audio: channel status says %u Hz, clock regeneration %u Hz; using %u\n",
			 r_cs, r_cts, rate);
		m->in_rate_warned = true;
	}
	if (rate != m->in_audio_rate)
		dev_info(&m->pdev->dev, "audio input: %u Hz\n", rate);
	m->in_audio_rate = rate;
	m->in_rate_cs = r_cs;
	m->in_rate_cts = r_cts;
	if (w != m->in_width || h != m->in_height || fps != m->in_fps || il != m->in_interlaced)
		dev_info(&m->pdev->dev, "input: %ux%u%s @ %u fps (VIC %u)\n", w, h, il ? "i" : "p", fps, vic);
	m->in_interlaced = il;
	m->in_width = w;
	m->in_height = h;
	m->in_fps = fps;
	m->in_vic = vic;
	m->sig[MXO2_INPUT_HDMI] = (struct mxo2_sig){ .width = w, .height = h, .fps = fps, .interlaced = il,
						     .wide = m->in_wide };
}

static void dec_detect_locked(struct mxo2 *m);

void mxo2_rx_detect(struct mxo2 *m)
{
	mutex_lock(&m->rx_lock);
	if (m->input == MXO2_INPUT_HDMI)
		rx_detect_locked(m);
	else
		dec_detect_locked(m);
	mutex_unlock(&m->rx_lock);
}

/* ---- ADV7181 analog decoder (re/NOTES.md, "Analog inputs", analog probes 1-7) ---- */

static int dec_rd(struct mxo2 *m, u8 reg, u8 *val)
{
	return i2c_read(m, ADV7181_ADDR, reg, val);
}

static int dec_wr(struct mxo2 *m, u8 reg, u8 val)
{
	return i2c_write(m, ADV7181_ADDR, reg, val);
}

static int dec_write_table(struct mxo2 *m, const u8 (*t)[2], size_t n)
{
	size_t i;
	int ret = 0;

	for (i = 0; i < n && !ret; i++)
		ret = dec_wr(m, t[i][0], t[i][1]);
	return ret;
}

/* Decoder table <-> analog input: 0/3 composite, 1/4 S-video, 2/5 component (525 / 625 lines) */
static int dec_table(int in, bool lines625)
{
	return in - MXO2_INPUT_COMPOSITE + (lines625 ? 3 : 0);
}

static int dec_input(int mode)
{
	return mode < 0 ? -1 : MXO2_INPUT_COMPOSITE + mode % 3;
}

/*
 * Load a decoder table (0-5) as MVKAVCIO FUN_0002976c does: power up + reset, 20 ms, the table, then the
 * hidden-register script (FUN_00028f04: ADI SD script, component b7=1b); composite also our level/filter overrides.
 */
static int dec_load(struct mxo2 *m, int mode)
{
	const struct adv7181_table *t = &adv7181_modes[mode];
	bool composite = dec_input(mode) == MXO2_INPUT_COMPOSITE;
	u8 pm;
	int ret;

	ret = dec_rd(m, ADV7181_PWR, &pm);
	if (!ret)
		ret = dec_wr(m, ADV7181_PWR, (pm & ~0x22) | 0x80);
	if (ret)
		return ret;
	msleep(20);
	ret = dec_write_table(m, t->regs, t->n);
	if (!ret && dec_input(mode) == MXO2_INPUT_COMPONENT)
		ret = dec_write_table(m, adv7181_hidden_comp, ARRAY_SIZE(adv7181_hidden_comp));
	else if (!ret)
		ret = dec_write_table(m, adv7181_hidden_sd, ARRAY_SIZE(adv7181_hidden_sd));
	if (!ret && composite)
		ret = dec_write_table(m, adv7181_overrides, ARRAY_SIZE(adv7181_overrides));
	if (!ret && composite)
		ret = dec_wr(m, 0x17, m->sharpness ? 0xb3 : 0xa2);
	/*
	 * The Windows tables force the standard (0x00 = 0x55 / 0x85) and clear the autodetect mask 0x07, so
	 * AD_RESULT never changes. Autodetect (PAL B/G/H/I/D, NTSC M, SECAM) with all standards enabled instead,
	 * keeping the input select (re/notes/pal-probe3.txt: a PAL source then reads PAL B/G/H/I/D).
	 */
	if (!ret)
		ret = dec_wr(m, 0x07, 0x7f);
	if (!ret)
		ret = dec_wr(m, 0x00, 0x10 | (t->regs[0][1] & 0x0f));         /* table entry 0 = reg 0x00 */
	m->dec_mode = ret ? -1 : mode;
	return ret;
}

/*
 * Luma shaping filter 0x17 (re/notes/analog-probe6.txt, "Composite sharpness"): 0xa2 = notch, no subcarrier dots
 * but edges over ~2.2 console pixels; 0xb3 (mode table, wide band) = edges within ~0.8 px, subcarrier dots left in
 * luma (the comb filter can't remove them for consoles' 240p) - removed in software for sharpness 1.
 */
void mxo2_set_sharpness(struct mxo2 *m, int val)
{
	mutex_lock(&m->rx_lock);
	m->sharpness = val;
	if (dec_input(m->dec_mode) == MXO2_INPUT_COMPOSITE)
		dec_wr(m, 0x17, val ? 0xb3 : 0xa2);
	mutex_unlock(&m->rx_lock);
}

static int dec_power_down(struct mxo2 *m)
{
	u8 pm;
	int ret = dec_rd(m, ADV7181_PWR, &pm);

	if (!ret)
		ret = dec_wr(m, ADV7181_PWR, pm | 0x22);
	m->dec_mode = -1;
	return ret;
}

/* AD_RESULT (Status1 bits 6:4) names */
static const char *const dec_std_names[8] = {
	"NTSC M/J", "NTSC 4.43", "PAL M", "PAL 60", "PAL B/G/H/I/D", "SECAM", "PAL Comb N", "SECAM 525",
};

/*
 * Analog format from the decoder status. AD_RESULT (Status1 bits 6:4) gives two things: the line system
 * (4 PAL B/G/H/I/D, 5 SECAM, 6 PAL N = 625 lines / 50 Hz, the rest 525 / 60 Hz) and the colour system, which
 * picks the decoder table (PAL colour, incl. PAL 60 / PAL M, and SECAM: table 3; NTSC: table 0). The decoder
 * runs in autodetect, so it decodes the colour itself; the table brings the matching filters and gains.
 * Status3 bit 6 clear = no field sequence = 240p/288p (game consoles), captured as one packed field per frame.
 * The card stores 243 (NTSC) lines per field; we deliver 240/480.
 */
static void dec_detect_locked(struct mxo2 *m)
{
	u32 w = 0, h = 0, fps = 0;
	bool il = false, pal = false, pal_colour;
	u8 s1 = 0, s3 = 0;
	int std, want;

	if (dec_rd(m, ADV7181_STATUS1, &s1) || dec_rd(m, ADV7181_STATUS3, &s3))
		goto out;
	m->dec_status1 = s1;
	m->dec_status3 = s3;
	if (s1 == 0xff && s3 == 0xff)   /* nobody answering (no ACK check on this bus): not a signal */
		goto out;
	if (!(s1 & BIT(0)))
		goto out;
	std = (s1 >> 4) & 7;
	pal = std == 4 || std == 5 || std == 6;
	pal_colour = std != 0 && std != 1 && std != 7;
	/* component has no colour subcarrier: the table follows the line system */
	want = dec_table(m->input, m->input == MXO2_INPUT_COMPONENT ? pal : pal_colour);
	if (want == m->dec_mode) {
		m->dec_want = want;
	} else if (want != m->dec_want) {
		/* AD_RESULT can read wrong for a while right after lock: the change must hold for 400 ms */
		m->dec_want = want;
		m->dec_want_since = jiffies;
		goto out;
	} else if (time_before(jiffies, m->dec_want_since + msecs_to_jiffies(400))) {
		goto out;
	} else {
		if (!dec_load(m, want))
			dev_info(&m->pdev->dev, "ADV7181: %s detected (%s), decoder mode %d loaded\n",
				 dec_std_names[std], pal ? "50 Hz" : "60 Hz", want);
		goto out;               /* format again next round, after the decoder settled */
	}
	il = composite_interlaced < 0 ? !!(s3 & BIT(6)) : !!composite_interlaced;
	w = 720;
	h = pal ? 576 : 480;
	fps = pal ? 25 : 30;
	if (!il) {
		h /= 2;
		fps *= 2;
	}
out:
	if (w != m->in_width || h != m->in_height || fps != m->in_fps || il != m->in_interlaced)
		dev_info(&m->pdev->dev, "%s input: %ux%u%s @ %u fps (decoder status %02x/%02x)\n",
			 mxo2_input_names[m->input],
			 w, h, il ? "i" : "p", fps, s1, s3);
	m->in_width = w;
	m->in_height = h;
	m->in_fps = fps;
	m->in_interlaced = il;
	m->in_vtotal = pal ? 625 : 525;
	m->in_vic = 0;
	m->in_wide = false;
	m->in_table = -1;
	m->in_pixel_rep = 1;
	m->sig[m->input] = (struct mxo2_sig){ .width = w, .height = h, .fps = fps, .interlaced = il };
	m->in_audio_rate = 0;   /* analog audio: no rate detection, 48 kHz */
	m->in_n = 0;
	m->in_cts = 0;
	m->in_rate_cs = 0;
	m->in_rate_cts = 0;
}

/*
 * Switch between HDMI (AD9398) and composite (ADV7181). The unused chip is powered down, as the Windows
 * input select MVKAVCIO FUN_000493d4 does. Callers make sure no capture is running.
 */
int mxo2_select_input(struct mxo2 *m, int in)
{
	int ret = 0;

	if (in < 0 || in >= MXO2_NUM_INPUTS)
		return -EINVAL;
	mutex_lock(&m->rx_lock);
	if (in == m->input && (in == MXO2_INPUT_HDMI || dec_input(m->dec_mode) == in))
		goto out;
	if (in != MXO2_INPUT_HDMI) {
		/* both chips stay on while idle (mxo2_input_power powers the unused one down for a capture) */
		int i;

		if (dec_input(m->dec_mode) != in)
			ret = dec_load(m, dec_table(in, false));
		for (i = 0; !ret && i < 20; i++) {      /* wait up to 1 s for the decoder to lock */
			u8 s1;

			if (!dec_rd(m, ADV7181_STATUS1, &s1) && s1 != 0xff && (s1 & BIT(0)))
				break;
			msleep(50);
		}
	} else {
		if (rx_mode >= 0) {
			ret = rx_load_mode(m, 0);       /* also clears the AD9398 power down */
			if (!ret)
				ret = rx_load_mode(m, rx_mode);
			m->rx_mode = ret ? -1 : rx_mode;
		}
	}
	if (ret) {
		dev_err(&m->pdev->dev, "switching to input %d failed: %d\n", in, ret);
		goto out;
	}
	m->input = in;
	if (in == MXO2_INPUT_HDMI) {
		rx_detect_locked(m);
	} else {
		int i;

		/* format known and stable before a capture starts: two equal readings 100 ms apart (status reads
		 * can be off right after lock; a 50/60 Hz table change takes 400 ms + a round) */
		u32 lw = 0, lh = 0;

		for (i = 0; i < 20; i++) {
			dec_detect_locked(m);
			if (m->in_width && m->in_width == lw && m->in_height == lh)
				break;
			lw = m->in_width;
			lh = m->in_height;
			msleep(100);
		}
	}
	dev_info(&m->pdev->dev, "input %s selected\n", mxo2_input_names[in]);
out:
	mutex_unlock(&m->rx_lock);
	return ret;
}

/*
 * After mxo2_rx_setup (AD9398 tables loaded = HDMI selected): at probe the input from the module parameter,
 * after a resume the input used before (the decoder lost its setup). In auto mode the decoder is loaded too,
 * so the monitor can see a composite signal while HDMI is selected.
 */
int mxo2_input_init(struct mxo2 *m, bool probe)
{
	int in = m->input;

	if (probe) {
		m->input_pref = input < 0 || input >= MXO2_NUM_INPUTS ? -1 : input;
		in = m->input_pref < 0 ? MXO2_INPUT_HDMI : m->input_pref;
	}
	m->input = MXO2_INPUT_HDMI;
	m->dec_mode = -1;
	if (in != MXO2_INPUT_HDMI)
		return mxo2_select_input(m, in);
	mxo2_input_power(m, false);
	return 0;
}

/*
 * Chip power around a capture. Fixed input: mxo2_select_input already powered the other chip down. Auto: while
 * idle both chips run (to see a signal on either input); while capturing only the selected one, as on Windows
 * (MVKAVCIO FUN_000493d4).
 */
void mxo2_input_power(struct mxo2 *m, bool capturing)
{
	u8 v;

	mutex_lock(&m->rx_lock);
	if (m->input == MXO2_INPUT_HDMI) {
		if (capturing)
			dec_power_down(m);
		else if (m->dec_mode < 0)
			dec_load(m, 0);
	} else if (!rx_rd(m, AD9398_POWER, &v)) {
		rx_wr(m, AD9398_POWER, capturing ? v | BIT(0) : v & ~BIT(0));
	}
	mutex_unlock(&m->rx_lock);
}

/* Auto mode: the input that has a signal now (HDMI first), or -1. Both chips are on while idle. */
static int auto_pick_locked(struct mxo2 *m)
{
	u8 st1 = 0, s1 = 0;

	if (m->rx_mode >= 0 && !rx_rd(m, AD9398_STATUS1, &st1) && st1 == 0x67)
		return MXO2_INPUT_HDMI;
	if (m->dec_mode >= 0 && !dec_rd(m, ADV7181_STATUS1, &s1) && s1 != 0xff && (s1 & BIT(0)))
		return dec_input(m->dec_mode);
	return -1;
}

/* Called every 500 ms: redo the CSC decision when the input changes, update the format. */
static void monitor_hdmi_locked(struct mxo2 *m);

/*
 * The input that is not selected, seen while idle (both chips on): its signal for Auto and the V4L2 input status,
 * without touching the selected input's state.
 */
static void hdmi_probe_locked(struct mxo2 *m)
{
	struct mxo2_sig sig = {};
	const struct mxo2_fmt *f = NULL;
	u8 st1, hdmi, l_hi, l_lo, vic = 0, avi2;
	u32 lines;
	size_t i;

	if (m->rx_mode < 0 || rx_rd(m, AD9398_STATUS1, &st1) || st1 != 0x67 || rx_rd(m, AD9398_HDMI_MODE, &hdmi) ||
	    rx_rd(m, 0x17, &l_hi) || rx_rd(m, 0x18, &l_lo))
		goto out;
	lines = ((l_hi & 0xf) << 8) | l_lo;
	if (hdmi & BIT(3))
		rx_rd(m, AD9398_VIC, &vic);
	for (i = 0; i < ARRAY_SIZE(formats) && !f; i++)
		if (vic && (formats[i].vic[0] == vic || formats[i].vic[1] == vic))
			f = &formats[i];
	for (i = 0; i < ARRAY_SIZE(formats) && !f; i++)
		if (lines >= formats[i].lines_lo && lines <= formats[i].lines_hi)
			f = &formats[i];
	if (!f)
		goto out;
	sig.width = f->w;
	sig.height = f->h;
	sig.interlaced = f->interlaced;
	sig.fps = vic ? f->fps : 0;
	if (vic && f->vic[1])
		sig.wide = vic == f->vic[1];
	else if (!rx_rd(m, 0x82, &avi2))
		sig.wide = ((avi2 >> 4) & 3) == 2;
out:
	m->sig[MXO2_INPUT_HDMI] = sig;
}

static void dec_probe_locked(struct mxo2 *m)
{
	struct mxo2_sig sig = {};
	u8 s1, s3;
	bool pal;
	int std;

	if (m->dec_mode < 0 || dec_rd(m, ADV7181_STATUS1, &s1) || dec_rd(m, ADV7181_STATUS3, &s3) ||
	    (s1 == 0xff && s3 == 0xff) || !(s1 & BIT(0)))
		goto out;
	std = (s1 >> 4) & 7;
	pal = std == 4 || std == 5 || std == 6;
	sig.interlaced = composite_interlaced < 0 ? !!(s3 & BIT(6)) : !!composite_interlaced;
	sig.width = 720;
	sig.height = pal ? 576 : 480;
	sig.fps = pal ? 25 : 30;
	if (!sig.interlaced) {
		sig.height /= 2;
		sig.fps *= 2;
	}
out:
	if (m->dec_mode >= 0)
		m->sig[dec_input(m->dec_mode)] = sig;
}

/* Input control changed: switch now (no waiting for the monitor) to the fixed input, or for Auto to the one with a signal. */
int mxo2_auto_input_now(struct mxo2 *m)
{
	int pick;

	mutex_lock(&m->rx_lock);
	pick = m->input_pref >= 0 ? m->input_pref : auto_pick_locked(m);
	mutex_unlock(&m->rx_lock);
	if (pick < 0 || pick == m->input)
		return 0;
	return mxo2_switch_input(m, pick);
}

void mxo2_rx_monitor(struct mxo2 *m)
{
	int want = -1;

	if (!mutex_trylock(&m->rx_lock))
		return;
	if (m->input != MXO2_INPUT_HDMI)
		dec_detect_locked(m);
	else if (m->rx_mode >= 0)
		monitor_hdmi_locked(m);
	mxo2_output_monitor_locked(m);
	if (!(READ_ONCE(m->cap_users) & (MXO2_USER_V4L2 | MXO2_USER_DEBUGFS))) {   /* both chips on */
		if (m->input == MXO2_INPUT_HDMI)
			dec_probe_locked(m);
		else
			hdmi_probe_locked(m);
	}
	/* input choice ("Input" control): only while no video capture runs and no app holds buffers */
	if (!m->input_locked && !(READ_ONCE(m->cap_users) & (MXO2_USER_V4L2 | MXO2_USER_DEBUGFS)) &&
	    !mxo2_video_busy(m)) {
		int pick = m->input_pref >= 0 ? m->input_pref : auto_pick_locked(m);

		if (pick >= 0 && pick != m->input && m->input_pref >= 0) {
			want = pick;                    /* fixed choice: switch at once */
		} else if (pick >= 0 && pick != m->input) {
			m->auto_count = pick == m->auto_want ? m->auto_count + 1 : 1;
			m->auto_want = pick;
			if (m->auto_count >= 2)         /* seen for a second round: switch */
				want = pick;
		} else {
			m->auto_count = 0;
		}
	}
	mutex_unlock(&m->rx_lock);
	if (want >= 0) {
		m->auto_count = 0;
		if (!mxo2_switch_input(m, want))
			dev_info(&m->pdev->dev, "input: switched to %s (%s)\n", mxo2_input_names[want],
				 m->input_pref < 0 ? "auto, signal there" : "Input control");
	}
}

/* HDMI: redo the CSC decision when the input changes, update the format, load the format's table. */
static void monitor_hdmi_locked(struct mxo2 *m)
{
	u8 hdmi, st3;
	int want;

	if (rx_rd(m, AD9398_HDMI_MODE, &hdmi) || rx_rd(m, AD9398_STATUS3, &st3))
		goto out;
	want = hdmi & BIT(3) ? MXO2_RX_HDMI : MXO2_RX_NO_HDMI;
	if (want != m->rx_state ||
	    (want == MXO2_RX_HDMI && ((st3 >> 5) & 3) != m->rx_colorspace)) {
		if (!rx_apply_csc(m))
			dev_info(&m->pdev->dev, "HDMI input %s, colour space %d\n",
				 m->rx_state == MXO2_RX_HDMI ? "present" : "gone", m->rx_colorspace);
	}
	rx_detect_locked(m);
	/* load the receiver table that belongs to the detected format (Windows: FUN_00014b28) */
	if (rx_mode >= 0 && m->in_width) {
		int mode = m->in_table >= 0 ? m->in_table : rx_mode;

		if (mode != m->rx_mode && !rx_load_mode(m, mode)) {
			dev_info(&m->pdev->dev, "AD9398 mode %d loaded for %ux%u%s input\n", mode,
				 m->in_width, m->in_height, m->in_interlaced ? "i" : "p");
			m->rx_mode = mode;
		}
	}
out:
	return;
}

/* ---- debugfs: rx (read = receiver status, write a number = load that mode) ---- */

static int rx_show(struct seq_file *s, void *unused)
{
	static const char * const cs[] = { "RGB", "YCbCr 4:2:2", "YCbCr 4:4:4", "?" };
	static const u8 regs[] = { 0x00, AD9398_STATUS1, 0x30, 0x17, 0x18, 0x24, 0x25, AD9398_GEN_SETUP2,
				   0x5a, AD9398_HDMI_MODE, AD9398_STATUS3, 0x82, AD9398_VIC };
	struct mxo2 *m = s->private;
	u8 v[ARRAY_SIZE(regs)];
	size_t i;
	int ret = 0;

	mutex_lock(&m->rx_lock);
	for (i = 0; i < ARRAY_SIZE(regs) && !ret; i++)
		ret = rx_rd(m, regs[i], &v[i]);
	mutex_unlock(&m->rx_lock);
	if (ret) {
		seq_printf(s, "I2C error %d\n", ret);
		return 0;
	}
	seq_printf(s, "mode %d, state %s\n", m->rx_mode,
		   m->rx_state == MXO2_RX_HDMI ? "HDMI" : m->rx_state == MXO2_RX_NO_HDMI ? "no HDMI" : "?");
	for (i = 0; i < ARRAY_SIZE(regs); i++)
		seq_printf(s, "%02x=%02x%s", regs[i], v[i], i + 1 < ARRAY_SIZE(regs) ? " " : "\n");
	seq_printf(s, "locked %s, colour space %s, VIC %u, detected %ux%u%s @ %u fps\n",
		   v[1] == 0x67 ? "yes" : "no", cs[(v[10] >> 5) & 3], v[12], m->in_width,
		   m->in_height, m->in_interlaced ? "i" : "p", m->in_fps);
	seq_printf(s, "pixel repetition %ux, receiver table wanted %d, picture aspect %s\n", m->in_pixel_rep,
		   m->in_table, m->in_wide ? "16:9" : "4:3");
	seq_printf(s, "audio %u Hz (channel status %u, clock regeneration %u; N %u CTS %u)\n",
		   m->in_audio_rate, m->in_rate_cs, m->in_rate_cts, m->in_n, m->in_cts);
	seq_printf(s, "input %s%s%s, ADV7181 mode %d, Status1 %02x (%s) Status3 %02x\n",
		   mxo2_input_names[m->input], m->input_pref < 0 ? " (auto)" : "",
		   m->input_locked ? " (set by an app)" : "", m->dec_mode, m->dec_status1,
		   dec_std_names[(m->dec_status1 >> 4) & 7], m->dec_status3);
	return 0;
}

static int rx_open(struct inode *inode, struct file *f)
{
	return single_open(f, rx_show, inode->i_private);
}

static ssize_t rx_write(struct file *f, const char __user *ubuf, size_t len, loff_t *ppos)
{
	struct mxo2 *m = ((struct seq_file *)f->private_data)->private;
	int mode, ret;

	ret = kstrtoint_from_user(ubuf, len, 0, &mode);
	if (ret)
		return ret;
	if (mode < 0 || mode >= AD9398_NUM_MODES)
		return -EINVAL;
	mutex_lock(&m->rx_lock);
	ret = rx_load_mode(m, mode);
	if (!ret)
		m->rx_mode = mode;
	mutex_unlock(&m->rx_lock);
	return ret ? ret : len;
}

static const struct file_operations rx_fops = {
	.owner = THIS_MODULE,
	.open = rx_open,
	.read = seq_read,
	.write = rx_write,
	.llseek = seq_lseek,
	.release = single_release,
};

void mxo2_rx_debugfs(struct mxo2 *m)
{
	debugfs_create_file("rx", 0600, m->dbg, m, &rx_fops);
}
