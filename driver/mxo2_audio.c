// SPDX-License-Identifier: GPL-2.0
/*
 * Matrox MXO2 - audio capture (ALSA, 48 kHz stereo S16_LE): HDMI, or the analog inputs with composite.
 *
 * Hardware (MVKAVBUS "ACaptur_", FUN_000112a8; re/notes/audio-probe*.txt):
 *   AudioWDmaAddress 0x204 = on-board address; AudioWDmaControl 0x200 = 0x80000000 (go) | 0x48000000 |
 *   ch_code << 21 (8 ch = 2) | bytes / 4. A block starts at the next input-frame boundary after it is
 *   armed; AudioWDmaTimeStamp1 changes when it starts. Arming the next block as soon as the current one
 *   has started queues it for the following boundary -> gapless (probe 2 strategy A). Data: 8 channels
 *   of 32-bit words per sample frame, 16-bit sample in the upper half, 48 kHz; channel 0 = left.
 * Blocks must hold exactly one frame of samples: samples per frame = htotal * vtotal * N / (128 * CTS)
 * (HDMI audio clock regeneration, independent of the sample rate), distributed with error diffusion so
 * 59.94 Hz sources (800.8 at 48 kHz) work.
 * The card's audio engine always runs at 48 kHz: a 44.1 kHz source arrives as 48 kHz data (960 samples
 * per 720p50 frame), and no AudioPllCtl value makes it follow the source (re/notes/audio-clock-test.txt).
 * So the stream is always 48 kHz and the block size is 48000 x frame time, with the frame time taken from
 * the source's clock regeneration: htotal * vtotal * N / (128 * fs * CTS), fs = detected source rate
 * (mxo2_rx.c). "HDMI Sample Rate" (read-only control) shows the source's rate.
 *
 * Driver side: the capture thread (mxo2_capture.c) calls mxo2_audio_service() whenever it is awake
 * (and mxo2_audio_arm() on every vsync while a video DMA runs). Finished blocks are copied with the PCI
 * DMA engine from an 8-block on-board ring into a coherent bounce buffer, then converted into the ALSA
 * buffer.
 */

#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/seq_file.h>
#include <sound/control.h>
#include <sound/core.h>
#include <sound/pcm.h>

#include "gaia_regs.h"
#include "mxo2.h"

#define A_CTL_IDLE        0x48000000
#define A_CTL_GO          0x80000000
#define A_CH_8            2              /* AudioWDmaControl bits 22:21: HDMI, 8 x 32-bit words per sample */
#define A_CH_STEREO       0              /* analog inputs: 2 x 32-bit words per sample (Windows 0xc8000c84) */
#define A_INPUT_CTL       0x00000001     /* Windows value while previewing HDMI */
#define A_INPUT_CTL_ANALOG 0x00040001    /* Windows value while previewing composite (notes/win-regshadow-analog.txt) */
#define A_SILENCE_AFTER_MS 150           /* no block for this long: generate silence */
#define A_RING_BASE       0x1f00000      /* on-board, free area below the Windows audio ring */
#define A_SLOT_BYTES      0x20000        /* up to 4096 samples x 8 ch x 4 B (96 kHz at 23.98 fps) */
#define A_SLOTS           8
#define A_FRAME_BYTES     32             /* largest sample frame: 8 x 32 bit (HDMI) */
#define A_MAX_SAMPLES     (A_SLOT_BYTES / A_FRAME_BYTES)
#define A_SG_MAX          0x1f000        /* card SG entries must stay below 0x20000 */
#define A_RATE_DEFAULT    48000

/*
 * The card samples the receiver's output with its own 48 kHz audio clock (AudioPllCtl = 2, the Windows
 * value; 0/1/3 don't make it follow a 44.1 kHz source). Kept as parameters for experiments only:
 * audio_clock = rate of the delivered data (0 = assume it follows the source - wrong on this card),
 * audio_pll = AudioPllCtl written at audio start.
 */
static uint audio_clock = 48000;
module_param(audio_clock, uint, 0644);
MODULE_PARM_DESC(audio_clock, "Card audio sample clock in Hz (default 48000; 0 = same as the HDMI source)");

static int audio_pll = 2;
module_param(audio_pll, int, 0644);
MODULE_PARM_DESC(audio_pll, "AudioPllCtl value written at audio start (default 2 as Windows; -1 = leave)");

static int output_audio = 1;
module_param(output_audio, int, 0644);
MODULE_PARM_DESC(output_audio, "Audio pass-through with the video pass-through (HDMI OUT audio): 1 = on (default), "
		 "0 = off");

#define A_RCTL_IDLE       0x08000000     /* AudioRDmaControl while idle (Windows) */

static u32 source_rate(struct mxo2 *m)
{
	return m->in_audio_rate ? m->in_audio_rate : A_RATE_DEFAULT;
}

/* the rate of the samples the card delivers, and of the ALSA stream */
static u32 stream_rate(struct mxo2 *m)
{
	return audio_clock ? audio_clock : source_rate(m);
}

/*
 * Samples for the next block. spf is kept as a 40.24 fixed-point value and the fraction carries over
 * between blocks (and across N/CTS updates, which jitter for many sources).
 */
/*
 * Composite (analog audio inputs): one block per two fields, as Windows (1602 samples per 29.97 Hz frame).
 * The block must match the two-field length closely (re/notes/analog-audio-probe*.txt: one-field blocks only
 * work within ~1 sample, two-field blocks of 1590..1600 for 1597.3), so its size comes from the measured
 * vsync period (NES 240p: 60.10 Hz, not 59.94); nominal 59.94 / 50 Hz until measured.
 */
static u32 analog_block_samples(struct mxo2 *m)
{
	u32 ns = READ_ONCE(m->field_ns);

	if (ns < 15000000 || ns > 25000000)
		ns = m->in_fps == 25 || m->in_fps == 50 ? 20000000 : 16683333;
	return min_t(u32, DIV_ROUND_CLOSEST_ULL(2ULL * stream_rate(m) * ns, NSEC_PER_SEC), A_MAX_SAMPLES);
}

static void audio_next_size(struct mxo2 *m, u32 *samples)
{
	struct mxo2_audio *a = &m->audio;
	u64 num, den, spf_q24 = 0;
	/* blocks follow the input vsync: per field for 1080i (half a frame) */
	u32 blocks_per_frame = m->in_interlaced ? 2 : 1;
	u32 fps_spf = m->in_fps ? stream_rate(m) / (m->in_fps * blocks_per_frame) : 0;
	u32 htot;

	if (m->cap_analog) {
		*samples = analog_block_samples(m);
		return;
	}
	htot = mxo2_rd(m, GAIA_VIN_STATUS_HDMI);
	if (htot)
		a->htotal = htot;       /* this status register sometimes reads 0 */
	/* the card counts de-repeated pixels; N/CTS refer to the TMDS clock (2x for 480i/576i) */
	num = (u64)a->htotal * m->in_pixel_rep * m->in_vtotal * m->in_n;  /* < 2^36 for 1080p */
	den = 128ULL * m->in_cts * blocks_per_frame;
	if (den)       /* source samples per frame, then scaled to the card's sample clock */
		spf_q24 = div64_u64(div64_u64(num << 24, den) * stream_rate(m), source_rate(m));
	/* trust the clock-regeneration values only if they agree with the VIC rate (or no VIC) */
	if (spf_q24 >= 400ULL << 24 && spf_q24 < (u64)(A_MAX_SAMPLES - 1) << 24 &&
	    (!fps_spf || ((spf_q24 >> 24) * 100 > fps_spf * 98ULL &&
			  (spf_q24 >> 24) * 100 < fps_spf * 102ULL))) {
		a->acc += spf_q24;
		*samples = a->acc >> 24;
		a->acc &= (1ULL << 24) - 1;
		a->spf_q24 = spf_q24;
		return;
	}
	a->spf_q24 = 0;
	*samples = min_t(u32, fps_spf ? fps_spf : stream_rate(m) / 60, A_MAX_SAMPLES);
}

/*
 * Audio pass-through: the read DMA plays a captured block with the same control word it was captured with
 * (Windows: AudioRDmaControl = AudioWDmaControl, RDma address = an earlier ring block). Called when block `b`
 * has been completely written.
 */
static void audio_play_block(struct mxo2 *m, u64 b)
{
	struct mxo2_audio *a = &m->audio;
	unsigned int slot = b % A_SLOTS;

	mxo2_wr(m, GAIA_AUDIO_RDMA_ADDRESS, (A_RING_BASE + slot * A_SLOT_BYTES) & 0x3fffffff);
	mxo2_wr(m, GAIA_AUDIO_RDMA_CONTROL, A_CTL_GO | A_CTL_IDLE | a->ch_code << 21 |
		((a->samples[slot] * a->frame_bytes / 4) & 0xfffe));
	a->played++;
}

static void audio_arm_next(struct mxo2 *m)
{
	struct mxo2_audio *a = &m->audio;
	unsigned int slot = a->armed % A_SLOTS;
	u32 samples;

	audio_next_size(m, &samples);
	a->samples[slot] = samples;
	mxo2_wr(m, GAIA_AUDIO_WDMA_ADDRESS, (A_RING_BASE + slot * A_SLOT_BYTES) & 0x3fffffff);
	mxo2_wr(m, GAIA_AUDIO_WDMA_CONTROL, A_CTL_GO | A_CTL_IDLE | a->ch_code << 21 |
		((samples * a->frame_bytes / 4) & 0xfffe));
	a->armed++;
}

/* Called when the capture thread starts / stops. */
void mxo2_audio_reset(struct mxo2 *m)
{
	struct mxo2_audio *a = &m->audio;

	a->active = false;
	a->armed = 0;
	a->started = 0;
	a->copied = 0;
	a->played = 0;
	a->acc = 0;
	a->real_ns = ktime_get_ns();    /* grace period before silence */
	a->frame_bytes = A_FRAME_BYTES;
	a->ch_code = A_CH_8;
	a->silence_ns = 0;
	mxo2_wr(m, GAIA_AUDIO_WDMA_CONTROL, A_CTL_IDLE);
	mxo2_wr(m, GAIA_AUDIO_RDMA_CONTROL, A_RCTL_IDLE);
}

/* an ALSA capture runs */
static bool audio_wanted(struct mxo2 *m)
{
	return READ_ONCE(m->cap_users) & MXO2_USER_AUDIO;
}

/* the audio engine runs: for ALSA, or for the outputs while they show the captured input */
static bool audio_engine_wanted(struct mxo2 *m)
{
	return audio_wanted(m) || (READ_ONCE(output_audio) && m->out_format >= 0);
}

/* A block started (TS1 changed)? Then queue the next one. Returns true if it did. */
static bool audio_check_start(struct mxo2 *m)
{
	struct mxo2_audio *a = &m->audio;
	u32 ts;

	if (!a->active) {
		if (!audio_engine_wanted(m))
			return false;
		/* first block */
		if (audio_pll >= 0)
			mxo2_wr(m, GAIA_AUDIO_PLL_CTL, audio_pll);
		mxo2_wr(m, GAIA_AUDIO_INPUT_CTL, m->cap_analog ? A_INPUT_CTL_ANALOG : A_INPUT_CTL);
		a->frame_bytes = m->cap_analog ? 8 : A_FRAME_BYTES;
		a->ch_code = m->cap_analog ? A_CH_STEREO : A_CH_8;
		a->last_ts = mxo2_rd(m, GAIA_AUDIO_WDMA_TIME_STAMP1);
		a->active = true;
		audio_arm_next(m);
		return false;
	}
	ts = mxo2_rd(m, GAIA_AUDIO_WDMA_TIME_STAMP1);
	if (ts == a->last_ts)
		return false;
	a->last_ts = ts;
	a->started++;
	audio_arm_next(m);
	if (a->started >= 2 && READ_ONCE(output_audio) && m->out_format >= 0)
		audio_play_block(m, a->started - 2);    /* block started-1 just began: the one before is complete */
	return true;
}

/* While a video DMA runs: after a vsync, wait up to 2 ms for the block start, then queue the next. */
void mxo2_audio_arm(struct mxo2 *m)
{
	unsigned long end = jiffies + msecs_to_jiffies(2) + 1;

	if (!m->audio.active && !audio_engine_wanted(m))
		return;
	while (!audio_check_start(m) && m->audio.active && time_before(jiffies, end))
		usleep_range(100, 200);
}

/* Convert one block (8 x 32-bit per sample frame) into the ALSA buffer; returns periods elapsed. */
static unsigned int audio_deliver(struct mxo2 *m, const u8 *src, u32 samples)
{
	struct mxo2_audio *a = &m->audio;
	struct snd_pcm_runtime *rt;
	unsigned long flags;
	unsigned int periods = 0;
	bool mute;
	u32 i;

	spin_lock_irqsave(&a->lock, flags);
	if (!a->substream || !a->running) {
		spin_unlock_irqrestore(&a->lock, flags);
		return 0;
	}
	rt = a->substream->runtime;
	mute = rt->rate != stream_rate(m);
	if (mute && !a->rate_mismatch)
		dev_warn(&m->pdev->dev, "audio: card now delivers %u Hz, stream is %u Hz - silence until reopened\n",
			 stream_rate(m), rt->rate);
	a->rate_mismatch = mute;
	for (i = 0; i < samples; i++) {
		u16 *dst = (u16 *)(rt->dma_area + frames_to_bytes(rt, a->hw_ptr));

		if (mute || !src) {
			dst[0] = dst[1] = 0;
		} else {
			dst[0] = get_unaligned_le32(src + i * a->frame_bytes) >> 16;     /* ch0 = left */
			dst[1] = get_unaligned_le32(src + i * a->frame_bytes + 4) >> 16; /* ch1 = right */
		}
		if (++a->hw_ptr >= rt->buffer_size)
			a->hw_ptr = 0;
		if (++a->period_pos >= rt->period_size) {
			a->period_pos = 0;
			periods++;
		}
	}
	spin_unlock_irqrestore(&a->lock, flags);
	return periods;
}

/*
 * No source (no input vsync, so no blocks): deliver silence in real time, so the stream keeps running and
 * PipeWire / OBS don't stall. Stops as soon as real blocks arrive again.
 */
static void audio_silence(struct mxo2 *m)
{
	struct mxo2_audio *a = &m->audio;
	u64 now = ktime_get_ns();
	unsigned int periods;
	u32 rate = stream_rate(m), n;

	if (!audio_wanted(m) || now - a->real_ns < A_SILENCE_AFTER_MS * NSEC_PER_MSEC) {
		a->silence_ns = 0;
		return;
	}
	if (!a->silence_ns) {
		a->silence_ns = now;
		return;
	}
	n = div_u64((now - a->silence_ns) * rate, NSEC_PER_SEC);
	if (n < rate / 200)             /* 5 ms chunks */
		return;
	a->silence_ns += div_u64((u64)n * NSEC_PER_SEC, rate);
	mutex_lock(&a->mutex);
	periods = audio_deliver(m, NULL, n);
	a->silence_samples += n;
	while (periods--)
		snd_pcm_period_elapsed(a->substream);
	mutex_unlock(&a->mutex);
}

/* Arm the next block if one started; if the DMA engine is free (`can_dma`), copy finished blocks. */
void mxo2_audio_service(struct mxo2 *m, bool can_dma)
{
	struct mxo2_audio *a = &m->audio;

	audio_check_start(m);
	audio_silence(m);
	if (!a->active || !can_dma)
		return;
	if (!audio_engine_wanted(m)) {   /* last user gone: stop queuing */
		mxo2_audio_reset(m);
		return;
	}
	if (!audio_wanted(m)) {         /* pass-through only: nothing to copy to the host */
		if (a->started >= 1)
			a->copied = a->started - 1;
		return;
	}
	mutex_lock(&a->mutex);          /* vs. pcm_close dropping the substream */
	/* block i is finished once block i + 1 has started: finished = started - 1 */
	if (a->started >= 1 && a->started - 1 > a->copied + A_SLOTS - 2) {
		a->overruns++;
		a->copied = a->started - 1 - (A_SLOTS - 2);
	}
	while (a->started >= 1 && a->copied < a->started - 1) {
		unsigned int slot = a->copied % A_SLOTS;
		u32 bytes = a->samples[slot] * a->frame_bytes;
		unsigned int periods;

		unsigned int n = 0;
		u32 off;

		for (off = 0; off < bytes; off += A_SG_MAX, n++) {
			put_unaligned_le64(a->bounce_dma + off, a->bounce_sg + 12 * n);
			put_unaligned_le32(min_t(u32, bytes - off, A_SG_MAX), a->bounce_sg + 12 * n + 8);
		}
		a->last_slot = slot;
		if (mxo2_dma_to_host(m, A_RING_BASE + slot * A_SLOT_BYTES, a->bounce_sg_dma, n,
				     a->frame_bytes / 4)) {
			a->dma_errors++;
		} else {
			periods = audio_deliver(m, a->bounce, a->samples[slot]);
			a->real_ns = ktime_get_ns();
			a->blocks++;
			a->samples_total += a->samples[slot];
			while (periods--)
				snd_pcm_period_elapsed(a->substream);
		}
		a->copied++;
		audio_check_start(m);   /* a DMA takes a moment; don't miss a boundary */
	}
	mutex_unlock(&a->mutex);
}

/* ---- debugfs: audio (last block as copied to the host vs. as it is in card memory) ---- */

static void show_frames(struct seq_file *s, const char *what, u32 (*rd)(void *, u32), void *ctx,
			u32 samples, u32 nch)
{
	u32 peak[8] = {}, i, c;

	seq_printf(s, "%s, first 3 sample frames (%u x 32 bit):\n", what, nch);
	for (i = 0; i < samples; i++) {
		for (c = 0; c < nch; c++) {
			s32 v = (s32)rd(ctx, i * nch + c) >> 16;

			if (i < 3)
				seq_printf(s, " %08x", rd(ctx, i * nch + c));
			peak[c] = max_t(u32, peak[c], abs(v));
		}
		if (i < 3)
			seq_putc(s, '\n');
	}
	seq_puts(s, " peak per channel (16-bit):");
	for (c = 0; c < nch; c++)
		seq_printf(s, " %u", peak[c]);
	seq_putc(s, '\n');
}

static u32 rd_bounce(void *ctx, u32 word)
{
	return get_unaligned_le32((u8 *)ctx + word * 4);
}

struct bar1_ctx {
	struct mxo2 *m;
	u32 base;               /* byte offset inside the 8 MiB window */
};

static u32 rd_bar1(void *ctx, u32 word)
{
	struct bar1_ctx *b = ctx;

	return ioread32(b->m->mem + b->base + word * 4);
}

static int audio_show(struct seq_file *s, void *unused)
{
	struct mxo2 *m = s->private;
	struct mxo2_audio *a = &m->audio;
	u32 onboard = A_RING_BASE + a->last_slot * A_SLOT_BYTES;
	u32 samples = min_t(u32, a->samples[a->last_slot], A_MAX_SAMPLES);
	struct bar1_ctx b = { m, onboard & ((8 << 20) - 1) };
	u32 mtc;

	seq_printf(s, "active %d, users %x, armed %llu started %llu copied %llu, played %llu, blocks %llu, "
		   "overruns %llu, DMA errors %llu\n", a->active, m->cap_users, a->armed, a->started, a->copied,
		   a->played, a->blocks, a->overruns, a->dma_errors);
	seq_printf(s, "AudioWDmaControl shadow %08x, AudioInputCtl shadow %08x, TS1 %08x TS2 %08x\n",
		   m->shadow[GAIA_AUDIO_WDMA_CONTROL / 4], m->shadow[GAIA_AUDIO_INPUT_CTL / 4],
		   mxo2_rd(m, GAIA_AUDIO_WDMA_TIME_STAMP1), mxo2_rd(m, GAIA_AUDIO_WDMA_TIME_STAMP2));
	if (!a->bounce || !a->blocks) {
		seq_puts(s, "no block copied yet\n");
		return 0;
	}
	seq_printf(s, "last block: slot %u (card address %#x), %u samples\n", a->last_slot, onboard,
		   samples);
	show_frames(s, "host copy (bounce buffer)", rd_bounce, a->bounce, samples, a->frame_bytes / 4);
	/* card memory through BAR1: window = MemoryTargetControl bits 3:0 (8 MiB each) */
	mutex_lock(&m->lock);
	mtc = m->shadow[GAIA_MEMORY_TARGET_CONTROL / 4];
	mxo2_wr(m, GAIA_MEMORY_TARGET_CONTROL, onboard >> 23);
	show_frames(s, "card memory (BAR1)", rd_bar1, &b, samples, a->frame_bytes / 4);
	mxo2_wr(m, GAIA_MEMORY_TARGET_CONTROL, mtc);
	mutex_unlock(&m->lock);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(audio);

void mxo2_audio_debugfs(struct mxo2 *m)
{
	debugfs_create_file("audio", 0400, m->dbg, m, &audio_fops);
}

/* ---- ALSA ---- */

static const struct snd_pcm_hardware mxo2_pcm_hw = {
	.info = SNDRV_PCM_INFO_MMAP | SNDRV_PCM_INFO_INTERLEAVED | SNDRV_PCM_INFO_BLOCK_TRANSFER |
		SNDRV_PCM_INFO_MMAP_VALID,
	.formats = SNDRV_PCM_FMTBIT_S16_LE,
	.rates = SNDRV_PCM_RATE_32000 | SNDRV_PCM_RATE_44100 | SNDRV_PCM_RATE_48000 |
		 SNDRV_PCM_RATE_88200 | SNDRV_PCM_RATE_96000 | SNDRV_PCM_RATE_176400 |
		 SNDRV_PCM_RATE_192000,
	.rate_min = 32000,
	.rate_max = 192000,
	.channels_min = 2,
	.channels_max = 2,
	.buffer_bytes_max = 1024 * 1024,
	.period_bytes_min = 1024,
	.period_bytes_max = 64 * 1024,
	.periods_min = 2,
	.periods_max = 128,
};

static int pcm_open(struct snd_pcm_substream *ss)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);
	unsigned long flags;
	int ret;

	ss->runtime->hw = mxo2_pcm_hw;
	/* the stream runs at whatever the source sends right now */
	ret = snd_pcm_hw_constraint_single(ss->runtime, SNDRV_PCM_HW_PARAM_RATE, stream_rate(m));
	if (ret < 0)
		return ret;
	spin_lock_irqsave(&m->audio.lock, flags);
	m->audio.substream = ss;
	m->audio.running = false;
	spin_unlock_irqrestore(&m->audio.lock, flags);
	return 0;
}

static void pcm_release_user(struct mxo2 *m)
{
	if (m->audio.holding) {
		m->audio.holding = false;
		mxo2_capture_put(m, MXO2_USER_AUDIO);
	}
}

static int pcm_close(struct snd_pcm_substream *ss)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);
	unsigned long flags;

	pcm_release_user(m);
	mutex_lock(&m->audio.mutex);
	spin_lock_irqsave(&m->audio.lock, flags);
	m->audio.substream = NULL;
	m->audio.running = false;
	spin_unlock_irqrestore(&m->audio.lock, flags);
	mutex_unlock(&m->audio.mutex);
	return 0;
}

static int pcm_hw_free(struct snd_pcm_substream *ss)
{
	pcm_release_user(snd_pcm_substream_chip(ss));
	return 0;
}

/* The capture thread is started here (may sleep), not in trigger (atomic). */
static int pcm_prepare(struct snd_pcm_substream *ss)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);
	unsigned long flags;
	int ret;

	if (!m->audio.holding) {
		ret = mxo2_capture_get(m, MXO2_USER_AUDIO);
		if (ret)
			return ret;
		m->audio.holding = true;
	}
	spin_lock_irqsave(&m->audio.lock, flags);
	m->audio.hw_ptr = 0;
	m->audio.period_pos = 0;
	spin_unlock_irqrestore(&m->audio.lock, flags);
	return 0;
}

static int pcm_trigger(struct snd_pcm_substream *ss, int cmd)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
		m->audio.running = true;        /* already under the substream lock */
		return 0;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
		m->audio.running = false;
		return 0;
	}
	return -EINVAL;
}

static snd_pcm_uframes_t pcm_pointer(struct snd_pcm_substream *ss)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);

	return READ_ONCE(m->audio.hw_ptr);
}

static const struct snd_pcm_ops mxo2_pcm_ops = {
	.open = pcm_open,
	.close = pcm_close,
	.hw_free = pcm_hw_free,
	.prepare = pcm_prepare,
	.trigger = pcm_trigger,
	.pointer = pcm_pointer,
};

/* ---- playback (to HDMI OUT / analog OUT through the playback engine, mxo2_play.c) ---- */

static const struct snd_pcm_hardware mxo2_play_hw = {
	.info = SNDRV_PCM_INFO_MMAP | SNDRV_PCM_INFO_INTERLEAVED | SNDRV_PCM_INFO_BLOCK_TRANSFER |
		SNDRV_PCM_INFO_MMAP_VALID,
	.formats = SNDRV_PCM_FMTBIT_S16_LE,
	.rates = SNDRV_PCM_RATE_48000,
	.rate_min = 48000,
	.rate_max = 48000,
	.channels_min = 2,
	.channels_max = 2,
	.buffer_bytes_max = 256 * 1024,
	.period_bytes_min = 1024,
	.period_bytes_max = 64 * 1024,
	.periods_min = 2,
	.periods_max = 64,
};

/*
 * Engine: fill `samples` stereo frames in the card layout (2 x 32-bit words, sample in the upper half) from the
 * playback stream, or silence when it is not running.
 */
void mxo2_audio_play_fetch(struct mxo2 *m, u8 *dst, u32 samples)
{
	struct mxo2_audio *a = &m->audio;
	struct snd_pcm_runtime *rt;
	struct snd_pcm_substream *ss;
	unsigned int periods = 0;
	unsigned long flags;
	u32 i;

	mutex_lock(&a->play_mutex);     /* vs. play_close dropping the substream */
	spin_lock_irqsave(&a->lock, flags);
	ss = a->play_ss;
	if (!ss || !a->play_running) {
		spin_unlock_irqrestore(&a->lock, flags);
		mutex_unlock(&a->play_mutex);
		memset(dst, 0, samples * 8);
		return;
	}
	rt = ss->runtime;
	for (i = 0; i < samples; i++) {
		const s16 *src = (const s16 *)(rt->dma_area + frames_to_bytes(rt, a->play_ptr));

		put_unaligned_le32((u32)(u16)src[0] << 16, dst + i * 8);       /* left */
		put_unaligned_le32((u32)(u16)src[1] << 16, dst + i * 8 + 4);   /* right */
		if (++a->play_ptr >= rt->buffer_size)
			a->play_ptr = 0;
		if (++a->play_period_pos >= rt->period_size) {
			a->play_period_pos = 0;
			periods++;
		}
	}
	a->play_samples += samples;
	spin_unlock_irqrestore(&a->lock, flags);
	while (periods--)
		snd_pcm_period_elapsed(ss);
	mutex_unlock(&a->play_mutex);
}

static int play_open(struct snd_pcm_substream *ss)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);
	unsigned long flags;

	ss->runtime->hw = mxo2_play_hw;
	spin_lock_irqsave(&m->audio.lock, flags);
	m->audio.play_ss = ss;
	m->audio.play_running = false;
	spin_unlock_irqrestore(&m->audio.lock, flags);
	return 0;
}

/*
 * The playback engine takes the whole card (pass-through off, capture refused), so it is started only when the
 * stream really starts playing (trigger START), not at prepare: alsa-lib prepares on every hw_params, and
 * PipeWire's card probing does that on both directions - taking the card there made the capture fail and
 * PipeWire drop the input. Trigger runs atomic, so a work item starts / stops the engine.
 */
static void play_work_fn(struct work_struct *work)
{
	struct mxo2 *m = container_of(work, struct mxo2, audio.play_work);
	struct mxo2_audio *a = &m->audio;

	mutex_lock(&a->play_mutex);
	if (READ_ONCE(a->play_running) && !a->play_holding) {
		mutex_unlock(&a->play_mutex);
		if (mxo2_play_engine_get(m, MXO2_PLAY_AUDIO, -1)) {
			dev_info(&m->pdev->dev, "audio out: card busy (capture runs), not playing\n");
			return;
		}
		mutex_lock(&a->play_mutex);
		a->play_holding = true;
	}
	mutex_unlock(&a->play_mutex);
}

static void play_release_engine(struct mxo2 *m)
{
	cancel_work_sync(&m->audio.play_work);
	if (m->audio.play_holding) {
		m->audio.play_holding = false;
		mxo2_play_engine_put(m, MXO2_PLAY_AUDIO);
	}
}

static int play_close(struct snd_pcm_substream *ss)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);
	unsigned long flags;

	play_release_engine(m);
	mutex_lock(&m->audio.play_mutex);
	spin_lock_irqsave(&m->audio.lock, flags);
	m->audio.play_ss = NULL;
	m->audio.play_running = false;
	spin_unlock_irqrestore(&m->audio.lock, flags);
	mutex_unlock(&m->audio.play_mutex);
	return 0;
}

static int play_hw_free(struct snd_pcm_substream *ss)
{
	play_release_engine(snd_pcm_substream_chip(ss));
	return 0;
}

static int play_prepare(struct snd_pcm_substream *ss)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);
	unsigned long flags;

	spin_lock_irqsave(&m->audio.lock, flags);
	m->audio.play_ptr = 0;
	m->audio.play_period_pos = 0;
	spin_unlock_irqrestore(&m->audio.lock, flags);
	return 0;
}

static int play_trigger(struct snd_pcm_substream *ss, int cmd)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);

	switch (cmd) {
	case SNDRV_PCM_TRIGGER_START:
	case SNDRV_PCM_TRIGGER_RESUME:
		m->audio.play_running = true;
		schedule_work(&m->audio.play_work);     /* engine start may sleep */
		return 0;
	case SNDRV_PCM_TRIGGER_STOP:
	case SNDRV_PCM_TRIGGER_SUSPEND:
		m->audio.play_running = false;  /* the engine keeps running (silence) until hw_free / close */
		return 0;
	}
	return -EINVAL;
}

static snd_pcm_uframes_t play_pointer(struct snd_pcm_substream *ss)
{
	struct mxo2 *m = snd_pcm_substream_chip(ss);

	return READ_ONCE(m->audio.play_ptr);
}

static const struct snd_pcm_ops mxo2_play_ops = {
	.open = play_open,
	.close = play_close,
	.hw_free = play_hw_free,
	.prepare = play_prepare,
	.trigger = play_trigger,
	.pointer = play_pointer,
};

/* read-only control "HDMI Sample Rate": current source rate in Hz (0 = no audio) */
static int rate_ctl_info(struct snd_kcontrol *kc, struct snd_ctl_elem_info *uinfo)
{
	uinfo->type = SNDRV_CTL_ELEM_TYPE_INTEGER;
	uinfo->count = 1;
	uinfo->value.integer.min = 0;
	uinfo->value.integer.max = 192000;
	return 0;
}

static int rate_ctl_get(struct snd_kcontrol *kc, struct snd_ctl_elem_value *v)
{
	struct mxo2 *m = snd_kcontrol_chip(kc);

	v->value.integer.value[0] = m->in_audio_rate;
	return 0;
}

static const struct snd_kcontrol_new rate_ctl = {
	.iface = SNDRV_CTL_ELEM_IFACE_PCM,
	.name = "HDMI Sample Rate",
	.access = SNDRV_CTL_ELEM_ACCESS_READ | SNDRV_CTL_ELEM_ACCESS_VOLATILE,
	.info = rate_ctl_info,
	.get = rate_ctl_get,
};

int mxo2_audio_register(struct mxo2 *m)
{
	struct mxo2_audio *a = &m->audio;
	struct device *dev = &m->pdev->dev;
	struct snd_card *card;
	struct snd_pcm *pcm;
	int ret;

	spin_lock_init(&a->lock);
	mutex_init(&a->mutex);
	mutex_init(&a->play_mutex);
	INIT_WORK(&a->play_work, play_work_fn);
	a->bounce = dma_alloc_coherent(dev, A_SLOT_BYTES, &a->bounce_dma, GFP_KERNEL);
	a->bounce_sg = dma_alloc_coherent(dev, PAGE_SIZE, &a->bounce_sg_dma, GFP_KERNEL);
	if (!a->bounce || !a->bounce_sg) {
		ret = -ENOMEM;
		goto err_mem;
	}
	ret = snd_card_new(dev, -1, NULL, THIS_MODULE, 0, &card);
	if (ret)
		goto err_mem;
	strscpy(card->driver, "mxo2", sizeof(card->driver));
	strscpy(card->shortname, "MXO2 Mini", sizeof(card->shortname));
	snprintf(card->longname, sizeof(card->longname), "Matrox MXO2 Mini audio (in: HDMI or analog, out) at %s",
		 pci_name(m->pdev));
	ret = snd_pcm_new(card, "MXO2 HDMI", 0, 1, 1, &pcm);
	if (ret)
		goto err_card;
	pcm->private_data = m;
	strscpy(pcm->name, "MXO2 Mini", sizeof(pcm->name));
	snd_pcm_set_ops(pcm, SNDRV_PCM_STREAM_CAPTURE, &mxo2_pcm_ops);
	snd_pcm_set_ops(pcm, SNDRV_PCM_STREAM_PLAYBACK, &mxo2_play_ops);
	snd_pcm_set_managed_buffer_all(pcm, SNDRV_DMA_TYPE_VMALLOC, NULL, 0, 0);
	ret = snd_ctl_add(card, snd_ctl_new1(&rate_ctl, m));
	if (ret)
		goto err_card;
	ret = snd_card_register(card);
	if (ret)
		goto err_card;
	a->card = card;
	dev_info(dev, "ALSA card %d: %s\n", card->number, card->longname);
	return 0;
err_card:
	snd_card_free(card);
err_mem:
	if (a->bounce_sg)
		dma_free_coherent(dev, PAGE_SIZE, a->bounce_sg, a->bounce_sg_dma);
	if (a->bounce)
		dma_free_coherent(dev, A_SLOT_BYTES, a->bounce, a->bounce_dma);
	a->bounce = a->bounce_sg = NULL;
	return ret;
}

void mxo2_audio_unregister(struct mxo2 *m)
{
	struct mxo2_audio *a = &m->audio;
	struct device *dev = &m->pdev->dev;

	if (a->card)
		snd_card_free(a->card);        /* closes streams -> releases the capture user */
	a->card = NULL;
	if (a->bounce_sg)
		dma_free_coherent(dev, PAGE_SIZE, a->bounce_sg, a->bounce_sg_dma);
	if (a->bounce)
		dma_free_coherent(dev, A_SLOT_BYTES, a->bounce, a->bounce_dma);
	a->bounce = a->bounce_sg = NULL;
}
