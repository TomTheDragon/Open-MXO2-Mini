/* SPDX-License-Identifier: GPL-2.0 */
/* Kernel API differences the driver builds across (tested: 6.12; guarded: 6.1 .. current). */
#ifndef MXO2_COMPAT_H
#define MXO2_COMPAT_H

#include <linux/version.h>

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 12, 0)
#include <linux/unaligned.h>
#else
#include <asm/unaligned.h>
#endif

#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 8, 0)
#define PCI_IRQ_INTX PCI_IRQ_LEGACY
#define MXO2_VB2_MIN_BUFFERS(q, n) ((q)->min_buffers_needed = (n))
#else
#define MXO2_VB2_MIN_BUFFERS(q, n) ((q)->min_queued_buffers = (n))
#endif

/* Since 6.13 vb2 takes q->lock around blocking waits itself; the helpers were removed later. */
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 13, 0)
#define MXO2_VB2_WAIT_OPS .wait_prepare = vb2_ops_wait_prepare, .wait_finish = vb2_ops_wait_finish,
#else
#define MXO2_VB2_WAIT_OPS
#endif

#endif
