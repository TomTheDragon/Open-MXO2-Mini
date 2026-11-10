// SPDX-License-Identifier: GPL-2.0
/*
 * Matrox MXO2 - HDMI capture into host memory: hardware init, capture thread, PCI DMA.
 * Frames go either to V4L2 buffers (mxo2_v4l2.c) or to two debugfs test buffers.
 *
 * Data path (see re/NOTES.md):
 *   HDMI receiver (AD9398, set up by mxo2_rx.c at start) -> FPGA video write DMA
 *   ("WDMA") into an on-board memory slot -> PCI DMA of that slot into host pages.
 *
 * The WDMA captures exactly one frame per write of VideoWDmaControl, so the capture thread
 * re-arms it for every frame. VideoWDmaTimeStamp1 changes once per captured frame.
 * At TS1 change n the thread arms on-board slot n % 3 for the next frame and copies the slot
 * armed at change n - 2 (certainly complete, and not written while it is copied).
 *
 * Interrupts (re/notes/int-probe.txt): InterruptStatus0 latches events whether enabled or not;
 * bit 23 = input frame boundary (fires even without capture), bit 20 = PCI DMA done, bit 28 =
 * 240 Hz system clock tick, bit 24 is always set. InterruptEnable0 bit 31 is a master enable
 * (re/notes/irq-test.txt): without it no interrupt is sent at all. A bit is cleared by writing it to
 * InterruptClear0 and then 0. MSI is preferred (the INTx line is shared with USB here); with
 * use_irq=0 the thread polls instead.
 *
 * PCI DMA (MVKAVBUS FUN_0003f800): PciDmaFrame = on-board address, PciDmaSGAddress = host SG
 * list, PciDmaSGCount = list size in dwords, PciDmaPitchRes, PciDmaCommand 0x10000000 then
 * 0x90000000 (go). SG entry = {u64 bus address, u32 length < 0x20000}. PciDmaCommandCount
 * increments when a transfer is done.
 */

#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/interrupt.h>
#include <linux/dma-mapping.h>
#include <linux/kthread.h>
#include <linux/ktime.h>
#include <linux/seq_file.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>

#include "gaia_regs.h"
#include "mxo2.h"

#define WDMA_CTL_CAPTURE   0x82000000 /* Windows value, 1080p YUYV 8-bit */
#define WDMA_CTL2_VALUE    0x00000041 /* bit 6: source = HDMI */
#define WDMA_CTL2_ANALOG   0x00000101 /* bit 8 (+ bit 0): source = analog decoder (Windows composite) */
#define WDMA_CTL2_625      0x00000010 /* bit 4: 625-line / 50 Hz analog timing (Windows PAL, re/notes/pal-probe3.txt) */
#define VIN_CONTROL_HDMI   0x00000d02 /* bits 1:0 = 2: HDMI selected */
#define VIN_CONTROL_OFF    0x00000d00
#define DMA_CMD_FLAGS      0x10000000
#define DMA_CMD_TO_CARD    0x20000000 /* host -> card memory (Windows FUN_0003f800 direction 1) */
#define DMA_CMD_GO         0x80000000
/* PciDmaPitchRes low field = line length in dwords (Windows: 0x3c0 for 1080p) */
#define SG_MAX_LEN         0x1f000    /* Windows splits runs at this length */
#define INT_VSYNC          BIT(23)
#define INT_DMA_DONE       BIT(20)
#define INT_STICKY         BIT(24)    /* field id (toggles per 1080i field, constant 1 otherwise) */
#define INT_FIELD_TOP_NEXT BIT(24)    /* read right after a field ended: 1 = the next field is the top one */
#define WDMA_CTL_FIELD(p5) (0x80000000 | (p5) << 24) /* p5 0 = even rows, 1 = odd rows (with a pitch) */

#define INT_MASTER         BIT(31)    /* master enable: no interrupt is sent without it */
#define TS_TIMEOUT_MS      200
#define DMA_TIMEOUT_MS     100

static int use_irq = 1;
module_param(use_irq, int, 0644);
MODULE_PARM_DESC(use_irq, "0 = poll registers, 1 = MSI (default), 2 = legacy INTx (shared line!)");

static int vin_code = 0x0d;
module_param(vin_code, int, 0644);
MODULE_PARM_DESC(vin_code, "VinControl mode code (bits 12:8) while capturing (default 0x0d, worked for 720p-1080p/i; "
		 "experiment: 0x10 480i, 0x11 576i, 0x12 480p, 0x13 576p; never 0x03 - stops capture)");

/*
 * PAL 288p (progressive console on a 625-line decoder): the card stores every other field one line lower
 * (re/notes/pal-wobble-pal50.txt). InterruptStatus0 bit 24, read right after the field landed, tells them
 * apart: 0 = the lower one (re/notes/pal-probe6.txt, 80/80 fields); its copy starts one line later.
 */
static int pal_field_fix = 1;
module_param(pal_field_fix, int, 0644);
MODULE_PARM_DESC(pal_field_fix, "Composite PAL 288p: align the alternating fields (1 = on, default; "
		 "-1 = inverted flag; 0 = off)");

static int analog_field_swap;
module_param(analog_field_swap, int, 0644);
MODULE_PARM_DESC(analog_field_swap, "Composite 480i/576i: 1 = store the first captured field on the odd rows "
		 "(the field order for analog is not detected; try this if motion combs)");

static uint int_enable = 0x80900000;
module_param(int_enable, uint, 0644);
MODULE_PARM_DESC(int_enable, "InterruptEnable0 value (default 0x80900000 = master | vsync | DMA done)");

/*
 * Register values the Windows driver had programmed during HDMI preview
 * (re/notes/win/2-preview.txt), applied after the table defaults. MemoryTargetControl stays 0
 * so that debugfs bar1 shows on-board slot 0.
 */
static const struct { u16 off; u32 val; } windows_values[] = {
	{ GAIA_GP_OENABLE, 0x0000007f },
	{ GAIA_CODEC_FLASH_ADDR, 0x8004006f },
	{ GAIA_VCXO_DAC_VALUE, 0x80008000 },
	{ GAIA_AUDIO_PLL_CTL, 0x00000002 },
	{ GAIA_PLL_CONTROL, 0x0103fc60 },
	{ GAIA_PLL_CONTROL2, 0x00002000 },
	{ GAIA_GENLOCK_PRELOAD, 0x00010000 },
	{ GAIA_GENLOCK_FWCONTROL, 0x00d180d1 },
	{ GAIA_GENLOCK_SOURCE_CONTROL, 0x00000000 },
	{ GAIA_GENLOCK_CONTROL, 0x00000401 },
	{ GAIA_SYSTEM_CLOCK_RELOAD, 0x000184e8 },
	{ GAIA_CLOCK_LOAD_COUNT, 0x0004b87f },
	{ GAIA_SYSTEM_CLOCK_CONTROL, 0x00000001 },
	{ GAIA_VIN_CONTROL_ANALOG, 0xc0000000 },
	{ GAIA_VIN_CONTROL_HDMI, 0x80000000 },
	{ GAIA_VIN_CLIPPERS_CONTROL, 0x00000000 },
	{ GAIA_VIN_CONTROL, VIN_CONTROL_HDMI },
	{ GAIA_VIDEO_WDMA_CONTROL2, WDMA_CTL2_VALUE },
};

/*
 * Register values that differ between the Windows HDMI and composite captures (re/notes/win-regshadow*.txt,
 * re/notes/win/pal/). Index 0 = HDMI, 1 = analog NTSC, 2 = analog PAL.
 */
static const struct { u16 off; u32 val[3]; } input_values[] = {
	{ GAIA_GENLOCK_CONTROL, { 0x00000401, 0x00001000, 0x00001101 } },
	{ GAIA_SYSTEM_CLOCK_RELOAD, { 0x000184e8, 0x00008f48, 0x0000f988 } },
	{ GAIA_CLOCK_LOAD_COUNT, { 0x0004b87f, 0x00037176, 0x00042060 } },
};

/* Genlock + system clock values for 0 = HDMI, 1 = analog NTSC, 2 = analog PAL timing */
static void set_timing(struct mxo2 *m, int col)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(input_values); i++)
		mxo2_wr(m, input_values[i].off, input_values[i].val[col]);
}

static void gaia_setup(struct mxo2 *m)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(gaia_regs); i++)
		if (gaia_regs[i].access == GAIA_WO || gaia_regs[i].access == GAIA_RW)
			mxo2_wr(m, gaia_regs[i].off, gaia_regs[i].init);
	for (i = 0; i < ARRAY_SIZE(windows_values); i++)
		mxo2_wr(m, windows_values[i].off, windows_values[i].val);
	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL, 0);
	mxo2_wr(m, GAIA_MEMORY_TARGET_CONTROL, 0);
}

/* ---- host buffers ---- */

void mxo2_hbuf_free(struct mxo2 *m, struct mxo2_hbuf *b)
{
	struct device *dev = &m->pdev->dev;
	unsigned int i;

	if (b->vaddr)
		vunmap(b->vaddr);
	for (i = 0; i < MXO2_FRAME_PAGES; i++) {
		if (!b->pages[i])
			continue;
		if (b->dma[i])
			dma_unmap_page(dev, b->dma[i], PAGE_SIZE, DMA_FROM_DEVICE);
		__free_page(b->pages[i]);
	}
	if (b->sg)
		dma_free_coherent(dev, b->sg_size, b->sg, b->sg_dma);
	memset(b, 0, sizeof(*b));
}

/* A host buffer of `bytes` (<= MXO2_FRAME_BYTES) with the card SG list for exactly those bytes. */
int mxo2_hbuf_alloc(struct mxo2 *m, struct mxo2_hbuf *b, u32 bytes)
{
	struct device *dev = &m->pdev->dev;
	unsigned int i, n = 0, np = DIV_ROUND_UP(bytes, PAGE_SIZE);
	u64 run_addr = 0;
	u32 run_len = 0;
	u8 *e;

	if (!bytes || bytes > MXO2_FRAME_BYTES)
		return -EINVAL;
	b->npages = np;
	for (i = 0; i < np; i++) {
		b->pages[i] = alloc_page(GFP_KERNEL | __GFP_DMA32);
		if (!b->pages[i])
			goto err;
		b->dma[i] = dma_map_page(dev, b->pages[i], 0, PAGE_SIZE, DMA_FROM_DEVICE);
		if (dma_mapping_error(dev, b->dma[i])) {
			b->dma[i] = 0;
			goto err;
		}
	}
	b->vaddr = vmap(b->pages, np, VM_MAP, PAGE_KERNEL);
	if (!b->vaddr)
		goto err;

	b->sg_size = PAGE_ALIGN(np * 12);
	b->sg = dma_alloc_coherent(dev, b->sg_size, &b->sg_dma, GFP_KERNEL);
	if (!b->sg)
		goto err;

	/* One entry per run of physically contiguous pages, each shorter than 0x20000 bytes. */
	e = b->sg;
	for (i = 0; i <= np; i++) {
		u32 len = 0;

		if (i < np)
			len = min_t(u32, PAGE_SIZE, bytes - i * PAGE_SIZE);
		if (i < np && run_len && b->dma[i] == run_addr + run_len &&
		    run_len + len <= SG_MAX_LEN) {
			run_len += len;
			continue;
		}
		if (run_len) {
			put_unaligned_le64(run_addr, e);
			put_unaligned_le32(run_len, e + 8);
			e += 12;
			n++;
		}
		run_addr = i < np ? b->dma[i] : 0;
		run_len = len;
	}
	b->sg_entries = n;
	return 0;
err:
	mxo2_hbuf_free(m, b);
	return -ENOMEM;
}

/* Hand a host buffer to the device (before a DMA) or back to the CPU (after it). */
void mxo2_hbuf_sync(struct mxo2 *m, struct mxo2_hbuf *b, bool for_cpu)
{
	unsigned int i;

	for (i = 0; i < b->npages; i++) {
		if (for_cpu)
			dma_sync_single_for_cpu(&m->pdev->dev, b->dma[i], PAGE_SIZE, DMA_FROM_DEVICE);
		else
			dma_sync_single_for_device(&m->pdev->dev, b->dma[i], PAGE_SIZE, DMA_FROM_DEVICE);
	}
}

/*
 * Build the card's SG list (12-byte entries {u64 bus address, u32 length}) for the first
 * `bytes` bytes of a DMA-mapped scatterlist; runs are split at SG_MAX_LEN and touching ones
 * merged. Returns the number of entries; `sg` must hold enough (see mxo2_v4l2.c).
 */
unsigned int mxo2_build_card_sg(u8 *sg, struct scatterlist *sgl, unsigned int nents, u32 bytes)
{
	struct scatterlist *s;
	u64 run_addr = 0;
	u32 run_len = 0;
	unsigned int i, n = 0;

	for_each_sg(sgl, s, nents, i) {
		u64 addr = sg_dma_address(s);
		u32 len = min_t(u32, sg_dma_len(s), bytes);

		bytes -= len;
		while (len) {
			u32 piece;

			if (run_len && addr == run_addr + run_len && run_len < SG_MAX_LEN) {
				piece = min(len, SG_MAX_LEN - run_len);
				run_len += piece;
			} else {
				if (run_len) {
					put_unaligned_le64(run_addr, sg + 12 * n);
					put_unaligned_le32(run_len, sg + 12 * n + 8);
					n++;
				}
				piece = min_t(u32, len, SG_MAX_LEN);
				run_addr = addr;
				run_len = piece;
			}
			addr += piece;
			len -= piece;
		}
		if (!bytes)
			break;
	}
	if (run_len) {
		put_unaligned_le64(run_addr, sg + 12 * n);
		put_unaligned_le32(run_len, sg + 12 * n + 8);
		n++;
	}
	return n;
}

/* ---- interrupts ---- */

static irqreturn_t mxo2_irq(int irq, void *data)
{
	struct mxo2 *m = data;
	u32 pending = ioread32(m->regs + GAIA_INTERRUPT_STATUS0) & READ_ONCE(m->int_enable) &
		      ~(INT_STICKY | INT_MASTER);

	if (!pending)
		return IRQ_NONE;        /* shared line: not ours */
	m->int_bits_seen |= pending;
	iowrite32(pending, m->regs + GAIA_INTERRUPT_CLEAR0);
	iowrite32(0, m->regs + GAIA_INTERRUPT_CLEAR0);
	if (pending & INT_VSYNC) {
		/* field period, averaged over ~64 fields (analog audio block size) */
		u64 now = ktime_get_ns(), d = now - m->vsync_ns;
		u32 f = READ_ONCE(m->field_ns);

		m->vsync_ns = now;
		if (d > 5 * NSEC_PER_MSEC && d < 50 * NSEC_PER_MSEC)
			WRITE_ONCE(m->field_ns, f ? (u32)(f + div_s64((s64)d - f, 64)) : (u32)d);
		set_bit(MXO2_EV_VSYNC, &m->irq_events);
	}
	if (pending & INT_DMA_DONE)
		set_bit(MXO2_EV_DMA, &m->irq_events);
	m->stats.irqs++;
	wake_up(&m->irq_wq);
	return IRQ_HANDLED;
}

static int irq_setup(struct mxo2 *m)
{
	struct pci_dev *pdev = m->pdev;
	int ret;

	m->irq = -1;
	if (!use_irq)
		return 0;
	ret = pci_alloc_irq_vectors(pdev, 1, 1, use_irq == 2 ? PCI_IRQ_INTX : PCI_IRQ_MSI);
	if (ret < 0)
		return ret;
	m->irq_msi = pdev->msi_enabled;
	m->irq = pci_irq_vector(pdev, 0);
	m->irq_events = 0;
	m->int_bits_seen = 0;
	m->vsync_ns = 0;
	m->field_ns = 0;
	/* start clean: pulse-clear everything, as the Windows driver does */
	mxo2_wr(m, GAIA_INTERRUPT_CLEAR0, 0xffffffff);
	mxo2_wr(m, GAIA_INTERRUPT_CLEAR0, 0);
	ret = request_irq(m->irq, mxo2_irq, m->irq_msi ? 0 : IRQF_SHARED, "mxo2", m);
	if (ret) {
		pci_free_irq_vectors(pdev);
		m->irq = -1;
		return ret;
	}
	m->int_enable = int_enable;
	mxo2_wr(m, GAIA_INTERRUPT_ENABLE0, m->int_enable);
	dev_info(&pdev->dev, "using %s interrupt %d, InterruptEnable0 %08x\n",
		 m->irq_msi ? "MSI" : "INTx", m->irq, m->int_enable);
	return 0;
}

static void irq_teardown(struct mxo2 *m)
{
	if (m->irq < 0)
		return;
	m->int_enable = 0;
	mxo2_wr(m, GAIA_INTERRUPT_ENABLE0, 0);
	free_irq(m->irq, m);
	pci_free_irq_vectors(m->pdev);
	m->irq = -1;
}

/* Wait for an event bit from the IRQ handler; true if it arrived. */
static bool wait_event_bit(struct mxo2 *m, int ev, unsigned int ms)
{
	wait_event_interruptible_timeout(m->irq_wq,
					 test_bit(ev, &m->irq_events) || kthread_should_stop(),
					 msecs_to_jiffies(ms));
	return test_and_clear_bit(ev, &m->irq_events);
}

/* ---- capture thread ---- */

/*
 * Wait until VideoWDmaTimeStamp1 changes (a frame was written). Wakes on every vsync; each wake also
 * services audio (queue the next block, copy finished ones) since the DMA engine is idle here.
 */
static bool wait_ts_change(struct mxo2 *m, u32 *ts)
{
	unsigned long end = jiffies + msecs_to_jiffies(TS_TIMEOUT_MS);
	unsigned long poll_end = 0;
	bool polling_after_vsync = false;
	u32 v;

	while (!kthread_should_stop()) {
		mxo2_audio_service(m, true);
		v = mxo2_rd(m, GAIA_VIDEO_WDMA_TIME_STAMP1);
		if (v != *ts) {
			*ts = v;
			return true;
		}
		if (time_after(jiffies, end))
			return false;
		if (m->irq >= 0 && !polling_after_vsync) {
			/* sleep until the next frame boundary, then poll briefly in case
			 * TS1 is updated a little after the vsync interrupt */
			/* audio user: wake at least every 20 ms (silence without a source) */
			wait_event_bit(m, MXO2_EV_VSYNC,
				       READ_ONCE(m->cap_users) & MXO2_USER_AUDIO ? 20 : TS_TIMEOUT_MS);
			poll_end = jiffies + msecs_to_jiffies(2) + 1;
			polling_after_vsync = true;
			continue;
		}
		if (polling_after_vsync && time_after(jiffies, poll_end))
			polling_after_vsync = false;
		else
			usleep_range(200, 400);
	}
	return false;
}

/* VideoWDmaControl2 for the running capture: source select, line length in dwords for analog/weave */
static u32 wdma_ctl2(struct mxo2 *m, bool with_width)
{
	if (m->cap_analog)      /* Windows: 0x01680101 NTSC / 0x01680111 PAL; packs 240p fields with Control bit 25 */
		return WDMA_CTL2_ANALOG | (m->cap_pal ? WDMA_CTL2_625 : 0) |
		       ((m->cap_line_bytes / 4) & 0x7ff) << 16;
	return WDMA_CTL2_VALUE | (with_width ? ((m->cap_line_bytes / 4) & 0x7ff) << 16 : 0);
}

static void arm_slot(struct mxo2 *m, unsigned int slot)
{
	mxo2_wr(m, GAIA_VIDEO_WDMA_ADDRESS, (slot * MXO2_FRAME_BYTES) & 0x3fffffff);
	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL, WDMA_CTL_CAPTURE);
}

/*
 * Copy between card memory at `onboard` and host memory described by a card SG list (to_card: host -> card);
 * waits until done. Vsyncs during the wait queue the next audio block (no DMA from there: the engine is busy).
 */
int mxo2_dma(struct mxo2 *m, u32 onboard, dma_addr_t sg_dma, unsigned int sg_entries, u32 width, bool to_card)
{
	u32 cmd = DMA_CMD_FLAGS | (to_card ? DMA_CMD_TO_CARD : 0);
	u32 count = mxo2_rd(m, GAIA_PCI_DMA_COMMAND_COUNT);
	ktime_t t0 = ktime_get();
	unsigned long end;
	u32 us;

	clear_bit(MXO2_EV_DMA, &m->irq_events);
	mxo2_wr(m, GAIA_PCI_DMA_FRAME, onboard & 0x3fffffff);
	mxo2_wr(m, GAIA_PCI_DMA_SGADDRESS_LOWER, lower_32_bits(sg_dma));
	mxo2_wr(m, GAIA_PCI_DMA_SGADDRESS_UPPER, upper_32_bits(sg_dma));
	mxo2_wr(m, GAIA_PCI_DMA_SGCOUNT, sg_entries * 3);
	mxo2_wr(m, GAIA_PCI_DMA_PITCH_RES, width & 0x7ff);
	mxo2_wr(m, GAIA_PCI_DMA_COMMAND, cmd);
	mxo2_wr(m, GAIA_PCI_DMA_COMMAND, cmd | DMA_CMD_GO);

	end = jiffies + msecs_to_jiffies(DMA_TIMEOUT_MS);
	while (mxo2_rd(m, GAIA_PCI_DMA_COMMAND_COUNT) == count) {
		if (time_after(jiffies, end)) {
			m->stats.dma_timeouts++;
			return -ETIMEDOUT;
		}
		if (m->irq >= 0) {
			wait_event_interruptible_timeout(m->irq_wq,
				test_bit(MXO2_EV_DMA, &m->irq_events) ||
				test_bit(MXO2_EV_VSYNC, &m->irq_events) || kthread_should_stop(),
				msecs_to_jiffies(DMA_TIMEOUT_MS));
			if (test_and_clear_bit(MXO2_EV_VSYNC, &m->irq_events))
				mxo2_audio_arm(m);
			clear_bit(MXO2_EV_DMA, &m->irq_events);
		} else {
			usleep_range(500, 1000);
		}
	}
	us = ktime_us_delta(ktime_get(), t0);
	m->stats.last_dma_us = us;
	m->stats.max_dma_us = max(m->stats.max_dma_us, us);
	return 0;
}

int mxo2_dma_to_host(struct mxo2 *m, u32 onboard, dma_addr_t sg_dma, unsigned int sg_entries, u32 width)
{
	return mxo2_dma(m, onboard, sg_dma, sg_entries, width, false);
}

/* Card address of a finished slot's first delivered line */
u32 mxo2_slot_offset(struct mxo2 *m, unsigned int slot)
{
	return slot * MXO2_FRAME_BYTES + m->cap_skip + m->slot_skip[slot];
}

/* debugfs test path: copy into the non-latest host buffer and publish it */
static void hbuf_frame(struct mxo2 *m, unsigned int slot, u32 ts, u64 seq)
{
	struct mxo2_hbuf *b;
	int target;

	target = m->latest == 0 ? 1 : 0;
	b = &m->hbuf[target];
	mxo2_hbuf_sync(m, b, false);
	if (!mxo2_dma_to_host(m, mxo2_slot_offset(m, slot), b->sg_dma, b->sg_entries,
			      m->cap_analog ? m->cap_line_bytes / 4 : 1920 * 2 / 4)) {
		mxo2_hbuf_sync(m, b, true);
		b->ts = ts;
		b->seq = seq;
		m->latest = target;
		m->stats.dma_done++;
	}
}

/*
 * Runs while any user (debugfs, V4L2, ALSA) is active. The video WDMA is always re-armed (it paces
 * the loop); finished frames are copied only for a video user. frame_lock keeps a video user from
 * going away while its frame is being delivered.
 */
/* Hand a finished on-board frame to the current video user (V4L2 or debugfs), if any. */
static void deliver_frame(struct mxo2 *m, unsigned int slot, u32 ts, u64 seq)
{
	unsigned int users;

	mutex_lock(&m->frame_lock);
	users = m->cap_users;
	if (users & MXO2_USER_V4L2)
		mxo2_v4l2_frame(m, slot);
	else if (users & MXO2_USER_DEBUGFS)
		hbuf_frame(m, slot, ts, seq);
	mutex_unlock(&m->frame_lock);
}

/* Progressive input: one WDMA arm = one frame; copy the slot armed two frames ago. */
static void capture_loop_progressive(struct mxo2 *m)
{
	u32 ts = mxo2_rd(m, GAIA_VIDEO_WDMA_TIME_STAMP1);
	u64 n = 0;

	arm_slot(m, 0);
	while (!kthread_should_stop()) {
		if (!wait_ts_change(m, &ts)) {
			if (kthread_should_stop())
				break;
			m->stats.ts_timeouts++;
			arm_slot(m, n % MXO2_ONBOARD_SLOTS); /* no signal? keep trying */
			continue;
		}
		m->stats.ts_changes++;
		if (m->cap_pal) {               /* field flag before re-arming, as probe 6 read it */
			int fix = READ_ONCE(pal_field_fix);
			bool lower = !(mxo2_rd(m, GAIA_INTERRUPT_STATUS0) & INT_STICKY) ^ (fix < 0);

			m->slot_skip[n % MXO2_ONBOARD_SLOTS] = fix && lower ? m->cap_line_bytes : 0;
		}
		n++;
		arm_slot(m, n % MXO2_ONBOARD_SLOTS);   /* slot for the next frame */
		mxo2_output_frame(m, (n - 1) % MXO2_ONBOARD_SLOTS, WDMA_CTL_CAPTURE);
		if (n >= 2)
			deliver_frame(m, (n - 2) % MXO2_ONBOARD_SLOTS, ts, n - 2);
	}
}

/* VideoWDmaControl for one field of a woven frame */
static u32 field_ctl(struct mxo2 *m, bool top)
{
	bool odd = !top;

	if (m->cap_analog && analog_field_swap)
		odd = !odd;
	return WDMA_CTL_FIELD(odd ? 1 : 0);
}

static void arm_field(struct mxo2 *m, unsigned int slot, bool top)
{
	mxo2_wr(m, GAIA_VIDEO_WDMA_ADDRESS, (slot * MXO2_FRAME_BYTES) & 0x3fffffff);
	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL, field_ctl(m, top));
}

/*
 * Which field to arm next. HDMI: InterruptStatus0 bit 24, read right after a field ended. Analog: that bit
 * stays set (re/notes/analog-probe4.txt) and no field flag is known, so the fields simply alternate.
 */
static bool next_top(struct mxo2 *m, bool last_top)
{
	if (m->cap_analog)
		return !last_top;
	return !!(mxo2_rd(m, GAIA_INTERRUPT_STATUS0) & INT_FIELD_TOP_NEXT);
}

/*
 * Interlaced input (re/notes/interlace-probe*.txt): one WDMA arm = one field. With a line pitch in
 * VideoWDmaControl2[26:16], scan mode 0 writes the field to the even rows and mode 1 to the odd rows of
 * the slot, so a top + bottom pair is woven by the hardware. Which field comes next is InterruptStatus0
 * bit 24, read right after the previous field ended. A frame is complete when a bottom field lands in the
 * slot whose top field came just before; it is copied one field later (like the progressive lag).
 */
static void capture_loop_interlaced(struct mxo2 *m)
{
	u32 ts = mxo2_rd(m, GAIA_VIDEO_WDMA_TIME_STAMP1);
	unsigned int slot = 0, armed_slot, prev_slot = ~0u;
	bool armed_top, prev_top = false, top;
	int pending = -1;               /* complete frame waiting one field before its copy */
	u64 frames = 0;

	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL2, wdma_ctl2(m, true));
	armed_top = next_top(m, false);
	armed_slot = slot;
	arm_field(m, slot, armed_top);
	while (!kthread_should_stop()) {
		int done = -1;

		if (!wait_ts_change(m, &ts)) {
			if (kthread_should_stop())
				break;
			m->stats.ts_timeouts++;
			prev_slot = ~0u;        /* sequence broken: start a new frame */
			armed_top = next_top(m, false);
			armed_slot = slot;
			arm_field(m, slot, armed_top);
			continue;
		}
		/* the field armed last has landed */
		mxo2_output_frame(m, armed_slot, field_ctl(m, armed_top));
		if (!armed_top && prev_top && prev_slot == armed_slot) {
			done = armed_slot;
			m->stats.ts_changes++;  /* counts complete frames */
		}
		top = next_top(m, armed_top);
		if (top)
			slot = (slot + 1) % MXO2_ONBOARD_SLOTS;    /* a new frame starts */
		prev_top = armed_top;
		prev_slot = armed_slot;
		armed_top = top;
		armed_slot = slot;
		arm_field(m, slot, top);
		if (pending >= 0)
			deliver_frame(m, pending, ts, frames++);
		pending = done;
	}
	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL2, wdma_ctl2(m, false));
}

/*
 * Runs while any user (debugfs, V4L2, ALSA) is active. The video WDMA is always re-armed (it paces
 * the loop); finished frames are copied only for a video user. frame_lock keeps a video user from
 * going away while its frame is being delivered.
 */
static int capture_thread(void *data)
{
	struct mxo2 *m = data;

	if (m->cap_interlaced)
		capture_loop_interlaced(m);
	else
		capture_loop_progressive(m);
	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL, 0);
	mxo2_audio_reset(m);
	return 0;
}

static void rx_work_fn(struct work_struct *work)
{
	struct mxo2 *m = container_of(to_delayed_work(work), struct mxo2, rx_work);

	mxo2_rx_monitor(m);
	if (READ_ONCE(m->hw_ready))
		mxo2_output_poll(m);
	if (READ_ONCE(m->hw_ready))
		schedule_delayed_work(&m->rx_work, msecs_to_jiffies(500));
}

/* Gaia defaults + Windows values, HDMI receiver, input (at probe and after a resume). */
static int hw_setup(struct mxo2 *m, bool probe)
{
	int ret;

	gaia_setup(m);
	mxo2_wr(m, GAIA_VIN_CONTROL, VIN_CONTROL_OFF);
	m->rx_mode = -1;
	ret = mxo2_rx_setup(m);         /* needs the table defaults (SRI) */
	if (ret)
		return ret;
	ret = mxo2_input_init(m, probe);
	if (ret)
		return ret;
	mxo2_rx_detect(m);
	WRITE_ONCE(m->hw_ready, true);
	schedule_delayed_work(&m->rx_work, msecs_to_jiffies(500));
	return 0;
}

/* Called at probe. */
int mxo2_hw_init(struct mxo2 *m)
{
	int ret;

	ret = dma_set_mask_and_coherent(&m->pdev->dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;
	INIT_DELAYED_WORK(&m->rx_work, rx_work_fn);
	return hw_setup(m, true);
}

void mxo2_hw_fini(struct mxo2 *m)
{
	WRITE_ONCE(m->hw_ready, false);
	cancel_delayed_work_sync(&m->rx_work);
}

#define VIDEO_USERS (MXO2_USER_DEBUGFS | MXO2_USER_V4L2)

/*
 * video: a video user will be capturing. Only then the other input's chip is powered down as on Windows;
 * for audio / output-only captures both stay on, so Auto still sees a signal on the other input.
 */
static int thread_start(struct mxo2 *m, bool video)
{
	struct device *dev = &m->pdev->dev;
	int ret;

	mxo2_input_power(m, video);
	m->latest = -1;
	memset(&m->stats, 0, sizeof(m->stats));
	m->stats.started_ns = ktime_get_ns();
	m->cap_interlaced = mxo2_cap_weave(m);  /* fixed until the thread stops */
	m->cap_analog = m->input != MXO2_INPUT_HDMI;
	m->cap_pal = m->cap_analog && m->in_vtotal == 625;
	m->cap_line_bytes = (m->in_width ? m->in_width : m->cap_analog ? 720 : 1920) * 2;
	/*
	 * NTSC composite: the card stores 243 lines per field (486 per frame, re/notes/analog-probe5.txt);
	 * deliver 240 / 480 by skipping the first 2 / 4 lines (an even count keeps the field parity).
	 * PAL: 288 lines per packed field, 287 per woven field (574 rows; re/notes/pal-probe3.txt), no skip.
	 */
	m->cap_skip = 0;
	memset(m->slot_skip, 0, sizeof(m->slot_skip));
	if (m->cap_analog && (m->in_height == 480 || m->in_height == 240))
		m->cap_skip = (m->cap_interlaced ? 4 : 2) * m->cap_line_bytes;
	mxo2_audio_reset(m);
	set_timing(m, m->cap_analog ? 1 + m->cap_pal : 0);
	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL, 0);
	/* bit 16: component data layout (Windows MVKAVBUS FUN_0002bf54; without it Cb/Cr swap and the picture
	 * shifts, re/notes/comp-probe.txt) */
	mxo2_wr(m, GAIA_VIN_CONTROL_ANALOG, m->input == MXO2_INPUT_COMPONENT ? 0xc0010000 : 0xc0000000);
	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL2, wdma_ctl2(m, false));
	/* VinControl bits 1:0 = 2 HDMI, 0 analog (MVKAVBUS FUN_0002bf54) */
	mxo2_wr(m, GAIA_VIN_CONTROL, ((m->cap_analog ? VIN_CONTROL_OFF : VIN_CONTROL_HDMI) & ~0x1f00) |
		(vin_code & 0x1f) << 8);
	mxo2_output_start(m);
	ret = irq_setup(m);
	if (ret) {
		dev_err(dev, "interrupt setup failed (%d), use use_irq=0 to poll\n", ret);
		goto err;
	}
	pci_set_master(m->pdev);
	m->cap_thread = kthread_run(capture_thread, m, "mxo2-cap");
	if (IS_ERR(m->cap_thread)) {
		ret = PTR_ERR(m->cap_thread);
		m->cap_thread = NULL;
		pci_clear_master(m->pdev);
		irq_teardown(m);
		goto err;
	}
	return 0;
err:
	mxo2_output_stop(m);
	mxo2_wr(m, GAIA_VIN_CONTROL, VIN_CONTROL_OFF);
	mxo2_input_power(m, false);
	return ret;
}

static void thread_stop(struct mxo2 *m)
{
	kthread_stop(m->cap_thread);
	m->cap_thread = NULL;
	mxo2_wr(m, GAIA_VIDEO_WDMA_CONTROL, 0);
	mxo2_wr(m, GAIA_PCI_DMA_COMMAND, 0);
	mxo2_wr(m, GAIA_VIN_CONTROL, VIN_CONTROL_OFF);
	mxo2_output_stop(m);
	msleep(50);                     /* let a transfer still in flight finish */
	if (!m->playing)
		pci_clear_master(m->pdev);
	irq_teardown(m);
	mxo2_input_power(m, false);
	dev_info(&m->pdev->dev, "capture stopped: %llu frames, %llu audio blocks copied\n",
		 m->stats.dma_done, m->audio.blocks);
}

/*
 * Weave two fields per frame? HDMI: for interlaced signals. Composite: only for an interlaced signal delivered at
 * full height (480/576); 240/288-line formats take one packed field per frame (also from a 480i signal).
 */
bool mxo2_cap_weave(struct mxo2 *m)
{
	if (m->input == MXO2_INPUT_HDMI)
		return m->in_interlaced;
	return m->in_interlaced && (m->streaming ? m->streaming->height : 480) >= 480;
}

/* The running thread's mode no longer matches the input (it was started for audio, or the input changed). */
static bool cap_mode_stale(struct mxo2 *m)
{
	bool analog = m->input != MXO2_INPUT_HDMI;

	return m->cap_analog != analog || m->cap_interlaced != mxo2_cap_weave(m) ||
	       m->cap_pal != (analog && m->in_vtotal == 625) ||
	       m->cap_line_bytes != (m->in_width ? m->in_width : analog ? 720 : 1920) * 2;
}

/* Take a capture user; the thread starts with the first one. Only one video user at a time. */
int mxo2_capture_get(struct mxo2 *m, unsigned int user)
{
	unsigned int video = VIDEO_USERS;
	int i, ret = 0;

	mutex_lock(&m->cap_lock);
	if (!m->hw_ready) {
		ret = -ENODEV;
		goto out;
	}
	if (m->playing) {               /* one DMA engine and one output timing: playback has them */
		ret = -EBUSY;
		goto out;
	}
	if ((m->cap_users & user) || ((user & video) && (m->cap_users & video))) {
		ret = -EBUSY;
		goto out;
	}
	if (user == MXO2_USER_DEBUGFS) {
		for (i = 0; i < MXO2_HOST_BUFS; i++) {
			ret = mxo2_hbuf_alloc(m, &m->hbuf[i], MXO2_FRAME_BYTES);
			if (ret) {
				while (i--)
					mxo2_hbuf_free(m, &m->hbuf[i]);
				goto out;
			}
		}
	}
	if (m->cap_thread && (user & video) && cap_mode_stale(m))
		thread_stop(m);         /* audio / output thread: restarted below in the current video mode */
	else if (m->cap_thread && (user & video))
		mxo2_input_power(m, true);      /* video joins a running audio / output capture */
	if (!m->cap_thread) {
		ret = thread_start(m, (user | m->cap_users) & video);
		if (ret) {
			if (user == MXO2_USER_DEBUGFS)
				for (i = 0; i < MXO2_HOST_BUFS; i++)
					mxo2_hbuf_free(m, &m->hbuf[i]);
			goto out;
		}
	}
	mutex_lock(&m->frame_lock);
	m->cap_users |= user;
	mutex_unlock(&m->frame_lock);
out:
	mutex_unlock(&m->cap_lock);
	return ret;
}

/* Drop a capture user. After return the thread no longer touches that user's buffers. */
void mxo2_capture_put(struct mxo2 *m, unsigned int user)
{
	int i;

	mutex_lock(&m->cap_lock);
	if (!(m->cap_users & user))
		goto out;
	mutex_lock(&m->frame_lock);     /* waits for a frame being delivered */
	m->cap_users &= ~user;
	if (user == MXO2_USER_DEBUGFS) {
		m->latest = -1;
		for (i = 0; i < MXO2_HOST_BUFS; i++)
			mxo2_hbuf_free(m, &m->hbuf[i]);
	}
	mutex_unlock(&m->frame_lock);
	if (user == MXO2_USER_AUDIO) {  /* wait for an audio delivery in progress */
		mutex_lock(&m->audio.mutex);
		mutex_unlock(&m->audio.mutex);
	}
	if (!m->cap_users && m->cap_thread)
		thread_stop(m);
	else if ((user & VIDEO_USERS) && !(m->cap_users & VIDEO_USERS) && m->cap_thread)
		mxo2_input_power(m, false);     /* audio / output go on: both input chips on again for Auto */
out:
	mutex_unlock(&m->cap_lock);
}

/*
 * Every 500 ms (rx_work): with output=1 the capture runs while the selected input has a signal the outputs can
 * show. An audio / output-only capture follows format changes (a video capture keeps its mode).
 */
void mxo2_output_poll(struct mxo2 *m)
{
	bool want = mxo2_output_wanted(m);
	bool have = READ_ONCE(m->cap_users) & MXO2_USER_OUTPUT;

	if (want && !have) {
		mxo2_capture_get(m, MXO2_USER_OUTPUT);
	} else if (!want && have) {
		mxo2_capture_put(m, MXO2_USER_OUTPUT);
	} else if (have) {
		mutex_lock(&m->cap_lock);
		if (m->cap_thread && !(m->cap_users & VIDEO_USERS) && cap_mode_stale(m)) {
			thread_stop(m);
			if (thread_start(m, false)) {
				dev_err(&m->pdev->dev, "capture restart for the new format failed\n");
				mutex_lock(&m->frame_lock);
				m->cap_users = 0;       /* only audio / output were using it */
				mutex_unlock(&m->frame_lock);
			}
		}
		mutex_unlock(&m->cap_lock);
	}
}

/*
 * Switch the input unless video is being captured; an audio-only capture is restarted in the new mode.
 * Lock order: cap_lock, then rx_lock (in mxo2_select_input).
 */
int mxo2_switch_input(struct mxo2 *m, int input)
{
	bool running;
	int ret;

	mutex_lock(&m->cap_lock);
	if (m->cap_users & (MXO2_USER_V4L2 | MXO2_USER_DEBUGFS)) {
		mutex_unlock(&m->cap_lock);
		return -EBUSY;
	}
	running = m->cap_thread;
	if (running)
		thread_stop(m);
	ret = mxo2_select_input(m, input);
	if (running && m->cap_users && thread_start(m, m->cap_users & VIDEO_USERS))
		dev_err(&m->pdev->dev, "capture restart after the input switch failed\n");
	mutex_unlock(&m->cap_lock);
	return ret;
}

/* System sleep: the card loses its setup. Stop the capture thread; users stay registered. */
void mxo2_capture_suspend(struct mxo2 *m)
{
	WRITE_ONCE(m->hw_ready, false);
	cancel_delayed_work_sync(&m->rx_work);
	mutex_lock(&m->cap_lock);
	m->resume_capture = m->cap_thread;
	if (m->cap_thread)
		thread_stop(m);
	mutex_unlock(&m->cap_lock);
}

/* Set the card up again (as at probe, same input) and restart a capture that ran before. */
void mxo2_capture_resume(struct mxo2 *m)
{
	int ret = hw_setup(m, false);

	if (ret) {
		dev_err(&m->pdev->dev, "hardware setup after resume failed: %d\n", ret);
		return;
	}
	mutex_lock(&m->cap_lock);
	if (m->resume_capture && m->cap_users && thread_start(m, m->cap_users & VIDEO_USERS))
		dev_err(&m->pdev->dev, "capture restart after resume failed\n");
	m->resume_capture = false;
	mutex_unlock(&m->cap_lock);
	dev_info(&m->pdev->dev, "resumed, input %s\n", mxo2_input_names[m->input]);
}

/*
 * Playback (mxo2_play.c) takes the card: pass-through stops, refused while anything else captures (the PCI DMA
 * engine and the output timing are shared). Sets the timing for output format f.
 */
int mxo2_play_claim(struct mxo2 *m, int f)
{
	int ret = 0;

	mxo2_capture_put(m, MXO2_USER_OUTPUT);
	mutex_lock(&m->cap_lock);
	if (!m->hw_ready)
		ret = -ENODEV;
	else if (m->cap_thread || m->playing)
		ret = -EBUSY;
	if (!ret) {
		m->playing = true;
		set_timing(m, f == MXO2_OUT_1080P30 ? 0 : f == MXO2_OUT_PAL ? 2 : 1);
		pci_set_master(m->pdev);
	}
	mutex_unlock(&m->cap_lock);
	return ret;
}

/* Playback ends: the card is free again (pass-through comes back with the next monitor round). */
void mxo2_play_release(struct mxo2 *m)
{
	mutex_lock(&m->cap_lock);
	m->playing = false;
	if (!m->cap_thread)
		pci_clear_master(m->pdev);
	mutex_unlock(&m->cap_lock);
}

void mxo2_capture_stop_all(struct mxo2 *m)
{
	mxo2_capture_put(m, MXO2_USER_DEBUGFS);
	mxo2_capture_put(m, MXO2_USER_V4L2);
	mxo2_capture_put(m, MXO2_USER_AUDIO);
	mxo2_capture_put(m, MXO2_USER_OUTPUT);
}

/* ---- debugfs ---- */

/* capture: write "start" or "stop"; read shows the state */
static ssize_t capture_write(struct file *f, const char __user *ubuf, size_t len, loff_t *ppos)
{
	struct mxo2 *m = f->private_data;
	char buf[8] = {};
	int ret;

	if (copy_from_user(buf, ubuf, min(len, sizeof(buf) - 1)))
		return -EFAULT;
	if (sysfs_streq(buf, "start")) {
		ret = mxo2_capture_get(m, MXO2_USER_DEBUGFS);
	} else if (sysfs_streq(buf, "stop")) {
		mxo2_capture_put(m, MXO2_USER_DEBUGFS);
		ret = 0;
	} else {
		ret = -EINVAL;
	}
	return ret ? ret : len;
}

/* int_enable: write a hex value to change InterruptEnable0 while capturing (for experiments) */
static ssize_t int_enable_write(struct file *f, const char __user *ubuf, size_t len, loff_t *ppos)
{
	struct mxo2 *m = f->private_data;
	u32 v;
	int ret = kstrtou32_from_user(ubuf, len, 16, &v);

	if (ret)
		return ret;
	mutex_lock(&m->cap_lock);
	if (m->irq < 0) {
		mutex_unlock(&m->cap_lock);
		return -ENODEV;
	}
	WRITE_ONCE(m->int_enable, v);
	m->int_bits_seen = 0;
	mxo2_wr(m, GAIA_INTERRUPT_ENABLE0, v);
	mutex_unlock(&m->cap_lock);
	return len;
}

static const struct file_operations int_enable_fops = {
	.owner = THIS_MODULE,
	.open = simple_open,
	.write = int_enable_write,
};

static const struct file_operations capture_fops = {
	.owner = THIS_MODULE,
	.open = simple_open,
	.write = capture_write,
};

static int status_show(struct seq_file *s, void *unused)
{
	struct mxo2 *m = s->private;
	struct mxo2_stats st = m->stats;
	u64 secs_x100 = st.started_ns ? div_u64(ktime_get_ns() - st.started_ns, 10000000) : 0;

	seq_printf(s, "running        %s%s%s%s%s\n", m->cap_thread ? "yes" : "no",
		   m->cap_users & MXO2_USER_V4L2 ? " V4L2" : "",
		   m->cap_users & MXO2_USER_DEBUGFS ? " debugfs" : "",
		   m->cap_users & MXO2_USER_AUDIO ? " audio" : "",
		   m->cap_users & MXO2_USER_OUTPUT ? " output" : "");
	seq_printf(s, "outputs        %s, HDMI OUT %s\n", m->out_format >= 0 ? "showing the input" : "not fed",
		   m->out_hpd ? "TV connected" : "no TV");
	seq_printf(s, "audio rate     %u Hz (channel status %u, clock regeneration %u)%s\n", m->in_audio_rate,
		   m->in_rate_cs, m->in_rate_cts, m->audio.rate_mismatch ? ", STREAM RATE DIFFERS" : "");
	seq_printf(s, "audio          %s, %llu blocks, %llu samples, %llu overruns, %llu DMA errors, "
		   "last block %u samples, spf %llu.%03llu (N %u CTS %u htotal %u vtotal %u)\n",
		   m->audio.active ? "active" : "idle", m->audio.blocks, m->audio.samples_total,
		   m->audio.overruns, m->audio.dma_errors,
		   m->audio.copied ? m->audio.samples[(m->audio.copied - 1) % 8] : 0,
		   m->audio.spf_q24 >> 24, ((m->audio.spf_q24 & 0xffffff) * 1000) >> 24,
		   m->in_n, m->in_cts, m->audio.htotal, m->in_vtotal);
	seq_printf(s, "audio silence  %llu samples (no source)\n", m->audio.silence_samples);
	seq_printf(s, "field period   %u ns (vsync interrupts), audio layout %u B/sample\n", m->field_ns,
		   m->audio.frame_bytes);
	seq_printf(s, "input          %s %ux%u%s @ %u fps, VIC %u%s%s\n",
		   mxo2_input_names[m->input], m->in_width, m->in_height,
		   m->in_interlaced ? "i" : "p", m->in_fps, m->in_vic,
		   m->cap_thread && m->cap_analog ? " (capturing analog)" : "",
		   m->cap_thread && m->cap_interlaced ? " (capturing woven fields)" : "");
	seq_printf(s, "frames_onboard %llu\n", st.ts_changes);
	seq_printf(s, "frames_copied  %llu\n", st.dma_done);
	seq_printf(s, "fps_x100       %llu\n", secs_x100 ? div64_u64(st.dma_done * 10000, secs_x100) : 0);
	seq_printf(s, "ts_timeouts    %llu\n", st.ts_timeouts);
	seq_printf(s, "dma_timeouts   %llu\n", st.dma_timeouts);
	seq_printf(s, "no_buffer      %llu\n", st.no_buffer);
	seq_printf(s, "dma_us_last    %u\n", st.last_dma_us);
	seq_printf(s, "dma_us_max     %u\n", st.max_dma_us);
	seq_printf(s, "sg_entries     %u\n", m->hbuf[0].sg_entries);
	seq_printf(s, "irq            %s %d, %llu handled, enable %08x, bits seen %08x\n",
		   m->irq < 0 ? "none (polling)" : m->irq_msi ? "MSI" : "INTx", m->irq, st.irqs,
		   m->int_enable, m->int_bits_seen);
	seq_printf(s, "ts1 %08x ts2 %08x wdma_status %08x int0 %08x dma_cmdcount %08x\n",
		   mxo2_rd(m, GAIA_VIDEO_WDMA_TIME_STAMP1), mxo2_rd(m, GAIA_VIDEO_WDMA_TIME_STAMP2),
		   mxo2_rd(m, GAIA_VIDEO_WDMA_STATUS), mxo2_rd(m, GAIA_INTERRUPT_STATUS0),
		   mxo2_rd(m, GAIA_PCI_DMA_COMMAND_COUNT));
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(status);

/* frame: each open() takes a copy of the newest frame (raw YUYV as stored by the card) */
struct frame_snap {
	void *data;
	u64 seq;
};

static int frame_open(struct inode *inode, struct file *f)
{
	struct mxo2 *m = inode->i_private;
	struct frame_snap *snap;

	snap = kzalloc(sizeof(*snap), GFP_KERNEL);
	if (!snap)
		return -ENOMEM;
	snap->data = vmalloc(MXO2_FRAME_BYTES);
	if (!snap->data) {
		kfree(snap);
		return -ENOMEM;
	}
	mutex_lock(&m->frame_lock);
	if (m->latest < 0) {
		mutex_unlock(&m->frame_lock);
		vfree(snap->data);
		kfree(snap);
		return -EAGAIN;
	}
	memcpy(snap->data, m->hbuf[m->latest].vaddr, MXO2_FRAME_BYTES);
	snap->seq = m->hbuf[m->latest].seq;
	mutex_unlock(&m->frame_lock);
	f->private_data = snap;
	return 0;
}

static ssize_t frame_read(struct file *f, char __user *buf, size_t len, loff_t *ppos)
{
	struct frame_snap *snap = f->private_data;

	return simple_read_from_buffer(buf, len, ppos, snap->data, MXO2_FRAME_BYTES);
}

static int frame_release(struct inode *inode, struct file *f)
{
	struct frame_snap *snap = f->private_data;

	vfree(snap->data);
	kfree(snap);
	return 0;
}

static const struct file_operations frame_fops = {
	.owner = THIS_MODULE,
	.open = frame_open,
	.read = frame_read,
	.release = frame_release,
	.llseek = default_llseek,
};

void mxo2_capture_debugfs(struct mxo2 *m)
{
	debugfs_create_file("capture", 0200, m->dbg, m, &capture_fops);
	debugfs_create_file("status", 0444, m->dbg, m, &status_fops);
	debugfs_create_file("int_enable", 0200, m->dbg, m, &int_enable_fops);
	debugfs_create_file_size("frame", 0400, m->dbg, m, &frame_fops, MXO2_FRAME_BYTES);
}
