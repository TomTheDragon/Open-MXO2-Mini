/* SPDX-License-Identifier: GPL-2.0 */
/* Matrox MXO2 ("Gaia" FPGA) experimental driver - shared definitions. */
#ifndef MXO2_H
#define MXO2_H

#include <linux/mutex.h>
#include "mxo2_compat.h"
#include <linux/pci.h>
#include <linux/types.h>
#include <linux/wait.h>
#include <linux/workqueue.h>
#include <sound/core.h>
#include <sound/pcm.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>
#include <media/videobuf2-v4l2.h>

#define MXO2_BAR0_SIZE 0x1000 /* BAR0 is 8 KiB but only decodes 4 KiB (mirrored) */

/* One 1080p YUYV frame: 1920 * 1080 * 2 bytes (also the on-board slot size). */
#define MXO2_FRAME_BYTES (1920 * 1080 * 2)
#define MXO2_FRAME_PAGES DIV_ROUND_UP(MXO2_FRAME_BYTES, PAGE_SIZE)
#define MXO2_ONBOARD_SLOTS 3
#define MXO2_HOST_BUFS 2

/* A host frame buffer: single pages, mapped for DMA, plus the card's SG list for them. */
struct mxo2_hbuf {
	struct page *pages[MXO2_FRAME_PAGES];
	dma_addr_t dma[MXO2_FRAME_PAGES];
	void *vaddr;            /* vmap() of all pages, for CPU access */
	void *sg;               /* SG list: 12-byte entries {u64 addr, u32 len} */
	dma_addr_t sg_dma;
	size_t sg_size;
	unsigned int sg_entries;
	unsigned int npages;    /* pages in use (<= MXO2_FRAME_PAGES) */
	u32 ts;                 /* VideoWDmaTimeStamp1 of the frame in it */
	u64 seq;                /* capture sequence number */
};

struct mxo2_stats {
	u64 ts_changes;         /* frames written to on-board memory (TS1 changed) */
	u64 dma_done;           /* frames copied to host */
	u64 ts_timeouts;        /* no new frame within the timeout */
	u64 dma_timeouts;
	u64 irqs;               /* hard interrupts handled */
	u64 no_buffer;          /* V4L2: frame dropped, no buffer queued */
	u32 last_dma_us;
	u32 max_dma_us;
	u64 started_ns;
};

struct snd_pcm_substream;
struct mxo2;

/* Inputs (V4L2 inputs 0-3 of the one video device). The analog ones share the ADV7181 decoder: table = input - 1 (525 lines), +3 (625). */
enum { MXO2_INPUT_HDMI, MXO2_INPUT_COMPOSITE, MXO2_INPUT_SVIDEO, MXO2_INPUT_COMPONENT, MXO2_NUM_INPUTS };
extern const char *const mxo2_input_names[MXO2_NUM_INPUTS];
#define MXO2_CID_INPUT (V4L2_CID_USER_BASE + 0x1f00) /* "Input" menu: 0 Auto, 1 + MXO2_INPUT_* */

/* Signal detected on one input (mxo2_rx.c); width 0 = none. */
struct mxo2_sig {
	u32 width, height, fps;
	bool interlaced;        /* two fields per frame */
	bool wide;              /* SD picture aspect 16:9 */
};

/* The V4L2 capture device (/dev/videoN); it captures from the card's selected input (m->input). */
struct mxo2_vid {
	struct mxo2 *m;
	struct video_device vdev;
	struct vb2_queue queue;
	struct mutex lock;      /* serialises ioctls and the vb2 queue */
	spinlock_t buf_lock;    /* buf_list */
	struct list_head buf_list; /* queued buffers waiting for a frame */
	u32 width, height;      /* format of the current / next stream */
	bool fmt_interlaced;    /* ... woven frames */
	bool pal;               /* analog: last signal was 50 Hz (sizes 576/288) */
	u32 req_width, req_height; /* analog: requested size (S_FMT), 0 = the signal's own size */
	u32 hdmi_width, hdmi_height; /* HDMI: last detected size, used while there is no signal (1080p at first) */
	bool hdmi_interlaced;
	int fmt_input;          /* input the current format was made for */
	u32 sequence;
	bool line_double;       /* composite 240p/288p delivered as 480/576 lines: each line twice */
	bool use_bounce;        /* frames go through `bounce` (scaled width or line doubling) */
	struct mxo2_scale_tap *taps; /* horizontal scaler while streaming a scaled format, else NULL */
	struct mxo2_hbuf bounce;     /* ... and its 720-wide source field */
	u8 *linebuf;            /* one filtered 720-sample line (bounce path) */
	struct v4l2_ctrl_handler ctrl;
	struct v4l2_ctrl *input_ctrl;   /* "Input" menu: Auto, then MXO2_INPUT_* + 1 */
};

/* Horizontal area-average tap: source samples k0..k0+n-1 with weights w (1/256 sample units). */
struct mxo2_scale_tap {
	u16 k0;
	u8 n;
	u16 w[4];
};

/* HDMI audio capture state (mxo2_audio.c) */
struct mxo2_audio {
	struct snd_card *card;
	struct snd_pcm_substream *substream;
	spinlock_t lock;        /* substream, running, hw_ptr, period_pos */
	struct mutex mutex;     /* capture thread delivery vs. close */
	bool running;           /* ALSA triggered */
	bool holding;           /* we hold a MXO2_USER_AUDIO reference */
	snd_pcm_uframes_t hw_ptr;
	snd_pcm_uframes_t period_pos;
	/* hardware ring (capture thread only) */
	bool active;
	u32 last_ts;
	u64 armed, started, copied;
	u32 samples[8];         /* samples in each on-board slot */
	unsigned int last_slot; /* slot of the last copied block */
	bool rate_mismatch;     /* source rate != stream rate: delivering silence */
	u64 acc;                /* fractional samples carried over (24 fraction bits) */
	u64 spf_q24;            /* samples per frame from N/CTS, 0 = using the VIC rate */
	u32 htotal;
	u32 frame_bytes;        /* bytes per sample frame in card memory: 32 (HDMI, 8 ch) or 8 (analog, stereo) */
	u32 ch_code;            /* AudioWDmaControl bits 22:21: 2 = 8 ch, 0 = stereo */
	void *bounce;
	dma_addr_t bounce_dma;
	u8 *bounce_sg;
	dma_addr_t bounce_sg_dma;
	/* stats */
	u64 blocks, samples_total, overruns, dma_errors;
	/* no source: silence keeps the ALSA stream (and PipeWire) running */
	u64 real_ns;            /* last real block delivered (or stream start) */
	u64 silence_ns;         /* silence generated up to this time, 0 = not generating */
	u64 silence_samples;
};

struct mxo2 {
	struct pci_dev *pdev;
	void __iomem *regs;
	void __iomem *mem;
	resource_size_t mem_len;
	struct dentry *dbg;
	struct mutex lock;      /* register access from debugfs */
	u32 shadow[MXO2_BAR0_SIZE / 4]; /* last value written, for write-only registers */

	/* capture (mxo2_capture.c) */
	struct mutex cap_lock;  /* start/stop */
	struct task_struct *cap_thread;
	unsigned int cap_users; /* MXO2_USER_* bits; the thread runs while non-zero */
	bool cap_interlaced;    /* the running thread weaves fields (fixed while it runs) */
	u32 cap_line_bytes;     /* line pitch for weaving = input width * 2 (fixed while it runs) */
	bool cap_analog;        /* capturing from the ADV7181 decoder (fixed while it runs) */
	bool cap_pal;           /* analog 625-line / 50 Hz timing (fixed while it runs) */
	u32 cap_skip;           /* bytes skipped at the start of an on-board frame (analog: extra lines) */
	u32 slot_skip[MXO2_ONBOARD_SLOTS]; /* extra bytes skipped per slot (PAL 288p lower fields) */
	struct mxo2_hbuf hbuf[MXO2_HOST_BUFS];
	struct mutex frame_lock; /* protects `latest` and the buffer contents vs. readers */
	int latest;             /* index of the newest complete host buffer, -1 = none */
	struct mxo2_stats stats;

	/* interrupts: the hard handler records events here and wakes the capture thread */
	int irq;                /* Linux IRQ number, or -1 when polling */
	bool irq_msi;
	u32 int_enable;         /* value written to InterruptEnable0 */
	u32 int_bits_seen;      /* OR of all bits acknowledged by the handler */
	u64 vsync_ns;           /* time of the last input vsync interrupt */
	u32 field_ns;           /* averaged vsync period (one field), 0 = not measured yet */
	unsigned long irq_events; /* MXO2_EV_* bits */
	wait_queue_head_t irq_wq;

	/* HDMI receiver (mxo2_rx.c) */
	struct mutex rx_lock;   /* SRI / I2C access */
	int rx_mode;            /* loaded AD9398 mode table, -1 = not configured by us */
	int rx_state;           /* MXO2_RX_* */
	int rx_colorspace;      /* AVI colour space: 0 RGB, 1 YCbCr 4:2:2, 2 YCbCr 4:4:4 */
	struct delayed_work rx_work; /* periodic mxo2_rx_monitor() */
	bool hw_ready;          /* Gaia defaults + receiver set up (at probe) */

	/* selected input and ADV7181 analog decoder (mxo2_rx.c) */
	int input;              /* MXO2_INPUT_* */
	int input_pref;         /* "Input" control / input= parameter: -1 Auto (the input with a signal, HDMI first),
				 * else MXO2_INPUT_*; applied while no video capture runs */
	bool input_locked;      /* ... except after VIDIOC_S_INPUT, until the device is closed */
	int auto_want, auto_count; /* candidate input and how many monitor rounds in a row it was seen */
	bool resume_capture;    /* the capture thread ran when the system suspended */
	int dec_mode;           /* loaded decoder table 0-5 (composite / S-video / component, +3 = 625 lines), -1 none */
	int dec_want;           /* table the status asks for; loaded once that held for 400 ms */
	unsigned long dec_want_since;
	u8 dec_status1, dec_status3;

	/* detected input (mxo2_rx.c, updated by the monitor) */
	u32 in_width, in_height; /* 0 = no supported signal */
	u32 in_fps;             /* frames per second, 0 = unknown */
	bool in_interlaced;     /* two fields per frame (1080i, 480i, 576i) */
	u32 in_pixel_rep;       /* HDMI pixel repetition (2 for 480i/576i), de-repeated by the AD9398 */
	int in_table;           /* AD9398 mode table for this format, -1 = rx_mode */
	bool in_wide;           /* picture aspect 16:9 (SD anamorphic) rather than 4:3 */
	u8 in_vic;
	u32 in_vtotal;          /* lines per frame (1125 for 1080i/p) */
	u32 in_n, in_cts;       /* HDMI audio clock regeneration */
	u32 in_audio_rate;      /* detected audio sample rate (Hz), 0 = none/unknown */
	u32 in_rate_cs, in_rate_cts; /* from channel status / from clock regeneration */
	bool in_rate_warned;

	/* V4L2 (mxo2_v4l2.c): one video device for all inputs */
	struct v4l2_device v4l2_dev;
	struct mxo2_vid vid;
	struct mxo2_vid *streaming;     /* &vid while its stream owns the capture, NULL = none */
	struct mxo2_sig sig[MXO2_NUM_INPUTS]; /* detected signal per input (inactive ones probed while idle) */
	int sharpness;          /* composite: 0 decoder notch, 1 wide band + subcarrier filter, 2 wide band */
	struct workqueue_struct *fix_wq; /* ordered: chroma fix + buffer completion */

	/* video outputs (mxo2_output.c): pass-through of the captured input */
	int out_format;         /* output format of the running capture, -1 = outputs not fed */
	bool out_hpd;           /* a TV is connected to HDMI OUT */

	struct mxo2_audio audio;
};

#define MXO2_USER_DEBUGFS BIT(0)  /* debugfs test capture (2 host buffers) */
#define MXO2_USER_V4L2    BIT(1)  /* V4L2 streaming */
#define MXO2_USER_AUDIO   BIT(2)  /* ALSA capture */
#define MXO2_USER_OUTPUT  BIT(3)  /* video outputs show the input (no host copy) */

/* A V4L2 buffer plus the card's SG list for it. */
struct mxo2_vbuf {
	struct vb2_v4l2_buffer vb;
	struct list_head list;
	struct work_struct fix_work;
	bool notch;             /* fix_work: composite subcarrier notch (else: HDMI chroma fix) */
	struct mxo2_vid *vid;
	void *card_sg;
	dma_addr_t card_sg_dma;
	size_t card_sg_size;
	unsigned int card_sg_entries;
};

enum { MXO2_RX_UNKNOWN, MXO2_RX_NO_HDMI, MXO2_RX_HDMI };

#define MXO2_EV_VSYNC 0 /* InterruptStatus0 bit 23: input frame boundary */
#define MXO2_EV_DMA   1 /* InterruptStatus0 bit 20: PCI DMA done */

u32 mxo2_rd(struct mxo2 *m, unsigned int off);
void mxo2_wr(struct mxo2 *m, unsigned int off, u32 val);

int mxo2_hw_init(struct mxo2 *m);
void mxo2_hw_fini(struct mxo2 *m);
int mxo2_capture_get(struct mxo2 *m, unsigned int user);
void mxo2_capture_put(struct mxo2 *m, unsigned int user);
void mxo2_capture_stop_all(struct mxo2 *m);
/* PCI DMA of `onboard` (byte address in card memory) to host; width = PitchRes field in dwords */
u32 mxo2_slot_offset(struct mxo2 *m, unsigned int slot);
int mxo2_dma_to_host(struct mxo2 *m, u32 onboard, dma_addr_t sg_dma, unsigned int sg_entries,
		     u32 width);
int mxo2_hbuf_alloc(struct mxo2 *m, struct mxo2_hbuf *b, u32 bytes);
void mxo2_hbuf_free(struct mxo2 *m, struct mxo2_hbuf *b);
void mxo2_hbuf_sync(struct mxo2 *m, struct mxo2_hbuf *b, bool for_cpu);
unsigned int mxo2_build_card_sg(u8 *sg, struct scatterlist *sgl, unsigned int nents, u32 bytes);
void mxo2_capture_debugfs(struct mxo2 *m);

int mxo2_rx_setup(struct mxo2 *m);
/* I2C to the box chips over SRI; callers hold rx_lock */
int mxo2_i2c_write(struct mxo2 *m, u8 dev, u8 reg, u8 val);
int mxo2_i2c_read(struct mxo2 *m, u8 dev, u8 reg, u8 *val);
void mxo2_rx_detect(struct mxo2 *m);
void mxo2_rx_monitor(struct mxo2 *m);
void mxo2_rx_debugfs(struct mxo2 *m);
int mxo2_select_input(struct mxo2 *m, int input);
int mxo2_input_init(struct mxo2 *m, bool probe);
void mxo2_input_power(struct mxo2 *m, bool capturing);
int mxo2_switch_input(struct mxo2 *m, int input);
int mxo2_auto_input_now(struct mxo2 *m);
void mxo2_set_sharpness(struct mxo2 *m, int val);
bool mxo2_cap_weave(struct mxo2 *m);
void mxo2_capture_suspend(struct mxo2 *m);
void mxo2_capture_resume(struct mxo2 *m);

int mxo2_v4l2_register(struct mxo2 *m);
void mxo2_v4l2_unregister(struct mxo2 *m);
/* called by the capture thread with a finished on-board slot; true if a buffer was filled */
void mxo2_v4l2_frame(struct mxo2 *m, unsigned int slot);
bool mxo2_video_busy(struct mxo2 *m);

bool mxo2_output_wanted(struct mxo2 *m);
void mxo2_output_start(struct mxo2 *m);
void mxo2_output_stop(struct mxo2 *m);
void mxo2_output_frame(struct mxo2 *m, unsigned int slot, u32 wdma_ctl);
void mxo2_output_monitor_locked(struct mxo2 *m);
void mxo2_output_poll(struct mxo2 *m);

int mxo2_audio_register(struct mxo2 *m);
void mxo2_audio_unregister(struct mxo2 *m);
void mxo2_audio_reset(struct mxo2 *m);
void mxo2_audio_arm(struct mxo2 *m);
void mxo2_audio_service(struct mxo2 *m, bool can_dma);
void mxo2_audio_debugfs(struct mxo2 *m);

#endif
