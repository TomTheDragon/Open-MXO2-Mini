// SPDX-License-Identifier: GPL-2.0
/*
 * Matrox MXO2 - video output from Linux: V4L2 output device "Matrox MXO2 Mini Output". Frames written to it
 * (write(), MMAP or DMABUF streaming; ffmpeg -f v4l2, GStreamer v4l2sink) appear on HDMI OUT and the analog OUT.
 * Formats (YUYV): 720x480 NTSC 29.97 (woven 480i), 720x576 PAL 25 (woven 576i); 1920x1080 30p only with
 * play_hd=1 (HDMI OUT gets a signal the TV calls "not supported": probably no HD pixel clock without an HDMI
 * input, re/notes/play-test.txt).
 * A thread copies each queued buffer into one of three output slots in card memory (PCI DMA host -> card,
 * Windows FUN_0003f800 direction 1) and, at the next frame tick, points the output read DMA at it - the path the
 * pass-through uses with captured frames. While playing, pass-through and capture are off (shared DMA engine
 * and output timing).
 * Audio: the same thread ("playback engine") also plays the ALSA playback stream: per frame the next 1601.6
 * (NTSC) / 1920 (PAL) / 1600 (1080p) samples at 48 kHz go into an audio slot in card memory and the audio read
 * DMA is started on it at the frame tick, in the stereo layout the analog-audio pass-through uses (2 x 32-bit
 * words per sample, sample in the upper half; AudioRDmaControl = go | 0x48000000 | dwords). Without an ALSA
 * stream it plays silence. The engine runs while the output device streams and/or the ALSA playback stream is
 * prepared; audio alone uses NTSC timing (the picture then shows the last frame in card memory).
 */

#include <linux/delay.h>
#include <linux/hrtimer.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <media/v4l2-ioctl.h>
#include <media/videobuf2-dma-sg.h>

#include "mxo2.h"
#include "gaia_regs.h"

static bool play_hd;
module_param(play_hd, bool, 0644);
MODULE_PARM_DESC(play_hd, "Offer 1920x1080 30p on the output device (experiment: the TV reports \"not supported\" "
		 "so far - no HD pixel clock without an HDMI input)");

static uint play_genlock;
module_param(play_genlock, uint, 0644);
MODULE_PARM_DESC(play_genlock, "Experiment: GenlockControl value for playback instead of the format's (0 = auto: "
		 "NTSC 0x1000, PAL 0x1101, 1080p30 0x401 as Windows with an input)");

#define PLAY_SLOTS      3
#define PLAY_BASE       0x01000000      /* after the capture slots (3 x 0x3f4800), below the audio ring */
#define RDMA_ON         0xa0000000      /* enable | 0x20000000 as the pass-through; | 0x02000000 progressive */
#define RDMA_FRAME      0x02000000
#define APLAY_BASE      0x01c00000      /* audio playback slots, after the video slots, below the capture ring */
#define APLAY_SLOT      0x4000          /* 1920 samples x 8 B max */
#define APLAY_SLOTS     4
#define ARDMA_GO        0xc8000000      /* go | 0x48000000, stereo (channel code 0) */
#define AUDIO_RATE      48000

static const struct play_fmt {
	u32 width, height;
	u32 fps_num, fps_den;           /* frame interval = num / den s */
	u32 skip_lines;                 /* NTSC: the card's frame has 486 lines, ours start 4 lines down */
	bool interlaced;
} play_fmts[MXO2_OUT_FORMATS] = {
	[MXO2_OUT_NTSC] = { 720, 480, 1001, 30000, 4, true },
	[MXO2_OUT_PAL] = { 720, 576, 1, 25, 0, true },
	[MXO2_OUT_1080P30] = { 1920, 1080, 1, 30, 0, false },
};

struct mxo2_play {
	struct mxo2 *m;
	struct video_device vdev;
	struct vb2_queue queue;
	struct mutex lock;              /* ioctls + queue */
	spinlock_t buf_lock;
	struct list_head buf_list;
	struct task_struct *thread;
	int fmt;                        /* MXO2_OUT_* of the output device */
	u32 sequence;
	u64 shown;                      /* frames put on the outputs */
	/* engine */
	struct mutex eng_lock;          /* users, start / stop */
	unsigned int users;             /* MXO2_PLAY_VIDEO / _AUDIO */
	int eng_fmt;                    /* format the engine runs in */
	struct mutex vid_lock;          /* engine thread working on a video buffer (vs. stop_streaming) */
	void *abuf;                     /* audio bounce: one frame of samples */
	dma_addr_t abuf_dma;
	u8 *asg;                        /* its one-entry card SG list */
	dma_addr_t asg_dma;
	u32 aacc;                       /* fractional samples (x fps_den), e.g. NTSC 1601.6 per frame */
};

static const struct play_fmt *pfmt(struct mxo2_play *p)
{
	return &play_fmts[p->fmt];
}

static u32 frame_bytes(struct mxo2_play *p)
{
	return pfmt(p)->width * pfmt(p)->height * 2;
}

static struct mxo2_vbuf *to_mbuf(struct vb2_buffer *vb)
{
	return container_of(to_vb2_v4l2_buffer(vb), struct mxo2_vbuf, vb);
}

/* ---- playback thread ---- */

/* Copy the next frame of ALSA samples (or silence) into audio slot `aslot`; returns the sample count or < 0. */
static int audio_frame(struct mxo2_play *p, const struct play_fmt *f, unsigned int aslot)
{
	struct mxo2 *m = p->m;
	u32 samples = div_u64_rem(p->aacc + (u64)AUDIO_RATE * f->fps_num, f->fps_den, &p->aacc);
	u32 bytes = samples * 8;

	mxo2_audio_play_fetch(m, p->abuf, samples);
	put_unaligned_le64(p->abuf_dma, p->asg);
	put_unaligned_le32(bytes, p->asg + 8);
	if (mxo2_dma(m, APLAY_BASE + aslot * APLAY_SLOT, p->asg_dma, 1, 2, true))
		return -EIO;
	return samples;
}

static int play_thread(void *data)
{
	struct mxo2_play *p = data;
	struct mxo2 *m = p->m;
	const struct play_fmt *f = &play_fmts[p->eng_fmt];
	u64 frame_ns = div_u64((u64)f->fps_num * NSEC_PER_SEC, f->fps_den);
	u32 pitch = f->width * 2;
	u32 ctl = RDMA_ON | (f->interlaced ? (pitch / 4) & 0x7ff : RDMA_FRAME);
	ktime_t next = ktime_get();
	unsigned int slot = 0, aslot = 0;

	p->aacc = 0;
	while (!kthread_should_stop()) {
		struct mxo2_vbuf *buf = NULL;
		unsigned long flags;
		u32 onboard = PLAY_BASE + slot * MXO2_FRAME_BYTES;
		int ret = -ENODATA, samples;

		mutex_lock(&p->vid_lock);
		if (READ_ONCE(p->users) & MXO2_PLAY_VIDEO) {
			spin_lock_irqsave(&p->buf_lock, flags);
			buf = list_first_entry_or_null(&p->buf_list, struct mxo2_vbuf, list);
			if (buf)
				list_del(&buf->list);
			spin_unlock_irqrestore(&p->buf_lock, flags);
		}
		if (buf) {
			ret = mxo2_dma(m, onboard + f->skip_lines * pitch, buf->card_sg_dma, buf->card_sg_entries,
				       pitch / 4, true);
			buf->vb.vb2_buf.timestamp = ktime_get_ns();
			buf->vb.sequence = p->sequence++;
			vb2_buffer_done(&buf->vb.vb2_buf, ret ? VB2_BUF_STATE_ERROR : VB2_BUF_STATE_DONE);
		}
		mutex_unlock(&p->vid_lock);
		samples = audio_frame(p, f, aslot);
		/* wait for the frame tick, then show the new frame (the last one stays up without a new buffer) */
		next = ktime_add_ns(next, frame_ns);
		if (ktime_before(ktime_get(), next)) {
			set_current_state(TASK_INTERRUPTIBLE);
			schedule_hrtimeout(&next, HRTIMER_MODE_ABS);
		} else {
			next = ktime_get();     /* fell behind (slow DMA / no buffers): restart the clock */
		}
		if (!ret) {
			mxo2_output_show(m, onboard, ctl);
			p->shown++;
			slot = (slot + 1) % PLAY_SLOTS;
		}
		if (samples > 0) {
			mxo2_wr(m, GAIA_AUDIO_RDMA_ADDRESS, (APLAY_BASE + aslot * APLAY_SLOT) & 0x3fffffff);
			mxo2_wr(m, GAIA_AUDIO_RDMA_CONTROL, ARDMA_GO | ((samples * 2) & 0xfffe));
			aslot = (aslot + 1) % APLAY_SLOTS;
		}
	}
	mxo2_wr(m, GAIA_AUDIO_RDMA_CONTROL, 0x08000000);       /* Windows idle value */
	return 0;
}

static void return_all(struct mxo2_play *p, enum vb2_buffer_state state)
{
	struct mxo2_vbuf *buf, *tmp;
	unsigned long flags;

	spin_lock_irqsave(&p->buf_lock, flags);
	list_for_each_entry_safe(buf, tmp, &p->buf_list, list) {
		list_del(&buf->list);
		vb2_buffer_done(&buf->vb.vb2_buf, state);
	}
	spin_unlock_irqrestore(&p->buf_lock, flags);
}

/* ---- vb2 queue ---- */

static int queue_setup(struct vb2_queue *q, unsigned int *nbuffers, unsigned int *nplanes,
		       unsigned int sizes[], struct device *alloc_devs[])
{
	struct mxo2_play *p = vb2_get_drv_priv(q);

	if (*nplanes)
		return sizes[0] < frame_bytes(p) ? -EINVAL : 0;
	*nplanes = 1;
	sizes[0] = frame_bytes(p);
	if (*nbuffers < 3)
		*nbuffers = 3;
	return 0;
}

static int buf_init(struct vb2_buffer *vb)
{
	struct mxo2_play *p = vb2_get_drv_priv(vb->vb2_queue);
	struct mxo2_vbuf *buf = to_mbuf(vb);
	size_t max_entries = DIV_ROUND_UP(vb2_plane_size(vb, 0), PAGE_SIZE) + 1;

	buf->card_sg_size = PAGE_ALIGN(max_entries * 12);
	buf->card_sg = dma_alloc_coherent(&p->m->pdev->dev, buf->card_sg_size, &buf->card_sg_dma, GFP_KERNEL);
	return buf->card_sg ? 0 : -ENOMEM;
}

static int buf_prepare(struct vb2_buffer *vb)
{
	struct mxo2_play *p = vb2_get_drv_priv(vb->vb2_queue);
	struct mxo2_vbuf *buf = to_mbuf(vb);
	struct sg_table *sgt = vb2_dma_sg_plane_desc(vb, 0);

	if (vb2_plane_size(vb, 0) < frame_bytes(p))
		return -EINVAL;
	vb2_set_plane_payload(vb, 0, frame_bytes(p));
	buf->card_sg_entries = mxo2_build_card_sg(buf->card_sg, sgt->sgl, sgt->nents, frame_bytes(p));
	if (buf->card_sg_entries * 3 > 0x1fff)  /* PciDmaSGCount is 13 bits (dwords) */
		return -EINVAL;
	return 0;
}

static void buf_cleanup(struct vb2_buffer *vb)
{
	struct mxo2_play *p = vb2_get_drv_priv(vb->vb2_queue);
	struct mxo2_vbuf *buf = to_mbuf(vb);

	if (buf->card_sg)
		dma_free_coherent(&p->m->pdev->dev, buf->card_sg_size, buf->card_sg, buf->card_sg_dma);
	buf->card_sg = NULL;
}

static void buf_queue(struct vb2_buffer *vb)
{
	struct mxo2_play *p = vb2_get_drv_priv(vb->vb2_queue);
	struct mxo2_vbuf *buf = to_mbuf(vb);
	unsigned long flags;

	spin_lock_irqsave(&p->buf_lock, flags);
	list_add_tail(&buf->list, &p->buf_list);
	spin_unlock_irqrestore(&p->buf_lock, flags);
}

/* ---- playback engine (eng_lock held) ---- */

static int engine_start(struct mxo2_play *p, int fmt)
{
	struct mxo2 *m = p->m;
	int ret;

	ret = mxo2_play_claim(m, fmt);
	if (ret) {
		dev_info(&m->pdev->dev, "output: busy (a capture runs), stop it first\n");
		return ret;
	}
	if (READ_ONCE(play_genlock))
		mxo2_wr(m, GAIA_GENLOCK_CONTROL, READ_ONCE(play_genlock));
	ret = mxo2_output_setup(m, fmt);
	if (ret)
		goto err;
	p->eng_fmt = fmt;
	p->thread = kthread_run(play_thread, p, "mxo2-play");
	if (IS_ERR(p->thread)) {
		ret = PTR_ERR(p->thread);
		p->thread = NULL;
		goto err;
	}
	dev_info(&m->pdev->dev, "output: playing %s from Linux\n", mxo2_output_name(fmt));
	return 0;
err:
	mxo2_play_release(m);
	return ret;
}

static void engine_stop(struct mxo2_play *p)
{
	if (!p->thread)
		return;
	kthread_stop(p->thread);
	p->thread = NULL;
	mxo2_play_release(p->m);
	dev_info(&p->m->pdev->dev, "output: stopped (%llu video frames shown)\n", p->shown);
}

/*
 * A user (MXO2_PLAY_VIDEO / _AUDIO) starts. fmt: the video format (MXO2_OUT_*), or -1 for audio = keep the running
 * format, else NTSC. Video in another format than a running audio-only engine restarts it.
 */
int mxo2_play_engine_get(struct mxo2 *m, unsigned int user, int fmt)
{
	struct mxo2_play *p = m->play;
	int ret = 0;

	if (!p)
		return -ENODEV;
	mutex_lock(&p->eng_lock);
	if (fmt < 0)
		fmt = p->thread ? p->eng_fmt : MXO2_OUT_NTSC;
	if (p->thread && fmt != p->eng_fmt)
		engine_stop(p);
	if (!p->thread)
		ret = engine_start(p, fmt);
	if (!ret)
		p->users |= user;
	mutex_unlock(&p->eng_lock);
	return ret;
}

void mxo2_play_engine_put(struct mxo2 *m, unsigned int user)
{
	struct mxo2_play *p = m->play;

	if (!p)
		return;
	mutex_lock(&p->eng_lock);
	p->users &= ~user;
	if (!p->users)
		engine_stop(p);
	mutex_unlock(&p->eng_lock);
}

static int start_streaming(struct vb2_queue *q, unsigned int count)
{
	struct mxo2_play *p = vb2_get_drv_priv(q);
	int ret;

	p->sequence = 0;
	p->shown = 0;
	ret = mxo2_play_engine_get(p->m, MXO2_PLAY_VIDEO, p->fmt);
	if (ret)
		return_all(p, VB2_BUF_STATE_QUEUED);
	return ret;
}

static void stop_streaming(struct vb2_queue *q)
{
	struct mxo2_play *p = vb2_get_drv_priv(q);

	mutex_lock(&p->eng_lock);
	p->users &= ~MXO2_PLAY_VIDEO;   /* the engine takes no more buffers ... */
	mutex_unlock(&p->eng_lock);
	mutex_lock(&p->vid_lock);       /* ... and finishes the one it works on */
	mutex_unlock(&p->vid_lock);
	return_all(p, VB2_BUF_STATE_ERROR);
	mxo2_play_engine_put(p->m, MXO2_PLAY_VIDEO);
}

static const struct vb2_ops play_qops = {
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

static int play_querycap(struct file *file, void *priv, struct v4l2_capability *cap)
{
	struct mxo2_play *p = video_drvdata(file);

	strscpy(cap->driver, "mxo2", sizeof(cap->driver));
	strscpy(cap->card, "Matrox MXO2 Mini Output", sizeof(cap->card));
	snprintf(cap->bus_info, sizeof(cap->bus_info), "PCI:%s", pci_name(p->m->pdev));
	return 0;
}

static int play_enum_fmt(struct file *file, void *priv, struct v4l2_fmtdesc *f)
{
	if (f->index)
		return -EINVAL;
	f->pixelformat = V4L2_PIX_FMT_YUYV;
	return 0;
}

static int play_formats(void)
{
	return READ_ONCE(play_hd) ? MXO2_OUT_FORMATS : MXO2_OUT_1080P30;
}

/* Output format for a requested size: exact height, else the nearest of 480 / 576 (/ 1080) */
static int pick_fmt(u32 width, u32 height)
{
	if ((height >= 828 || width > 720) && READ_ONCE(play_hd))
		return MXO2_OUT_1080P30;
	if (height > 528)
		return MXO2_OUT_PAL;
	return MXO2_OUT_NTSC;
}

static void fill_fmt(int fmt, struct v4l2_pix_format *pix)
{
	const struct play_fmt *f = &play_fmts[fmt];

	pix->width = f->width;
	pix->height = f->height;
	pix->pixelformat = V4L2_PIX_FMT_YUYV;
	pix->field = f->interlaced ? V4L2_FIELD_INTERLACED : V4L2_FIELD_NONE;
	pix->bytesperline = f->width * 2;
	pix->sizeimage = f->width * f->height * 2;
	pix->colorspace = f->height >= 720 ? V4L2_COLORSPACE_REC709 : V4L2_COLORSPACE_SMPTE170M;
	pix->ycbcr_enc = V4L2_YCBCR_ENC_DEFAULT;
	pix->quantization = V4L2_QUANTIZATION_LIM_RANGE;
	pix->xfer_func = V4L2_XFER_FUNC_DEFAULT;
}

static int play_g_fmt(struct file *file, void *priv, struct v4l2_format *f)
{
	struct mxo2_play *p = video_drvdata(file);

	fill_fmt(p->fmt, &f->fmt.pix);
	return 0;
}

static int play_try_fmt(struct file *file, void *priv, struct v4l2_format *f)
{
	fill_fmt(pick_fmt(f->fmt.pix.width, f->fmt.pix.height), &f->fmt.pix);
	return 0;
}

static int play_s_fmt(struct file *file, void *priv, struct v4l2_format *f)
{
	struct mxo2_play *p = video_drvdata(file);
	int fmt = pick_fmt(f->fmt.pix.width, f->fmt.pix.height);

	if (vb2_is_busy(&p->queue) && fmt != p->fmt)
		return -EBUSY;
	p->fmt = fmt;
	fill_fmt(fmt, &f->fmt.pix);
	return 0;
}

static int play_enum_framesizes(struct file *file, void *priv, struct v4l2_frmsizeenum *fs)
{
	if (fs->pixel_format != V4L2_PIX_FMT_YUYV || fs->index >= play_formats())
		return -EINVAL;
	fs->type = V4L2_FRMSIZE_TYPE_DISCRETE;
	fs->discrete.width = play_fmts[fs->index].width;
	fs->discrete.height = play_fmts[fs->index].height;
	return 0;
}

static int play_enum_frameintervals(struct file *file, void *priv, struct v4l2_frmivalenum *fi)
{
	int fmt = pick_fmt(fi->width, fi->height);

	if (fi->index || fi->pixel_format != V4L2_PIX_FMT_YUYV || fi->width != play_fmts[fmt].width ||
	    fi->height != play_fmts[fmt].height)
		return -EINVAL;
	fi->type = V4L2_FRMIVAL_TYPE_DISCRETE;
	fi->discrete.numerator = play_fmts[fmt].fps_num;
	fi->discrete.denominator = play_fmts[fmt].fps_den;
	return 0;
}

static int play_g_parm(struct file *file, void *priv, struct v4l2_streamparm *sp)
{
	struct mxo2_play *p = video_drvdata(file);

	if (sp->type != V4L2_BUF_TYPE_VIDEO_OUTPUT)
		return -EINVAL;
	sp->parm.output.capability = V4L2_CAP_TIMEPERFRAME;
	sp->parm.output.writebuffers = 3;
	sp->parm.output.timeperframe.numerator = pfmt(p)->fps_num;
	sp->parm.output.timeperframe.denominator = pfmt(p)->fps_den;
	return 0;
}

static int play_enum_output(struct file *file, void *priv, struct v4l2_output *out)
{
	if (out->index)
		return -EINVAL;
	strscpy(out->name, "HDMI + analog OUT", sizeof(out->name));
	out->type = V4L2_OUTPUT_TYPE_ANALOG;
	return 0;
}

static int play_g_output(struct file *file, void *priv, unsigned int *i)
{
	*i = 0;
	return 0;
}

static int play_s_output(struct file *file, void *priv, unsigned int i)
{
	return i ? -EINVAL : 0;
}

static const struct v4l2_ioctl_ops play_ioctl_ops = {
	.vidioc_querycap = play_querycap,
	.vidioc_enum_fmt_vid_out = play_enum_fmt,
	.vidioc_g_fmt_vid_out = play_g_fmt,
	.vidioc_s_fmt_vid_out = play_s_fmt,
	.vidioc_try_fmt_vid_out = play_try_fmt,
	.vidioc_enum_framesizes = play_enum_framesizes,
	.vidioc_enum_frameintervals = play_enum_frameintervals,
	.vidioc_g_parm = play_g_parm,
	.vidioc_s_parm = play_g_parm,
	.vidioc_enum_output = play_enum_output,
	.vidioc_g_output = play_g_output,
	.vidioc_s_output = play_s_output,
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

static const struct v4l2_file_operations play_fops = {
	.owner = THIS_MODULE,
	.open = v4l2_fh_open,
	.release = vb2_fop_release,
	.write = vb2_fop_write,
	.poll = vb2_fop_poll,
	.mmap = vb2_fop_mmap,
	.unlocked_ioctl = video_ioctl2,
};

int mxo2_play_register(struct mxo2 *m)
{
	struct mxo2_play *p;
	struct vb2_queue *q;
	struct video_device *vdev;
	int ret;

	p = devm_kzalloc(&m->pdev->dev, sizeof(*p), GFP_KERNEL);   /* lives as long as struct mxo2 */
	if (!p)
		return -ENOMEM;
	p->m = m;
	p->fmt = MXO2_OUT_NTSC;
	mutex_init(&p->lock);
	mutex_init(&p->eng_lock);
	mutex_init(&p->vid_lock);
	p->abuf = dmam_alloc_coherent(&m->pdev->dev, APLAY_SLOT, &p->abuf_dma, GFP_KERNEL);
	p->asg = dmam_alloc_coherent(&m->pdev->dev, 64, &p->asg_dma, GFP_KERNEL);
	if (!p->abuf || !p->asg)
		return -ENOMEM;
	spin_lock_init(&p->buf_lock);
	INIT_LIST_HEAD(&p->buf_list);

	q = &p->queue;
	q->type = V4L2_BUF_TYPE_VIDEO_OUTPUT;
	q->io_modes = VB2_MMAP | VB2_DMABUF | VB2_WRITE;
	q->drv_priv = p;
	q->buf_struct_size = sizeof(struct mxo2_vbuf);
	q->ops = &play_qops;
	q->mem_ops = &vb2_dma_sg_memops;
	q->timestamp_flags = V4L2_BUF_FLAG_TIMESTAMP_COPY;
	MXO2_VB2_MIN_BUFFERS(q, 1);
	q->gfp_flags = GFP_DMA32;
	q->lock = &p->lock;
	q->dev = &m->pdev->dev;
	ret = vb2_queue_init(q);
	if (ret)
		return ret;

	vdev = &p->vdev;
	strscpy(vdev->name, "Matrox MXO2 Mini Output", sizeof(vdev->name));
	vdev->release = video_device_release_empty;
	vdev->fops = &play_fops;
	vdev->ioctl_ops = &play_ioctl_ops;
	vdev->v4l2_dev = &m->v4l2_dev;
	vdev->queue = q;
	vdev->lock = &p->lock;
	vdev->vfl_dir = VFL_DIR_TX;
	vdev->device_caps = V4L2_CAP_VIDEO_OUTPUT | V4L2_CAP_STREAMING | V4L2_CAP_READWRITE;
	video_set_drvdata(vdev, p);
	ret = video_register_device(vdev, VFL_TYPE_VIDEO, -1);
	if (ret)
		return ret;
	m->play = p;
	dev_info(&m->pdev->dev, "registered %s (video output)\n", video_device_node_name(vdev));
	return 0;
}

void mxo2_play_unregister(struct mxo2 *m)
{
	struct mxo2_play *p = m->play;

	if (!p)
		return;
	vb2_video_unregister_device(&p->vdev);  /* stops a running playback */
	m->play = NULL;
}
