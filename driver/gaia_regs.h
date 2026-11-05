/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Matrox "Gaia" FPGA register map (MXO2, PCI 102b:8350).
 * Generated from the register table in MVKAVBUS.sys (v8.0.0.15342).
 * Access: RO = read-only, WO = write-only (reads return 0), RW = read/write.
 * "init" is the default value listed in the table.
 */
#ifndef MXO2_GAIA_REGS_H
#define MXO2_GAIA_REGS_H

#define GAIA_BOARD_STATUS                    0x000 /* RO init=0x00000000 */
#define GAIA_TEST                            0x004 /* RW init=0x00000000 */
#define GAIA_GAIA_VERSION                    0x008 /* RW init=0x80000000 */
#define GAIA_GENLOCK_STATUS                  0x014 /* RO init=0x00000000 */
#define GAIA_MISC_CONTROL                    0x018 /* RW init=0x00000000 */
#define GAIA_GP_IO                           0x020 /* RW init=0x00000000 */
#define GAIA_GP_OENABLE                      0x024 /* WO init=0x00000000 */
#define GAIA_MEMORY_TARGET_CONTROL           0x028 /* RW init=0x00000000 */
#define GAIA_MEMORY_CONTROL                  0x02c /* RW init=0x00000101 */
#define GAIA_DONGLE_SW_HW_ENABLE             0x030 /* RO init=0x00000000 */
#define GAIA_AUDIO_PLL_CTL                   0x038 /* WO init=0x00000002 */
#define GAIA_VCXO_DAC_VALUE                  0x03c /* RW init=0x00000000 */
#define GAIA_FPGADEBUG                       0x040 /* RW init=0x00000000 */
#define GAIA_WATCHDOG                        0x044 /* WO init=0x00000000 */
#define GAIA_VIDEO1_CRC16                    0x060 /* RO init=0x00000000 */
#define GAIA_VIDEO2_CRC16                    0x064 /* RO init=0x00000000 */
#define GAIA_AUDIO_CRC16                     0x068 /* RO init=0x00000000 */
#define GAIA_CODEC_MISC_CTL                  0x070 /* RW init=0x80000000 */
#define GAIA_CODEC_FLASH_ADDR                0x074 /* RW init=0x80000000 */
#define GAIA_PCICOUNT                        0x078 /* RW init=0x80000000 */
#define GAIA_INTERRUPT_STATUS0               0x080 /* RO init=0x00000000 */
#define GAIA_INTERRUPT_ENABLE0               0x084 /* WO init=0x00000000 */
#define GAIA_INTERRUPT_CLEAR0                0x088 /* RW init=0x00000000 */
#define GAIA_GENLOCK_CONTROL                 0x100 /* WO init=0x00000000 */
#define GAIA_GENLOCK_PRELOAD                 0x104 /* WO init=0x00010000 */
#define GAIA_GENLOCK_FWCONTROL               0x108 /* WO init=0x00000000 */
#define GAIA_GENLOCK_SOURCE_CONTROL          0x10c /* WO init=0x00000000 */
#define GAIA_GEN_STAT_CLR                    0x110 /* WO init=0x00000000 */
#define GAIA_PLL_CONTROL                     0x150 /* WO init=0x0103fc60 */
#define GAIA_PLL_CONTROL2                    0x154 /* WO init=0x00002000 */
#define GAIA_CLOCK_COUNTER                   0x180 /* RO init=0x00000000 */
#define GAIA_CLOCK_LOAD_COUNT                0x184 /* WO init=0x00000000 */
#define GAIA_SYSTEM_CLOCK                    0x188 /* RO init=0x00000000 */
#define GAIA_SYSTEM_CLOCK_CONTROL            0x18c /* WO init=0x00000000 */
#define GAIA_SYSTEM_CLOCK_RELOAD             0x190 /* WO init=0x00000000 */
#define GAIA_AUDIO_WDMA_CONTROL              0x200 /* WO init=0x00000000 */
#define GAIA_AUDIO_WDMA_ADDRESS              0x204 /* WO init=0x00000000 */
#define GAIA_AUDIO_RDMA_CONTROL              0x280 /* WO init=0x00000000 */
#define GAIA_AUDIO_RDMA_ADDRESS              0x284 /* WO init=0x00000000 */
#define GAIA_PCI_DMA_COMMAND                 0x300 /* WO init=0x00000000 */
#define GAIA_PCI_DMA_FRAME                   0x304 /* WO init=0x00000000 */
#define GAIA_PCI_DMA_SGADDRESS_LOWER         0x308 /* WO init=0x00000000 */
#define GAIA_PCI_DMA_SGADDRESS_UPPER         0x30c /* WO init=0x00000000 */
#define GAIA_PCI_DMA_SGCOUNT                 0x310 /* WO init=0x00000000 */
#define GAIA_PCI_DMA_PITCH_RES               0x314 /* WO init=0x00000000 */
#define GAIA_PCI_DMA_COMMAND_COUNT           0x318 /* RO init=0x00000000 */
#define GAIA_PCI_DMA_FIFO_COUNT              0x31c /* RO init=0x00000000 */
#define GAIA_VIDEO_RDMA1_CONTROL             0x380 /* WO init=0x00000000 */
#define GAIA_VIDEO_RDMA1_CONTROL2            0x384 /* WO init=0x00000000 */
#define GAIA_VIDEO_RDMA1_ADDRESS             0x388 /* WO init=0x00000000 */
#define GAIA_VIDEO_RDMA1_CHROMA              0x390 /* WO init=0x02000200 */
#define GAIA_VIDEO_RDMA1_YA                  0x394 /* WO init=0x00000040 */
#define GAIA_VIDEO_RDMA1_RESOLUTION          0x398 /* WO init=0x00000000 */
#define GAIA_VIDEO_RDMA2_CONTROL             0x400 /* WO init=0x00000000 */
#define GAIA_VIDEO_RDMA2_CONTROL2            0x404 /* WO init=0x00000000 */
#define GAIA_VIDEO_RDMA2_ADDRESS             0x408 /* WO init=0x00000000 */
#define GAIA_VIDEO_RDMA2_CHROMA              0x410 /* WO init=0x02000200 */
#define GAIA_VIDEO_RDMA2_YA                  0x414 /* WO init=0x00000040 */
#define GAIA_AUDIO_INPUT_CTL                 0x480 /* WO init=0x00000001 */
#define GAIA_AUDIO_OUTPUT_CTL                0x500 /* WO init=0x00e40001 */
#define GAIA_AUDIO_OUT_MUTE                  0x510 /* WO init=0x00010000 */
#define GAIA_VIDEO_WDMA_CONTROL              0x580 /* WO init=0x00000000 */
#define GAIA_VIDEO_WDMA_CONTROL2             0x584 /* WO init=0x00000000 */
#define GAIA_VIDEO_WDMA_ADDRESS              0x588 /* WO init=0x00000000 */
#define GAIA_LQ_WDMA_CONTROL                 0x600 /* WO init=0x02000000 */
#define GAIA_LQ_WDMA_ADDRESS                 0x604 /* WO init=0x00000000 */
#define GAIA_LQ_WDMA_CRTC                    0x608 /* WO init=0x00000000 */
#define GAIA_LQ_RDMA_CONTROL                 0x610 /* WO init=0x00020000 */
#define GAIA_LQ_RDMA_CROP                    0x614 /* WO init=0x00000000 */
#define GAIA_LQ_RDMA_CRTC                    0x618 /* WO init=0x00000000 */
#define GAIA_LQ_RDMA_ADDRESS                 0x61c /* WO init=0x00000000 */
#define GAIA_LQ_RDMA_CHROMA                  0x628 /* WO init=0x00000000 */
#define GAIA_LQ_RDMA_YA                      0x62c /* WO init=0x00000000 */
#define GAIA_LQ_SCALER_HZOOM                 0x630 /* WO init=0x00010000 */
#define GAIA_LQ_SCALER_VZOOM                 0x634 /* WO init=0x00010000 */
#define GAIA_LQ_SCALER_VOFFSET               0x638 /* WO init=0x00007f79 */
#define GAIA_LQ_SCALER_COMMAND_COUNT         0x648 /* RO init=0x00000000 */
#define GAIA_LQ_SCALER_FIFO_COUNT            0x64c /* RO init=0x00000000 */
#define GAIA_LQ_SCALER_HZOOM_INV             0x658 /* WO init=0x00000000 */
#define GAIA_LQ_SCALER_VZOOM_INV             0x65c /* WO init=0x00000000 */
#define GAIA_PICOBLAZE_DONGLE                0x680 /* RW init=0x00000000 */
#define GAIA_PICOBLAZE_DONGLE_ADDRESS        0x684 /* WO init=0x00000000 */
#define GAIA_PICOBLAZE_DONGLE_DATA           0x688 /* RW init=0x00000000 */
#define GAIA_VIDEO1_VHACTIVE                 0x700 /* WO init=0x00000000 */
#define GAIA_VIDEO1_VHBORDER                 0x704 /* WO init=0x00000000 */
#define GAIA_VIDEO1_VHRESOLUTION             0x708 /* WO init=0x00000000 */
#define GAIA_VIDEO1_VHPOSITION               0x70c /* WO init=0x00000000 */
#define GAIA_VIDEO1_VHTOTAL                  0x710 /* WO init=0x00000000 */
#define GAIA_VIDEO1_VHSYNC_LENGTH            0x714 /* WO init=0x00000000 */
#define GAIA_VIN_CONTROL                     0x780 /* WO init=0x00000d00 */
#define GAIA_VIN_CONTROL_ANALOG              0x784 /* WO init=0xc0000000 */
#define GAIA_VIN_CONTROL_HDMI                0x78c /* WO init=0x80000000 */
#define GAIA_VIN_CLIPPERS_CONTROL            0x790 /* WO init=0x00000000 */
#define GAIA_VIN_STATUS_ANALOG               0x7c4 /* RO init=0x00000000 */
#define GAIA_VIN_STATUS_HDMI                 0x7cc /* RO init=0x00000000 */
#define GAIA_VOUT_CONTROL                    0x800 /* WO init=0x00001101 */
#define GAIA_VOUT_CONTROL_HDMI               0x804 /* WO init=0x00000000 */
#define GAIA_VANALOG_VOUT_CONTROL            0x810 /* WO init=0x80000000 */
#define GAIA_VANALOG_VOUT_CRTC0              0x814 /* WO init=0x00000000 */
#define GAIA_VANALOG_VOUT_CRTC1              0x818 /* WO init=0x00000000 */
#define GAIA_VANALOG_VOUT_CRTC2              0x81c /* WO init=0x00000000 */
#define GAIA_VANALOG_VOUT_CRTC3              0x820 /* WO init=0x00000000 */
#define GAIA_BRAM_LUT_RED                    0x824 /* WO init=0x20000000 */
#define GAIA_BRAM_LUT_GREEN                  0x828 /* WO init=0x00000000 */
#define GAIA_BRAM_LUT_BLUE                   0x82c /* WO init=0x00000000 */
#define GAIA_HDMIPROC_AMP_GAIN_OFFSET        0x830 /* WO init=0x04000040 */
#define GAIA_HDMIPROC_AMP_HUE_SAT_AB         0x834 /* WO init=0x00000200 */
#define GAIA_HDMIPROC_AMP_HUE_SAT_CD         0x838 /* WO init=0x02000000 */
#define GAIA_VOUT1_CLIPPERS_CONTROL          0x840 /* WO init=0x00000000 */
#define GAIA_VOUT2_CLIPPERS_CONTROL          0x844 /* WO init=0x00000000 */
#define GAIA_HDMIHSYNC_CONTROL               0x850 /* WO init=0x04000040 */
#define GAIA_HDMIVSYNC_CONTROL               0x854 /* WO init=0x04000040 */
#define GAIA_HDMIHALF_LINE                   0x858 /* WO init=0x00000040 */
#define GAIA_SRI_ADDRESS                     0x880 /* WO init=0x00000000 */
#define GAIA_SRI_CONTROL                     0x884 /* RW init=0x00000000 */
#define GAIA_SRI_DATA                        0x888 /* RW init=0x00000000 */
#define GAIA_SRI_DATA_HP                     0x88c /* RW init=0x00000000 */
#define GAIA_SRI_FIFO_COUNT                  0x890 /* RO init=0x00000000 */
#define GAIA_AUDIO_WDMA_TIME_STAMP1          0x900 /* RO init=0x00000000 */
#define GAIA_AUDIO_WDMA_TIME_STAMP2          0x904 /* RO init=0x00000000 */
#define GAIA_AUDIO_WDMA_TBCSTATUS            0x908 /* RO init=0x00000000 */
#define GAIA_VIDEO_WDMA_STATUS               0x910 /* RO init=0x00000000 */
#define GAIA_VIDEO_WDMA_TIME_STAMP1          0x914 /* RO init=0x00000000 */
#define GAIA_VIDEO_WDMA_TIME_STAMP2          0x918 /* RO init=0x00000000 */
#define GAIA_AUDIO_DEBUG_RDMA_STATUS         0x920 /* RO init=0x00000000 */
#define GAIA_AUDIO_DEBUG_WDMA_STATUS         0x924 /* RO init=0x00000000 */
#define GAIA_VIDEO1_DEBUG_RDMA_STATUS        0x928 /* RO init=0x00000000 */
#define GAIA_VIDEO2_DEBUG_RDMA_STATUS        0x92c /* RO init=0x00000000 */
#define GAIA_VIDEO_DEBUG_WDMA_STATUS         0x930 /* RO init=0x00000000 */
#define GAIA_LQRDMA_STATUS                   0x934 /* RO init=0x00000000 */
#define GAIA_LQWDMA_STATUS                   0x938 /* RO init=0x00000000 */

struct gaia_reg_desc { unsigned int off; unsigned char access; unsigned int init; const char *name; };
#define GAIA_RO 1
#define GAIA_WO 2
#define GAIA_RW 3

/* In table order: the Windows driver writes the init value of every WO/RW register in this order. */
static const struct gaia_reg_desc gaia_regs[] __maybe_unused = {
	{ 0x000, 1, 0x00000000, "BoardStatus" },
	{ 0x004, 3, 0x00000000, "Test" },
	{ 0x008, 3, 0x80000000, "GaiaVersion" },
	{ 0x014, 1, 0x00000000, "GenlockStatus" },
	{ 0x018, 3, 0x00000000, "MiscControl" },
	{ 0x020, 3, 0x00000000, "GpIO" },
	{ 0x024, 2, 0x00000000, "GpOEnable" },
	{ 0x028, 3, 0x00000000, "MemoryTargetControl" },
	{ 0x02c, 3, 0x00000101, "MemoryControl" },
	{ 0x030, 1, 0x00000000, "DongleSwHwEnable" },
	{ 0x038, 2, 0x00000002, "AudioPllCtl" },
	{ 0x03c, 3, 0x00000000, "VcxoDacValue" },
	{ 0x040, 3, 0x00000000, "FPGADebug" },
	{ 0x044, 2, 0x00000000, "Watchdog" },
	{ 0x060, 1, 0x00000000, "Video1Crc16" },
	{ 0x064, 1, 0x00000000, "Video2Crc16" },
	{ 0x068, 1, 0x00000000, "AudioCrc16" },
	{ 0x070, 3, 0x80000000, "CodecMiscCtl" },
	{ 0x074, 3, 0x80000000, "CodecFlashAddr" },
	{ 0x078, 3, 0x80000000, "PCICount" },
	{ 0x080, 1, 0x00000000, "InterruptStatus0" },
	{ 0x084, 2, 0x00000000, "InterruptEnable0" },
	{ 0x088, 3, 0x00000000, "InterruptClear0" },
	{ 0x100, 2, 0x00000000, "GenlockControl" },
	{ 0x104, 2, 0x00010000, "GenlockPreload" },
	{ 0x108, 2, 0x00000000, "GenlockFWControl" },
	{ 0x10c, 2, 0x00000000, "GenlockSourceControl" },
	{ 0x110, 2, 0x00000000, "GenStatClr" },
	{ 0x150, 2, 0x0103fc60, "PllControl" },
	{ 0x154, 2, 0x00002000, "PllControl2" },
	{ 0x180, 1, 0x00000000, "ClockCounter" },
	{ 0x184, 2, 0x00000000, "ClockLoadCount" },
	{ 0x188, 1, 0x00000000, "SystemClock" },
	{ 0x18c, 2, 0x00000000, "SystemClockControl" },
	{ 0x190, 2, 0x00000000, "SystemClockReload" },
	{ 0x200, 2, 0x00000000, "AudioWDmaControl" },
	{ 0x204, 2, 0x00000000, "AudioWDmaAddress" },
	{ 0x280, 2, 0x00000000, "AudioRDmaControl" },
	{ 0x284, 2, 0x00000000, "AudioRDmaAddress" },
	{ 0x300, 2, 0x00000000, "PciDmaCommand" },
	{ 0x304, 2, 0x00000000, "PciDmaFrame" },
	{ 0x308, 2, 0x00000000, "PciDmaSGAddressLower" },
	{ 0x30c, 2, 0x00000000, "PciDmaSGAddressUpper" },
	{ 0x310, 2, 0x00000000, "PciDmaSGCount" },
	{ 0x314, 2, 0x00000000, "PciDmaPitchRes" },
	{ 0x318, 1, 0x00000000, "PciDmaCommandCount" },
	{ 0x31c, 1, 0x00000000, "PciDmaFifoCount" },
	{ 0x380, 2, 0x00000000, "VideoRDma1Control" },
	{ 0x384, 2, 0x00000000, "VideoRDma1Control2" },
	{ 0x388, 2, 0x00000000, "VideoRDma1Address" },
	{ 0x390, 2, 0x02000200, "VideoRDma1Chroma" },
	{ 0x394, 2, 0x00000040, "VideoRDma1YA" },
	{ 0x398, 2, 0x00000000, "VideoRDma1Resolution" },
	{ 0x400, 2, 0x00000000, "VideoRDma2Control" },
	{ 0x404, 2, 0x00000000, "VideoRDma2Control2" },
	{ 0x408, 2, 0x00000000, "VideoRDma2Address" },
	{ 0x410, 2, 0x02000200, "VideoRDma2Chroma" },
	{ 0x414, 2, 0x00000040, "VideoRDma2YA" },
	{ 0x480, 2, 0x00000001, "AudioInputCtl" },
	{ 0x500, 2, 0x00e40001, "AudioOutputCtl" },
	{ 0x510, 2, 0x00010000, "AudioOutMute" },
	{ 0x580, 2, 0x00000000, "VideoWDmaControl" },
	{ 0x584, 2, 0x00000000, "VideoWDmaControl2" },
	{ 0x588, 2, 0x00000000, "VideoWDmaAddress" },
	{ 0x600, 2, 0x02000000, "LqWDmaControl" },
	{ 0x604, 2, 0x00000000, "LqWDmaAddress" },
	{ 0x608, 2, 0x00000000, "LqWDmaCrtc" },
	{ 0x610, 2, 0x00020000, "LqRDmaControl" },
	{ 0x614, 2, 0x00000000, "LqRDmaCrop" },
	{ 0x618, 2, 0x00000000, "LqRDmaCrtc" },
	{ 0x61c, 2, 0x00000000, "LqRDmaAddress" },
	{ 0x628, 2, 0x00000000, "LqRDmaChroma" },
	{ 0x62c, 2, 0x00000000, "LqRDmaYA" },
	{ 0x630, 2, 0x00010000, "LqScalerHZoom" },
	{ 0x634, 2, 0x00010000, "LqScalerVZoom" },
	{ 0x638, 2, 0x00007f79, "LqScalerVoffset" },
	{ 0x648, 1, 0x00000000, "LqScalerCommandCount" },
	{ 0x64c, 1, 0x00000000, "LqScalerFifoCount" },
	{ 0x658, 2, 0x00000000, "LqScalerHZoomInv" },
	{ 0x65c, 2, 0x00000000, "LqScalerVZoomInv" },
	{ 0x680, 3, 0x00000000, "PicoblazeDongle" },
	{ 0x684, 2, 0x00000000, "PicoblazeDongleAddress" },
	{ 0x688, 3, 0x00000000, "PicoblazeDongleData" },
	{ 0x700, 2, 0x00000000, "Video1VHActive" },
	{ 0x704, 2, 0x00000000, "Video1VHBorder" },
	{ 0x708, 2, 0x00000000, "Video1VHResolution" },
	{ 0x70c, 2, 0x00000000, "Video1VHPosition" },
	{ 0x710, 2, 0x00000000, "Video1VHTotal" },
	{ 0x714, 2, 0x00000000, "Video1VHSyncLength" },
	{ 0x780, 2, 0x00000d00, "VinControl" },
	{ 0x784, 2, 0xc0000000, "VinControlAnalog" },
	{ 0x78c, 2, 0x80000000, "VinControlHDMI" },
	{ 0x790, 2, 0x00000000, "VinClippersControl" },
	{ 0x7c4, 1, 0x00000000, "VinStatusAnalog" },
	{ 0x7cc, 1, 0x00000000, "VinStatusHDMI" },
	{ 0x800, 2, 0x00001101, "VoutControl" },
	{ 0x804, 2, 0x00000000, "VoutControlHDMI" },
	{ 0x810, 2, 0x80000000, "VAnalogVoutControl" },
	{ 0x814, 2, 0x00000000, "VAnalogVoutCRTC0" },
	{ 0x818, 2, 0x00000000, "VAnalogVoutCRTC1" },
	{ 0x81c, 2, 0x00000000, "VAnalogVoutCRTC2" },
	{ 0x820, 2, 0x00000000, "VAnalogVoutCRTC3" },
	{ 0x824, 2, 0x20000000, "BramLutRed" },
	{ 0x828, 2, 0x00000000, "BramLutGreen" },
	{ 0x82c, 2, 0x00000000, "BramLutBlue" },
	{ 0x830, 2, 0x04000040, "HDMIProcAmpGainOffset" },
	{ 0x834, 2, 0x00000200, "HDMIProcAmpHueSatAB" },
	{ 0x838, 2, 0x02000000, "HDMIProcAmpHueSatCD" },
	{ 0x840, 2, 0x00000000, "Vout1ClippersControl" },
	{ 0x844, 2, 0x00000000, "Vout2ClippersControl" },
	{ 0x850, 2, 0x04000040, "HDMIHsyncControl" },
	{ 0x854, 2, 0x04000040, "HDMIVsyncControl" },
	{ 0x858, 2, 0x00000040, "HDMIHalfLine" },
	{ 0x880, 2, 0x00000000, "SriAddress" },
	{ 0x884, 3, 0x00000000, "SriControl" },
	{ 0x888, 3, 0x00000000, "SriData" },
	{ 0x88c, 3, 0x00000000, "SriDataHp" },
	{ 0x890, 1, 0x00000000, "SriFifoCount" },
	{ 0x900, 1, 0x00000000, "AudioWDmaTimeStamp1" },
	{ 0x904, 1, 0x00000000, "AudioWDmaTimeStamp2" },
	{ 0x908, 1, 0x00000000, "AudioWDmaTBCStatus" },
	{ 0x910, 1, 0x00000000, "VideoWDmaStatus" },
	{ 0x914, 1, 0x00000000, "VideoWDmaTimeStamp1" },
	{ 0x918, 1, 0x00000000, "VideoWDmaTimeStamp2" },
	{ 0x920, 1, 0x00000000, "AudioDebugRDmaStatus" },
	{ 0x924, 1, 0x00000000, "AudioDebugWDmaStatus" },
	{ 0x928, 1, 0x00000000, "Video1DebugRDmaStatus" },
	{ 0x92c, 1, 0x00000000, "Video2DebugRDmaStatus" },
	{ 0x930, 1, 0x00000000, "VideoDebugWDmaStatus" },
	{ 0x934, 1, 0x00000000, "LQRDmaStatus" },
	{ 0x938, 1, 0x00000000, "LQWDmaStatus" },
};

#endif
