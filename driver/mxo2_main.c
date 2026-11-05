// SPDX-License-Identifier: GPL-2.0
/*
 * Matrox MXO2 (PCI 102b:8350, "Gaia" FPGA) - experimental Linux driver.
 *
 * Stage 1: claim the card, map BAR0 (registers) and BAR1 (on-board memory
 * window) and expose the registers through debugfs. Writes are refused unless allow_write=1.
 * Stage 2 (mxo2_capture.c): HDMI capture with PCI DMA into host memory, controlled through
 * debugfs (capture, status, frame, int_enable). Stage 3 (mxo2_rx.c): HDMI receiver setup over
 * the FPGA's SRI/I2C bridge (debugfs rx).
 *
 * debugfs (usually /sys/kernel/debug/mxo2/<pci-address>/):
 *   regs  - named register dump (write-only registers show the last value
 *           written through this driver)
 *   bar0  - raw register access; pread/pwrite at 4-byte aligned offsets
 *   bar1  - raw read access to the on-board memory window
 *   capture - write "start" / "stop"
 *   status  - capture counters
 *   frame   - newest captured frame (raw YUYV 1920x1080, as stored by the card)
 */

#include <linux/debugfs.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/seq_file.h>
#include <linux/uaccess.h>

#include "gaia_regs.h"
#include "mxo2.h"

#define DRV_NAME "mxo2"
#define BAR0_SIZE MXO2_BAR0_SIZE

static bool allow_write;
module_param(allow_write, bool, 0644);
MODULE_PARM_DESC(allow_write, "Allow register writes through debugfs (default: off)");


static struct dentry *mxo2_dbg_root;

static u8 reg_access(unsigned int off)
{
	size_t i;

	for (i = 0; i < ARRAY_SIZE(gaia_regs); i++)
		if (gaia_regs[i].off == off)
			return gaia_regs[i].access;
	return 0; /* unknown */
}

u32 mxo2_rd(struct mxo2 *m, unsigned int off)
{
	return ioread32(m->regs + off);
}

void mxo2_wr(struct mxo2 *m, unsigned int off, u32 val)
{
	m->shadow[off / 4] = val;
	iowrite32(val, m->regs + off);
}

/* ---- debugfs: named register dump ---- */

static int regs_show(struct seq_file *s, void *unused)
{
	struct mxo2 *m = s->private;
	size_t i;

	mutex_lock(&m->lock);
	for (i = 0; i < ARRAY_SIZE(gaia_regs); i++) {
		const struct gaia_reg_desc *r = &gaia_regs[i];

		if (r->access == GAIA_WO)
			seq_printf(s, "0x%03x %-24s WO shadow=0x%08x\n", r->off, r->name,
				   m->shadow[r->off / 4]);
		else
			seq_printf(s, "0x%03x %-24s %s 0x%08x\n", r->off, r->name,
				   r->access == GAIA_RO ? "RO" : "RW", mxo2_rd(m, r->off));
	}
	mutex_unlock(&m->lock);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(regs);

/* ---- debugfs: raw BAR0 access ---- */

static ssize_t bar0_read(struct file *f, char __user *buf, size_t len, loff_t *ppos)
{
	struct mxo2 *m = f->private_data;
	loff_t pos = *ppos;
	size_t done = 0;

	if ((pos | len) & 3)
		return -EINVAL;
	mutex_lock(&m->lock);
	while (done < len && pos < BAR0_SIZE) {
		u32 v = mxo2_rd(m, pos);

		if (copy_to_user(buf + done, &v, 4)) {
			mutex_unlock(&m->lock);
			return done ? done : -EFAULT;
		}
		done += 4;
		pos += 4;
	}
	mutex_unlock(&m->lock);
	*ppos = pos;
	return done;
}

static ssize_t bar0_write(struct file *f, const char __user *buf, size_t len, loff_t *ppos)
{
	struct mxo2 *m = f->private_data;
	loff_t pos = *ppos;
	size_t done = 0;

	if (!allow_write)
		return -EPERM;
	if ((pos | len) & 3)
		return -EINVAL;
	mutex_lock(&m->lock);
	while (done < len && pos < BAR0_SIZE) {
		u32 v;

		if (copy_from_user(&v, buf + done, 4)) {
			mutex_unlock(&m->lock);
			return done ? done : -EFAULT;
		}
		if (reg_access(pos) == GAIA_RO)
			dev_dbg(&m->pdev->dev, "write to read-only register 0x%03llx\n", pos);
		mxo2_wr(m, pos, v);
		done += 4;
		pos += 4;
	}
	mutex_unlock(&m->lock);
	*ppos = pos;
	return done;
}

static const struct file_operations bar0_fops = {
	.owner = THIS_MODULE,
	.open = simple_open,
	.read = bar0_read,
	.write = bar0_write,
	.llseek = default_llseek,
};

/* ---- debugfs: raw BAR1 read ---- */

static ssize_t bar1_read(struct file *f, char __user *buf, size_t len, loff_t *ppos)
{
	struct mxo2 *m = f->private_data;
	loff_t pos = *ppos;
	size_t done = 0;

	if ((pos | len) & 3)
		return -EINVAL;
	while (done < len && pos < m->mem_len) {
		u32 v = ioread32(m->mem + pos);

		if (copy_to_user(buf + done, &v, 4))
			return done ? done : -EFAULT;
		done += 4;
		pos += 4;
		cond_resched();
	}
	*ppos = pos;
	return done;
}

static const struct file_operations bar1_fops = {
	.owner = THIS_MODULE,
	.open = simple_open,
	.read = bar1_read,
	.llseek = default_llseek,
};

/* ---- PCI ---- */

static int mxo2_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
	struct mxo2 *m;
	int ret;

	m = devm_kzalloc(&pdev->dev, sizeof(*m), GFP_KERNEL);
	if (!m)
		return -ENOMEM;
	m->pdev = pdev;
	m->latest = -1;
	m->irq = -1;
	init_waitqueue_head(&m->irq_wq);
	m->rx_mode = -1;
	m->in_table = -1;
	m->dec_mode = -1;
	m->dec_want = -1;
	m->sharpness = 2;
	m->in_pixel_rep = 1;
	mutex_init(&m->rx_lock);
	mutex_init(&m->lock);
	mutex_init(&m->cap_lock);
	mutex_init(&m->frame_lock);

	ret = pci_enable_device_mem(pdev);
	if (ret)
		return ret;
	ret = pci_request_regions(pdev, DRV_NAME);
	if (ret)
		goto err_disable;

	m->regs = pci_iomap(pdev, 0, BAR0_SIZE);
	m->mem = pci_iomap(pdev, 1, 0);
	m->mem_len = pci_resource_len(pdev, 1);
	if (!m->regs || !m->mem) {
		ret = -ENOMEM;
		goto err_unmap;
	}

	pci_set_drvdata(pdev, m);

	dev_info(&pdev->dev, "BoardStatus 0x%08x, GaiaVersion 0x%08x, SystemClock 0x%08x\n",
		 mxo2_rd(m, GAIA_BOARD_STATUS), mxo2_rd(m, GAIA_GAIA_VERSION),
		 mxo2_rd(m, GAIA_SYSTEM_CLOCK));

	m->dbg = debugfs_create_dir(pci_name(pdev), mxo2_dbg_root);
	debugfs_create_file("regs", 0444, m->dbg, m, &regs_fops);
	debugfs_create_file_size("bar0", 0600, m->dbg, m, &bar0_fops, BAR0_SIZE);
	debugfs_create_file_size("bar1", 0400, m->dbg, m, &bar1_fops, m->mem_len);
	mxo2_capture_debugfs(m);
	mxo2_rx_debugfs(m);
	mxo2_audio_debugfs(m);

	ret = mxo2_hw_init(m);
	if (ret)
		dev_warn(&pdev->dev, "hardware init failed (%d), capture unavailable\n", ret);
	ret = mxo2_v4l2_register(m);
	if (ret) {
		dev_err(&pdev->dev, "V4L2 registration failed: %d\n", ret);
		mxo2_hw_fini(m);
		debugfs_remove_recursive(m->dbg);
		goto err_unmap;
	}
	ret = mxo2_audio_register(m);
	if (ret)
		dev_warn(&pdev->dev, "ALSA registration failed (%d), no audio\n", ret);
	return 0;

err_unmap:
	if (m->regs)
		pci_iounmap(pdev, m->regs);
	if (m->mem)
		pci_iounmap(pdev, m->mem);
	pci_release_regions(pdev);
err_disable:
	pci_disable_device(pdev);
	return ret;
}

static void mxo2_remove(struct pci_dev *pdev)
{
	struct mxo2 *m = pci_get_drvdata(pdev);

	mxo2_audio_unregister(m);       /* closes audio streams */
	mxo2_v4l2_unregister(m);        /* stops a running stream */
	debugfs_remove_recursive(m->dbg);
	mxo2_capture_stop_all(m);
	mxo2_hw_fini(m);
	pci_iounmap(pdev, m->mem);
	pci_iounmap(pdev, m->regs);
	pci_release_regions(pdev);
	pci_disable_device(pdev);
}

/* System sleep: the card loses its whole setup (FPGA registers, receiver and decoder tables). */
static int mxo2_suspend(struct device *dev)
{
	mxo2_capture_suspend(dev_get_drvdata(dev));
	return 0;
}

static int mxo2_resume(struct device *dev)
{
	mxo2_capture_resume(dev_get_drvdata(dev));
	return 0;
}

static DEFINE_SIMPLE_DEV_PM_OPS(mxo2_pm_ops, mxo2_suspend, mxo2_resume);

static const struct pci_device_id mxo2_ids[] = {
	{ PCI_DEVICE_SUB(PCI_VENDOR_ID_MATROX, 0x8350, PCI_VENDOR_ID_MATROX, 0x9000) },
	{ }
};
MODULE_DEVICE_TABLE(pci, mxo2_ids);

static struct pci_driver mxo2_driver = {
	.name = DRV_NAME,
	.id_table = mxo2_ids,
	.probe = mxo2_probe,
	.remove = mxo2_remove,
	.driver.pm = pm_sleep_ptr(&mxo2_pm_ops),
};

static int __init mxo2_init(void)
{
	int ret;

	mxo2_dbg_root = debugfs_create_dir(DRV_NAME, NULL);
	ret = pci_register_driver(&mxo2_driver);
	if (ret)
		debugfs_remove_recursive(mxo2_dbg_root);
	return ret;
}

static void __exit mxo2_exit(void)
{
	pci_unregister_driver(&mxo2_driver);
	debugfs_remove_recursive(mxo2_dbg_root);
}

module_init(mxo2_init);
module_exit(mxo2_exit);

MODULE_DESCRIPTION("Matrox MXO2 (Gaia FPGA) experimental driver");
MODULE_VERSION("0.1");
MODULE_LICENSE("GPL");
