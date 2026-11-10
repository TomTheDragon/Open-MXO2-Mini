// SPDX-License-Identifier: GPL-2.0
/*
 * Matrox MXO2 - V4L2 capture device "Matrox MXO2 Mini": one device, the card's inputs are its V4L2 inputs
 * (0 HDMI, 1 Composite, 2 S-Video, 3 Component; VIDIOC_S_INPUT). Apps that can't select inputs (PipeWire) use the
 * "Input" menu control (Auto / one input); Auto follows the input with a signal while nothing streams. The format
 * list follows the selected input. Format YUYV. HDMI: the detected size (1080i woven). Analog inputs (ADV7181
 * decoder): 720x480/576 (woven 480i, or line-doubled 240p), 320/256 x 240/288 (240p, or one field per frame).
 * Buffers are videobuf2 scatter-gather buffers; every buffer gets its own card SG list, so
 * the card's PCI DMA writes straight into it. For YCbCr 4:2:2 sources the AD9398 delivers the
 * chroma bytes as [junk, ~C7, C6..C1] (re/NOTES.md, "Camera at 1080p30"), which is undone in
 * place with a lookup table: C = ((b << 1) | 1) ^ 0x80 (7-bit chroma).
 */

#include <linux/dma-mapping.h>
#include <linux/gcd.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <media/v4l2-event.h>
#include <media/v4l2-ioctl.h>
#include <media/videobuf2-dma-sg.h>

#include "mxo2.h"

static int chroma_fix = -1;
module_param(chroma_fix, int, 0644);
MODULE_PARM_DESC(chroma_fix, "Undo the AD9398 chroma bit layout: -1 = auto (YCbCr 4:2:2 sources), "
		 "0 = never, 1 = always");

/*
 * Composite 240p/288p (game consoles) can also be delivered 256 or 320 pixels wide, sampled 1:1 on the console's
 * pixel grid. The decoder samples at 13.5 MHz locked to the line (858 samples per NTSC line, 864 PAL), and so
 * does the console's pixel clock, so the console's pixels sit at fixed sample positions: pitch = samples per line
 * / console dots per line (NES/SNES/Famicom 256 px: 341 dots -> 2.516 samples; Mega Drive / PlayStation 320 px:
 * 420 dots -> 2.043). Each output pixel is the decoder signal at the centre of a console pixel (linear between
 * the two nearest samples). The picture centre: NES test bars 43.29 .. 687.42 -> 365.36 (re/notes/analog-240p.raw).
 */
static uint retro_center = 36536;
module_param(retro_center, uint, 0644);
MODULE_PARM_DESC(retro_center, "Composite 240p/288p at 256/320 wide: picture centre in 1/100 decoder samples "
		 "(default 36536; +-100 moves the picture by 1/2.5 console pixel)");

/* PAL: SNES picture edges 29.3 .. 677.0 in the 864-sample line -> 353.2 (re/notes/pal/v4l2-288p.raw) */
static uint retro_center_pal = 35320;
module_param(retro_center_pal, uint, 0644);
MODULE_PARM_DESC(retro_center_pal, "As retro_center, for PAL 288p (default 35320)");

static uint retro_dots256 = 341;
module_param(retro_dots256, uint, 0644);
MODULE_PARM_DESC(retro_dots256, "Console dots per line for the 256-wide format (default 341: NES, Famicom, SNES)");

static uint retro_dots320 = 420;
module_param(retro_dots320, uint, 0644);
MODULE_PARM_DESC(retro_dots320, "Console dots per line for the 320-wide format (default 420: Mega Drive, PlayStation)");

#define RETRO_SRC_W 720
static const u32 retro_widths[] = { 320, 256 };

static u8 chroma_lut[256];

static void chroma_lut_init(void)
{
	int b;

	for (b = 0; b < 256; b++)
		chroma_lut[b] = (((b << 1) | 1) & 0xff) ^ 0x80;
}

static void fix_chroma(u8 *p, size_t bytes)
{
	size_t i;

	for (i = 1; i < bytes; i += 2)  /* Y0 U Y1 V: every odd byte is chroma */
		p[i] = chroma_lut[p[i]];
}

/*
 * Analog sizes, offered for any analog signal (480i or 240p, NTSC or PAL) and without one: the full frame
 * (720x480/576: woven fields from a 480i signal, each line doubled from a 240p signal) and one field (240/288 lines:
 * the 240p picture, or one field per frame from a 480i signal) at 320 and 256 wide, on the console's pixel grid.
 * (No 720-wide field size: the line-doubled full frame shows the same picture.)
 */
#define ANALOG_SIZES (1 + ARRAY_SIZE(retro_widths))

static struct mxo2_sig *vsig(struct mxo2_vid *vid)
{
	return &vid->m->sig[vid->m->input];
}

static bool analog_sizes(struct mxo2_vid *vid)
{
	return vid->m->input != MXO2_INPUT_HDMI;
}

static u32 field_lines(struct mxo2_vid *vid)
{
	struct mxo2_sig *s = vsig(vid);

	if (s->width)
		vid->pal = s->height == 576 || s->height == 288;
	return vid->pal ? 288 : 240;
}

static void analog_size(struct mxo2_vid *vid, unsigned int i, u32 *w, u32 *h)
{
	*w = i ? retro_widths[i - 1] : RETRO_SRC_W;
	*h = i ? field_lines(vid) : 2 * field_lines(vid);
}

/* A supported size for a request (exact, else the analog default 720 x full height). */
static void pick_size(struct mxo2_vid *vid, u32 want_w, u32 want_h, u32 *w, u32 *h)
{
	struct mxo2_sig *s = vsig(vid);
	unsigned int i;

	if (analog_sizes(vid)) {
		for (i = 0; i < ANALOG_SIZES; i++) {
			analog_size(vid, i, w, h);
			if (*w == want_w && *h == want_h)
				return;
		}
		analog_size(vid, 0, w, h);
		return;
	}
	*w = s->width ? s->width : vid->hdmi_width;
	*h = s->width ? s->height : vid->hdmi_height;
}

/* Woven (interlaced) frames for a size? */
static bool size_woven(struct mxo2_vid *vid, u32 h)
{
	struct mxo2_sig *s = vsig(vid);

	if (!analog_sizes(vid))
		return s->width ? s->interlaced : vid->hdmi_interlaced;
	return s->interlaced && h >= 480;
}

static u32 size_fps(struct mxo2_vid *vid, u32 h)
{
	struct mxo2_sig *s = vsig(vid);
	u32 rate;

	if (!analog_sizes(vid))
		return s->fps ? s->fps : 30;
	rate = field_lines(vid) == 288 ? 50 : 60;               /* fields per second */
	return size_woven(vid, h) ? rate / 2 : rate;
}

/* Field-sized composite format at a scaled width (bounce + horizontal scaler). */
static bool scaled_width(struct mxo2_vid *vid)
{
	return analog_sizes(vid) && vid->width != RETRO_SRC_W;
}

/* Picture span in 1/100 decoder samples for an output width: line samples x width / console dots per line. */
static u32 retro_span100(u32 w, u32 line_samples)
{
	u32 dots = w == 320 ? retro_dots320 : retro_dots256;

	if (dots < w + 16 || dots > 2 * line_samples)
		dots = w == 320 ? 420 : 341;
	return line_samples * 100 * w / dots;
}

/*
 * Taps: t[0..w-1] luma, sample position of the centre of console pixel i; t[w..w+w/2-1] chroma, position of the
 * centre of pixel pair p, in chroma samples (one per two decoder samples, co-sited with the even one).
 * Each tap = k0 + w[1]/256 (linear between k0 and k0 + 1).
 */
static struct mxo2_scale_tap *build_taps(u32 w, u32 line_samples)
{
	s64 span = (s64)retro_span100(w, line_samples) * 256 / 100;     /* 1/256 samples */
	u32 centre = line_samples == 864 ? retro_center_pal : retro_center;
	s64 left = (s64)centre * 256 / 100 - span / 2, x;
	struct mxo2_scale_tap *t;
	u32 i;

	t = kcalloc(w + w / 2, sizeof(*t), GFP_KERNEL);
	if (!t)
		return NULL;
	for (i = 0; i < w + w / 2; i++) {
		bool luma = i < w;

		if (luma)
			x = left + div_s64((2 * i + 1) * span, 2 * w);          /* pixel centre */
		else
			x = div_s64(left + div_s64((2 * (i - w) + 1) * span, w), 2); /* pair centre, chroma units */
		x = clamp_t(s64, x, 0, (luma ? RETRO_SRC_W - 1 : RETRO_SRC_W / 2 - 1) * 256 - 1);
		t[i].k0 = x >> 8;
		t[i].n = 2;
		t[i].w[1] = x & 255;
		t[i].w[0] = 256 - t[i].w[1];
	}
	return t;
}

/*
 * Luma notch at the colour subcarrier (3.58 MHz = 3.771 samples at 13.5 MHz): the mean of the signal 1/4 period
 * left and right of each sample = taps 121/14/121 (/256), zero at fsc. Removes the dots that consoles' 240p leaves
 * in luma; edges stay within ~1.5 console pixels. Chroma untouched.
 */
static void fsc_notch_line(u8 *line, u32 samples)
{
	u32 k, prev = line[0], cur;

	for (k = 1; k + 1 < samples; k++) {
		cur = line[2 * k];
		line[2 * k] = (prev * 121 + cur * 14 + line[2 * k + 2] * 121 + 128) >> 8;
		prev = cur;
	}
}

/* Composite input with the software notch on? (sharpness 1) */
static bool fsc_filter(struct mxo2_vid *vid)
{
	return vid->m->input == MXO2_INPUT_COMPOSITE && vid->m->sharpness == 1;
}

/* One YUYV line, 720 samples -> w pixels at the console's pixel centres. */
static void scale_line(const u8 *src, u8 *dst, const struct mxo2_scale_tap *t, u32 w)
{
	u32 p, h;

	for (p = 0; p < w / 2; p++) {
		const struct mxo2_scale_tap *c = &t[w + p];
		u32 q = c->k0 * 4;

		for (h = 0; h < 2; h++) {
			const struct mxo2_scale_tap *tp = &t[2 * p + h];
			u32 k = tp->k0 * 2;

			dst[4 * p + 2 * h] = (src[k] * tp->w[0] + src[k + 2] * tp->w[1] + 128) >> 8;
		}
		dst[4 * p + 1] = (src[q + 1] * c->w[0] + src[q + 5] * c->w[1] + 128) >> 8;
		dst[4 * p + 3] = (src[q + 3] * c->w[0] + src[q + 7] * c->w[1] + 128) >> 8;
	}
}

static struct mxo2_vbuf *to_mbuf(struct vb2_buffer *vb)
{
	return container_of(to_vb2_v4l2_buffer(vb), struct mxo2_vbuf, vb);
}

static u32 frame_bytes(struct mxo2_vid *vid)
{
	return vid->width * vid->height * 2;
}

/* Field layout of woven frames: HDMI 1080i top first (Int0 field flag); analog order is not detected. */
static enum v4l2_field woven_field(bool analog)
{
	return analog ? V4L2_FIELD_INTERLACED : V4L2_FIELD_INTERLACED_TB;
}

/*
 * Current format, for the selected input: from its signal; without one HDMI keeps its last size (1080p at first),
 * analog the requested size. Fixed while buffers exist (the input can't change then either).
 */
static void update_format(struct mxo2_vid *vid)
{
	struct mxo2_sig *s = vsig(vid);

	if (vb2_is_busy(&vid->queue))
		return;
	if (!analog_sizes(vid) && s->width) {
		vid->hdmi_width = s->width;
		vid->hdmi_height = s->height;
		vid->hdmi_interlaced = s->interlaced;
	}
	pick_size(vid, vid->req_width, vid->req_height, &vid->width, &vid->height);
	vid->fmt_interlaced = size_woven(vid, vid->height);
	vid->fmt_input = vid->m->input;
}

bool mxo2_video_busy(struct mxo2 *m)
{
	return vb2_is_busy(&m->vid.queue);
}

/* Runs on the ordered fix_wq: chroma fix (HDMI) or subcarrier notch (composite), then hand the buffer over. */
static void fix_work_fn(struct work_struct *work)
{
	struct mxo2_vbuf *buf = container_of(work, struct mxo2_vbuf, fix_work);
	struct mxo2_vid *vid = buf->vid;
	struct sg_table *sgt = vb2_dma_sg_plane_desc(&buf->vb.vb2_buf, 0);
	void *vaddr = vb2_plane_vaddr(&buf->vb.vb2_buf, 0);

	if (vaddr) {
		dma_sync_sgtable_for_cpu(&vid->m->pdev->dev, sgt, DMA_FROM_DEVICE);
		if (buf->notch) {
			u32 r;

			for (r = 0; r < vid->height; r++)
				fsc_notch_line((u8 *)vaddr + r * vid->width * 2, vid->width);
		} else {
			fix_chroma(vaddr, frame_bytes(vid));
		}
	}
	vb2_buffer_done(&buf->vb.vb2_buf, VB2_BUF_STATE_DONE);
}

/* ---- capture thread hook ---- */

void mxo2_v4l2_frame(struct mxo2 *m, unsigned int slot)
{
	struct mxo2_vid *vid = READ_ONCE(m->streaming);
	struct mxo2_vbuf *buf;
	unsigned long flags;
	bool fix;
	int ret;

	if (!vid)
		return;
	spin_lock_irqsave(&vid->buf_lock, flags);
	buf = list_first_entry_or_null(&vid->buf_list, struct mxo2_vbuf, list);
	if (buf)
		list_del(&buf->list);
	spin_unlock_irqrestore(&vid->buf_lock, flags);
	if (!buf) {
		m->stats.no_buffer++;
		return;
	}

	if (vid->use_bounce) {  /* 720-wide field into the bounce buffer, then scale and / or double lines */
		u8 *dst = vb2_plane_vaddr(&buf->vb.vb2_buf, 0);
		bool notch = fsc_filter(vid) && vid->linebuf;
		/* last valid field line; a PAL 288p field read one line later has its last line unwritten */
		u32 last = (vid->line_double ? vid->height / 2 : vid->height) - 1 - !!m->slot_skip[slot];
		u32 r;

		mxo2_hbuf_sync(m, &vid->bounce, false);
		ret = mxo2_dma_to_host(m, mxo2_slot_offset(m, slot), vid->bounce.sg_dma,
				       vid->bounce.sg_entries, m->cap_line_bytes / 4);
		if (!ret && !dst)
			ret = -ENOMEM;
		if (!ret) {
			mxo2_hbuf_sync(m, &vid->bounce, true);
			for (r = 0; r < vid->height; r++) {
				const u8 *src = (u8 *)vid->bounce.vaddr +
						min(vid->line_double ? r / 2 : r, last) * m->cap_line_bytes;

				if (notch && (!vid->line_double || !(r & 1))) {
					memcpy(vid->linebuf, src, m->cap_line_bytes);
					fsc_notch_line(vid->linebuf, m->cap_line_bytes / 2);
				}
				if (notch)
					src = vid->linebuf;
				if (vid->taps)
					scale_line(src, dst + r * vid->width * 2, vid->taps, vid->width);
				else
					memcpy(dst + r * vid->width * 2, src, vid->width * 2);
			}
		}
	} else {
		ret = mxo2_dma_to_host(m, mxo2_slot_offset(m, slot), buf->card_sg_dma,
				       buf->card_sg_entries, vid->width * 2 / 4);
		if (!ret && m->slot_skip[slot] && vid->height >= 2) {
			/* PAL 288p field read one line later: its last line was never written, repeat the one above */
			u8 *dst = vb2_plane_vaddr(&buf->vb.vb2_buf, 0);
			u32 stride = vid->width * 2;

			if (dst)
				memcpy(dst + (vid->height - 1) * stride, dst + (vid->height - 2) * stride, stride);
		}
	}
	if (ret) {
		vb2_buffer_done(&buf->vb.vb2_buf, VB2_BUF_STATE_ERROR);
		return;
	}
	m->stats.dma_done++;
	buf->vb.vb2_buf.timestamp = ktime_get_ns();
	buf->vb.sequence = vid->sequence++;
	/* what the running capture really produces (its mode is fixed while it runs) */
	buf->vb.field = m->cap_interlaced ? woven_field(m->cap_analog) : V4L2_FIELD_NONE;
	vb2_set_plane_payload(&buf->vb.vb2_buf, 0, frame_bytes(vid));
	fix = !m->cap_analog && (chroma_fix > 0 ||
		(chroma_fix < 0 && m->rx_state == MXO2_RX_HDMI && m->rx_colorspace == 1));
	buf->notch = fsc_filter(vid) && !vid->use_bounce;      /* 720-wide composite frame: notch in place */
	if (buf->notch)
		fix = true;
	if (fix) {
		/* ~1M table lookups: done off the capture thread so it can re-arm in time */
		buf->vid = vid;
		INIT_WORK(&buf->fix_work, fix_work_fn);
		queue_work(m->fix_wq, &buf->fix_work);
	} else {
		vb2_buffer_done(&buf->vb.vb2_buf, VB2_BUF_STATE_DONE);
	}
}

static void return_all(struct mxo2_vid *vid, enum vb2_buffer_state state)
{
	struct mxo2_vbuf *buf, *tmp;
	unsigned long flags;

	spin_lock_irqsave(&vid->buf_lock, flags);
	list_for_each_entry_safe(buf, tmp, &vid->buf_list, list) {
		list_del(&buf->list);
		vb2_buffer_done(&buf->vb.vb2_buf, state);
	}
	spin_unlock_irqrestore(&vid->buf_lock, flags);
}

/* ---- vb2 queue ---- */

static int queue_setup(struct vb2_queue *q, unsigned int *nbuffers, unsigned int *nplanes,
		       unsigned int sizes[], struct device *alloc_devs[])
{
	struct mxo2_vid *vid = vb2_get_drv_priv(q);

	if (*nplanes)
		return sizes[0] < frame_bytes(vid) ? -EINVAL : 0;
	*nplanes = 1;
	sizes[0] = frame_bytes(vid);
	if (*nbuffers < 3)
		*nbuffers = 3;
	return 0;
}

static int buf_init(struct vb2_buffer *vb)
{
	struct mxo2_vid *vid = vb2_get_drv_priv(vb->vb2_queue);
	struct mxo2_vbuf *buf = to_mbuf(vb);
	size_t max_entries;

	/* worst case: every PAGE run split at 0x1f000, plus one per segment */
	max_entries = DIV_ROUND_UP(vb2_plane_size(vb, 0), PAGE_SIZE) + 1;
	buf->card_sg_size = PAGE_ALIGN(max_entries * 12);
	buf->card_sg = dma_alloc_coherent(&vid->m->pdev->dev, buf->card_sg_size, &buf->card_sg_dma,
					  GFP_KERNEL);
	return buf->card_sg ? 0 : -ENOMEM;
}

static int buf_prepare(struct vb2_buffer *vb)
{
	struct mxo2_vid *vid = vb2_get_drv_priv(vb->vb2_queue);
	struct mxo2_vbuf *buf = to_mbuf(vb);
	struct sg_table *sgt = vb2_dma_sg_plane_desc(vb, 0);

	if (vb2_plane_size(vb, 0) < frame_bytes(vid))
		return -EINVAL;
	buf->card_sg_entries = mxo2_build_card_sg(buf->card_sg, sgt->sgl, sgt->nents, frame_bytes(vid));
	if (buf->card_sg_entries * 3 > 0x1fff)  /* PciDmaSGCount is 13 bits (dwords) */
		return -EINVAL;
	return 0;
}

static void buf_cleanup(struct vb2_buffer *vb)
{
	struct mxo2_vid *vid = vb2_get_drv_priv(vb->vb2_queue);
	struct mxo2_vbuf *buf = to_mbuf(vb);

	if (buf->card_sg)
		dma_free_coherent(&vid->m->pdev->dev, buf->card_sg_size, buf->card_sg, buf->card_sg_dma);
	buf->card_sg = NULL;
}

static void buf_queue(struct vb2_buffer *vb)
{
	struct mxo2_vid *vid = vb2_get_drv_priv(vb->vb2_queue);
	struct mxo2_vbuf *buf = to_mbuf(vb);
	unsigned long flags;

	spin_lock_irqsave(&vid->buf_lock, flags);
	list_add_tail(&buf->list, &vid->buf_list);
	spin_unlock_irqrestore(&vid->buf_lock, flags);
}

static void free_scaler(struct mxo2_vid *vid)
{
	kfree(vid->linebuf);
	vid->linebuf = NULL;
	if (vid->use_bounce)
		mxo2_hbuf_free(vid->m, &vid->bounce);
	kfree(vid->taps);
	vid->taps = NULL;
	vid->use_bounce = false;
	vid->line_double = false;
}

/* Claim the card's capture path; the card captures from the input the format was made for. */
static int start_streaming(struct vb2_queue *q, unsigned int count)
{
	struct mxo2_vid *vid = vb2_get_drv_priv(q);
	struct mxo2 *m = vid->m;
	int ret;

	if (cmpxchg(&m->streaming, NULL, vid) != NULL) {
		ret = -EBUSY;
		goto err;
	}
	/* Auto may have switched between S_FMT and REQBUFS: go back to the input the buffers are sized for */
	if (m->input != vid->fmt_input) {
		ret = mxo2_switch_input(m, vid->fmt_input);
		if (ret)
			goto err_release;
	}
	vid->sequence = 0;
	/* 240p/288p signal at full height: one packed field per frame, each line delivered twice */
	vid->line_double = analog_sizes(vid) && vid->height == 2 * field_lines(vid) && !m->in_interlaced;
	if (scaled_width(vid) || vid->line_double) {
		ret = 0;
		if (scaled_width(vid)) {
			vid->taps = build_taps(vid->width, field_lines(vid) == 288 ? 864 : 858);
			ret = vid->taps ? 0 : -ENOMEM;
		}
		if (!ret) {
			vid->linebuf = kmalloc(RETRO_SRC_W * 2, GFP_KERNEL);
			ret = vid->linebuf ? 0 : -ENOMEM;
		}
		if (!ret)
			ret = mxo2_hbuf_alloc(m, &vid->bounce, RETRO_SRC_W * 2 * field_lines(vid));
		vid->use_bounce = !ret;
		if (ret)
			goto err_scaler;
	}
	ret = mxo2_capture_get(m, MXO2_USER_V4L2);
	if (ret)
		goto err_scaler;
	return 0;
err_scaler:
	free_scaler(vid);
err_release:
	WRITE_ONCE(m->streaming, NULL);
err:
	return_all(vid, VB2_BUF_STATE_QUEUED);
	return ret;
}

static void stop_streaming(struct vb2_queue *q)
{
	struct mxo2_vid *vid = vb2_get_drv_priv(q);
	struct mxo2 *m = vid->m;

	mxo2_capture_put(m, MXO2_USER_V4L2);
	flush_workqueue(m->fix_wq);     /* buffers still being fixed complete first */
	return_all(vid, VB2_BUF_STATE_ERROR);
	free_scaler(vid);
	WRITE_ONCE(m->streaming, NULL);
}

static const struct vb2_ops mxo2_qops = {
	.queue_setup = queue_setup,
	.buf_init = buf_init,
	.buf_prepare = buf_prepare,
	.buf_cleanup = buf_cleanup,
	.buf_queue = buf_queue,
	.start_streaming = start_streaming,
	.stop_streaming = stop_streaming,
	MXO2_VB2_WAIT_OPS
};

/* ---- ioctls ---- */

const char *const mxo2_input_names[MXO2_NUM_INPUTS] = { "HDMI", "Composite", "S-Video", "Component" };

static int vidioc_querycap(struct file *file, void *priv, struct v4l2_capability *cap)
{
	struct mxo2_vid *vid = video_drvdata(file);

	strscpy(cap->driver, "mxo2", sizeof(cap->driver));
	strscpy(cap->card, "Matrox MXO2 Mini", sizeof(cap->card));
	snprintf(cap->bus_info, sizeof(cap->bus_info), "PCI:%s", pci_name(vid->m->pdev));
	return 0;
}

static int vidioc_enum_fmt(struct file *file, void *priv, struct v4l2_fmtdesc *f)
{
	if (f->index)
		return -EINVAL;
	f->pixelformat = V4L2_PIX_FMT_YUYV;
	return 0;
}

static void fill_fmt(struct mxo2_vid *vid, struct v4l2_pix_format *pix, u32 width, u32 height)
{
	pix->width = width;
	pix->height = height;
	pix->pixelformat = V4L2_PIX_FMT_YUYV;
	pix->field = size_woven(vid, height) ? woven_field(analog_sizes(vid)) : V4L2_FIELD_NONE;
	pix->bytesperline = width * 2;
	pix->sizeimage = width * height * 2;
	pix->colorspace = height >= 720 ? V4L2_COLORSPACE_REC709 : V4L2_COLORSPACE_SMPTE170M;
	pix->ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT;
	pix->quantization = V4L2_QUANTIZATION_LIM_RANGE;
	pix->xfer_func = V4L2_XFER_FUNC_DEFAULT;
}

static int vidioc_g_fmt(struct file *file, void *priv, struct v4l2_format *f)
{
	struct mxo2_vid *vid = video_drvdata(file);

	update_format(vid);
	fill_fmt(vid, &f->fmt.pix, vid->width, vid->height);
	return 0;
}

/* HDMI: the size follows the signal. Analog: any of the analog sizes (see ANALOG_SIZES). */
static int vidioc_try_fmt(struct file *file, void *priv, struct v4l2_format *f)
{
	struct mxo2_vid *vid = video_drvdata(file);
	u32 w, h;

	update_format(vid);
	w = vid->width;
	h = vid->height;
	if (!vb2_is_busy(&vid->queue))
		pick_size(vid, f->fmt.pix.width, f->fmt.pix.height, &w, &h);
	fill_fmt(vid, &f->fmt.pix, w, h);
	return 0;
}

static int vidioc_s_fmt(struct file *file, void *priv, struct v4l2_format *f)
{
	struct mxo2_vid *vid = video_drvdata(file);
	u32 w, h;

	update_format(vid);
	pick_size(vid, f->fmt.pix.width, f->fmt.pix.height, &w, &h);
	if (vb2_is_busy(&vid->queue)) {
		if (w != vid->width || h != vid->height)
			return -EBUSY;
	} else if (analog_sizes(vid)) {
		vid->req_width = w;
		vid->req_height = h;
		vid->width = w;
		vid->height = h;
		vid->fmt_interlaced = size_woven(vid, h);
	}
	fill_fmt(vid, &f->fmt.pix, vid->width, vid->height);
	return 0;
}

static int vidioc_enum_framesizes(struct file *file, void *priv, struct v4l2_frmsizeenum *fs)
{
	struct mxo2_vid *vid = video_drvdata(file);

	if (fs->pixel_format != V4L2_PIX_FMT_YUYV)
		return -EINVAL;
	update_format(vid);
	if (fs->index >= (analog_sizes(vid) ? ANALOG_SIZES : 1))
		return -EINVAL;
	fs->type = V4L2_FRMSIZE_TYPE_DISCRETE;
	if (analog_sizes(vid)) {
		analog_size(vid, fs->index, &fs->discrete.width, &fs->discrete.height);
	} else {
		fs->discrete.width = vid->width;
		fs->discrete.height = vid->height;
	}
	return 0;
}

static int vidioc_enum_frameintervals(struct file *file, void *priv, struct v4l2_frmivalenum *fi)
{
	struct mxo2_vid *vid = video_drvdata(file);
	u32 w, h;

	if (fi->index || fi->pixel_format != V4L2_PIX_FMT_YUYV)
		return -EINVAL;
	update_format(vid);
	pick_size(vid, fi->width, fi->height, &w, &h);
	if (w != fi->width || h != fi->height)
		return -EINVAL;
	fi->type = V4L2_FRMIVAL_TYPE_DISCRETE;
	fi->discrete.numerator = 1;
	fi->discrete.denominator = size_fps(vid, h);
	return 0;
}

static int vidioc_g_parm(struct file *file, void *priv, struct v4l2_streamparm *sp)
{
	struct mxo2_vid *vid = video_drvdata(file);

	if (sp->type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
		return -EINVAL;
	update_format(vid);
	sp->parm.capture.capability = V4L2_CAP_TIMEPERFRAME;
	sp->parm.capture.readbuffers = 3;
	sp->parm.capture.timeperframe.numerator = 1;
	sp->parm.capture.timeperframe.denominator = size_fps(vid, vid->height);
	return 0;
}

/*
 * Pixel aspect (height / width of a pixel, as V4L2 defines it): square for HD; BT.601 values for SD,
 * 4:3 or 16:9 anamorphic (vivid uses the same: NTSC 11/10, PAL 54/59).
 */
static int vidioc_g_pixelaspect(struct file *file, void *priv, int type, struct v4l2_fract *f)
{
	struct mxo2_vid *vid = video_drvdata(file);
	bool wide = vsig(vid)->wide;

	if (type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
		return -EINVAL;
	update_format(vid);
	f->numerator = 1;
	f->denominator = 1;
	if (scaled_width(vid)) {
		/* pixel = span / width samples; a 240p sample is 10/22 (NTSC) or 59/108 (PAL) as wide as tall */
		bool pal = vid->height == 288;
		u32 span100 = retro_span100(vid->width, pal ? 864 : 858), g;

		f->numerator = vid->width * (pal ? 108 : 22) * 10;
		f->denominator = span100 * (pal ? 59 : 10) / 10;
		g = gcd(f->numerator, f->denominator);
		f->numerator /= g;
		f->denominator /= g;
	} else if (vid->height == 288) {               /* 288p: each pixel twice as tall as in 576i */
		f->numerator = wide ? 81 : 108;
		f->denominator = 59;
	} else if (vid->height == 240) {
		f->numerator = wide ? 33 : 11;
		f->denominator = wide ? 20 : 5;
	} else if (vid->height == 576) {
		f->numerator = wide ? 81 : 54;
		f->denominator = wide ? 118 : 59;
	} else if (vid->height == 480) {
		f->numerator = wide ? 33 : 11;
		f->denominator = wide ? 40 : 10;
	}
	return 0;
}

/*
 * Read-only crop bounds/default (the full frame). Only there because the V4L2 core enables
 * VIDIOC_CROPCAP - the ioctl that carries the pixel aspect - only for drivers with g_selection.
 * The crop target itself is unsupported (no cropping).
 */
static int vidioc_g_selection(struct file *file, void *priv, struct v4l2_selection *sel)
{
	struct mxo2_vid *vid = video_drvdata(file);

	if (sel->type != V4L2_BUF_TYPE_VIDEO_CAPTURE)
		return -EINVAL;
	switch (sel->target) {
	case V4L2_SEL_TGT_CROP_BOUNDS:
	case V4L2_SEL_TGT_CROP_DEFAULT:
		update_format(vid);
		sel->r.left = 0;
		sel->r.top = 0;
		sel->r.width = vid->width;
		sel->r.height = vid->height;
		return 0;
	}
	return -EINVAL;
}

/* V4L2 input n = MXO2_INPUT_n. The signal status is known for the selected input only. */
static int vidioc_enum_input(struct file *file, void *priv, struct v4l2_input *inp)
{
	struct mxo2_vid *vid = video_drvdata(file);
	struct mxo2 *m = vid->m;

	if (inp->index >= MXO2_NUM_INPUTS)
		return -EINVAL;
	strscpy(inp->name, mxo2_input_names[inp->index], sizeof(inp->name));
	inp->type = V4L2_INPUT_TYPE_CAMERA;
	inp->status = inp->index == m->input && !m->sig[inp->index].width ? V4L2_IN_ST_NO_SIGNAL : 0;
	return 0;
}

static int vidioc_g_input(struct file *file, void *priv, unsigned int *i)
{
	struct mxo2_vid *vid = video_drvdata(file);

	*i = vid->m->input;
	return 0;
}

/* Switches the card at once; the choice holds (no Auto switching) until the device's last handle is closed. */
static int vidioc_s_input(struct file *file, void *priv, unsigned int i)
{
	struct mxo2_vid *vid = video_drvdata(file);
	struct mxo2 *m = vid->m;
	int ret = 0;

	if (i >= MXO2_NUM_INPUTS)
		return -EINVAL;
	if (i != m->input) {
		if (vb2_is_busy(&vid->queue))
			return -EBUSY;  /* buffers are sized for the current input */
		ret = mxo2_switch_input(m, i);
	}
	if (!ret) {
		WRITE_ONCE(m->input_locked, true);
		update_format(vid);
	}
	return ret;
}

static int vidioc_log_status(struct file *file, void *priv)
{
	struct mxo2_vid *vid = video_drvdata(file);
	struct mxo2 *m = vid->m;
	struct mxo2_sig *s = vsig(vid);

	v4l2_info(&m->v4l2_dev, "input %s%s%s: signal %ux%u%s @ %u fps, format %ux%u, %s\n",
		  mxo2_input_names[m->input], m->input_pref < 0 ? " (auto)" : "",
		  m->input_locked ? " (set by an app)" : "", s->width, s->height, s->interlaced ? "i" : "p",
		  s->fps, vid->width, vid->height, m->streaming ? "capturing" : "idle");
	v4l2_info(&m->v4l2_dev, "frames on board %llu, copied %llu, no buffer %llu, DMA %u us\n",
		  m->stats.ts_changes, m->stats.dma_done, m->stats.no_buffer, m->stats.last_dma_us);
	return 0;
}

static int mxo2_s_ctrl(struct v4l2_ctrl *ctrl)
{
	struct mxo2_vid *vid = container_of(ctrl->handler, struct mxo2_vid, ctrl);

	struct mxo2 *m = vid->m;

	switch (ctrl->id) {
	case V4L2_CID_SHARPNESS:
		mxo2_set_sharpness(m, ctrl->val);
		return 0;
	case MXO2_CID_INPUT:
		/* a new choice here replaces an app's S_INPUT; applied now if idle, else after the stream stops */
		WRITE_ONCE(m->input_pref, ctrl->val - 1);
		WRITE_ONCE(m->input_locked, false);
		if (!vb2_is_busy(&vid->queue) && !mxo2_auto_input_now(m))
			update_format(vid);
		return 0;
	}
	return -EINVAL;
}

static const struct v4l2_ctrl_ops mxo2_ctrl_ops = {
	.s_ctrl = mxo2_s_ctrl,
};

static const char *const input_menu[] = { "Auto", "HDMI", "Composite", "S-Video", "Component", NULL };

static const struct v4l2_ctrl_config input_ctrl_cfg = {
	.ops = &mxo2_ctrl_ops,
	.id = MXO2_CID_INPUT,
	.name = "Input",
	.type = V4L2_CTRL_TYPE_MENU,
	.max = MXO2_NUM_INPUTS,
	.qmenu = input_menu,
};

static const struct v4l2_ioctl_ops mxo2_ioctl_ops = {
	.vidioc_querycap = vidioc_querycap,
	.vidioc_enum_fmt_vid_cap = vidioc_enum_fmt,
	.vidioc_g_fmt_vid_cap = vidioc_g_fmt,
	.vidioc_s_fmt_vid_cap = vidioc_s_fmt,
	.vidioc_try_fmt_vid_cap = vidioc_try_fmt,
	.vidioc_enum_framesizes = vidioc_enum_framesizes,
	.vidioc_enum_frameintervals = vidioc_enum_frameintervals,
	.vidioc_g_parm = vidioc_g_parm,
	.vidioc_s_parm = vidioc_g_parm,
	.vidioc_g_pixelaspect = vidioc_g_pixelaspect,
	.vidioc_g_selection = vidioc_g_selection,
	.vidioc_enum_input = vidioc_enum_input,
	.vidioc_g_input = vidioc_g_input,
	.vidioc_s_input = vidioc_s_input,
	.vidioc_log_status = vidioc_log_status,
	.vidioc_subscribe_event = v4l2_ctrl_subscribe_event,
	.vidioc_unsubscribe_event = v4l2_event_unsubscribe,
	.vidioc_reqbufs = vb2_ioctl_reqbufs,
	.vidioc_create_bufs = vb2_ioctl_create_bufs,
	.vidioc_prepare_buf = vb2_ioctl_prepare_buf,
	.vidioc_querybuf = vb2_ioctl_querybuf,
	.vidioc_qbuf = vb2_ioctl_qbuf,
	.vidioc_dqbuf = vb2_ioctl_dqbuf,
	.vidioc_expbuf = vb2_ioctl_expbuf,
	.vidioc_streamon = vb2_ioctl_streamon,
	.vidioc_streamoff = vb2_ioctl_streamoff,
};

/* Closing the last handle ends an app's S_INPUT choice (Auto / the Input control apply again). */
static int mxo2_release(struct file *file)
{
	struct mxo2_vid *vid = video_drvdata(file);
	bool last;

	mutex_lock(&vid->lock);
	last = v4l2_fh_is_singular_file(file);
	mutex_unlock(&vid->lock);
	if (last)
		WRITE_ONCE(vid->m->input_locked, false);
	return vb2_fop_release(file);
}

/*
 * PipeWire, when the app brings its own buffers (GStreamer pipewiresrc, OBS), first asks for USERPTR (not offered:
 * the card only reaches the low 4 GB, and bounce-buffered user memory would undo our CPU-side fixes), then falls
 * back to MMAP buffers mapped PROT_READ + MAP_PRIVATE, which vb2 refuses (EINVAL, it wants MAP_SHARED). A
 * read-only private mapping can't differ from a shared one as long as it can never become writable, so map it as
 * shared without write permission (mprotect(PROT_WRITE) then fails instead of copying pages).
 */
static int mxo2_mmap(struct file *file, struct vm_area_struct *vma)
{
	if (!(vma->vm_flags & (VM_SHARED | VM_WRITE))) {
		vm_flags_clear(vma, VM_MAYWRITE);
		vm_flags_set(vma, VM_SHARED | VM_MAYSHARE);
	}
	return vb2_fop_mmap(file, vma);
}

static const struct v4l2_file_operations mxo2_fops = {
	.owner = THIS_MODULE,
	.open = v4l2_fh_open,
	.release = mxo2_release,
	.read = vb2_fop_read,
	.poll = vb2_fop_poll,
	.mmap = mxo2_mmap,
	.unlocked_ioctl = video_ioctl2,
};

static void vid_unregister(struct mxo2_vid *vid)
{
	if (video_is_registered(&vid->vdev))
		vb2_video_unregister_device(&vid->vdev);
	v4l2_ctrl_handler_free(&vid->ctrl);
}

static int vid_register(struct mxo2 *m)
{
	struct mxo2_vid *vid = &m->vid;
	struct vb2_queue *q = &vid->queue;
	struct video_device *vdev = &vid->vdev;
	struct v4l2_ctrl_config input_cfg = input_ctrl_cfg;
	int ret;

	vid->m = m;
	mutex_init(&vid->lock);
	spin_lock_init(&vid->buf_lock);
	INIT_LIST_HEAD(&vid->buf_list);
	vid->hdmi_width = 1920;
	vid->hdmi_height = 1080;
	update_format(vid);

	v4l2_ctrl_handler_init(&vid->ctrl, 2);
	/* composite only: 0 = soft (decoder notch), 1 = sharp without subcarrier dots, 2 = sharpest (dots on colours) */
	v4l2_ctrl_new_std(&vid->ctrl, &mxo2_ctrl_ops, V4L2_CID_SHARPNESS, 0, 2, 1, m->sharpness);
	input_cfg.def = m->input_pref + 1;      /* the input= parameter */
	vid->input_ctrl = v4l2_ctrl_new_custom(&vid->ctrl, &input_cfg, NULL);
	if (vid->ctrl.error) {
		ret = vid->ctrl.error;
		goto err;
	}

	q->type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
	q->io_modes = VB2_MMAP | VB2_DMABUF | VB2_READ;
	q->drv_priv = vid;
	q->buf_struct_size = sizeof(struct mxo2_vbuf);
	q->ops = &mxo2_qops;
	q->mem_ops = &vb2_dma_sg_memops;
	q->timestamp_flags = V4L2_BUF_FLAG_TIMESTAMP_MONOTONIC;
	MXO2_VB2_MIN_BUFFERS(q, 2);
	q->gfp_flags = GFP_DMA32;
	q->lock = &vid->lock;
	q->dev = &m->pdev->dev;
	ret = vb2_queue_init(q);
	if (ret)
		goto err;

	strscpy(vdev->name, "Matrox MXO2 Mini", sizeof(vdev->name));
	vdev->release = video_device_release_empty;
	vdev->fops = &mxo2_fops;
	vdev->ioctl_ops = &mxo2_ioctl_ops;
	vdev->v4l2_dev = &m->v4l2_dev;
	vdev->ctrl_handler = &vid->ctrl;
	vdev->queue = q;
	vdev->lock = &vid->lock;
	vdev->device_caps = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING | V4L2_CAP_READWRITE;
	video_set_drvdata(vdev, vid);
	ret = video_register_device(vdev, VFL_TYPE_VIDEO, -1);
	if (ret)
		goto err;
	dev_info(&m->pdev->dev, "registered %s (inputs HDMI, Composite, S-Video, Component)\n",
		 video_device_node_name(vdev));
	return 0;
err:
	v4l2_ctrl_handler_free(&vid->ctrl);
	return ret;
}

int mxo2_v4l2_register(struct mxo2 *m)
{
	int ret;

	chroma_lut_init();
	m->fix_wq = alloc_ordered_workqueue("mxo2-fix", 0);
	if (!m->fix_wq)
		return -ENOMEM;
	ret = v4l2_device_register(&m->pdev->dev, &m->v4l2_dev);
	if (ret)
		goto err_wq;
	ret = vid_register(m);
	if (ret)
		goto err;
	ret = mxo2_play_register(m);
	if (ret)
		dev_warn(&m->pdev->dev, "video output device not registered (%d)\n", ret);
	return 0;
err:
	v4l2_device_unregister(&m->v4l2_dev);
err_wq:
	destroy_workqueue(m->fix_wq);
	return ret;
}

void mxo2_v4l2_unregister(struct mxo2 *m)
{
	mxo2_play_unregister(m);
	vid_unregister(&m->vid);
	v4l2_device_unregister(&m->v4l2_dev);
	destroy_workqueue(m->fix_wq);
}
