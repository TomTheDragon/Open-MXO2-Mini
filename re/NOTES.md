# MXO2 Mini MAX – Linux reverse-engineering notes

Goal: Linux driver for the Matrox MXO2 PCIe card (PCI `102b:8350`, subsystem `102b:9000`).
H.264 encoder (MAX) is out of scope.

## Source material
- `bin/` – Windows 7 x64 kernel drivers from `original/MtxUtils.exe` (v8.0.0.15342, 2015-07-16).
- The FPGA is loaded from on-board flash (the installer ships a separate
  `mvDriverFPGAUpdater.exe`), so the driver should not need to upload a bitstream.

## Driver stack (Windows)
- `MVKAVCIO.sys` – **the box I/O driver** (not H.264!): drives all box chips over I2C through SRI.
- `MVKAVBUS.sys` – PCI function driver for 8350 (and 82d0/82e0/8330/8340).
  Maps BARs (`MmMapIoSpace`), hooks the interrupt, creates child devices for:
  `MVKMISC`, `MVKMEMMNGR`, `MVKTRANSFER` (DMA), `MVKSYSTEMCLOCK`, `MVKINPUT`,
  `MVKOUTPUT`, `MVKLQSCALER`, `MVKAUDIOINPUT/OUTPUT`, `MVKAVCCODEC`/`MVKAVCIO` (H.264).
- Code is C++ with vtables; debug output is WPP tracing (no message text in the binary).

## Device detection (MVKAVBUS @0x11b50)
- Checks vendor 0x102b, subsystem 0x9000, device ID.
- 0x8330 and 0x8350 share the class constructor at `0x27d00`; 0x8350 then overrides
  some vtable slots (0x56050, 0x56418, 0x56178, 0x56f38, 0x57e98).
- Reads PCI config dword 0x68, bits 20..25 → passed to the constructor
  (probably PCIe negotiated link width from Link Status).

## FPGA register map
`MVKAVBUS.sys` contains six register-description tables
`{u32 idx; u32 offset; u32 reset; u32 flags; char *name}` (0x18 bytes each, zero-terminated), one per
FPGA design (Asterix names: Amerix, Odalix, Cathy, Gaia, Leffe, Obelix).
The 0x8330/0x8350 class references the **Gaia** table (@0x4e730).
- Full dump: `notes/MVKAVBUS.regmap.txt`, Gaia list: `notes/gaia.regs`.
- Flags (verified on hardware 2026-11-03): **1 = read-only** (status/counters),
  **2 = write-only** (most control registers; read back as 0 → driver must shadow them),
  **3 = read/write**. "reset" is probably the value the driver programs at init.
- All offsets are < 0x1000, so registers live in BAR0 (8 KiB). BAR1 (8 MiB) is probably a
  window into on-board memory (see `MemoryTargetControl`).

Key Gaia registers: `BoardStatus` 0x0, `GaiaVersion` 0x8, `InterruptStatus0` 0x80,
`InterruptEnable0` 0x84 (W), `InterruptClear0` 0x88, `SystemClock` 0x188 (free-running),
`PciDmaCommand` 0x300, `PciDmaSGAddressLower/Upper` 0x308/0x30c, `PciDmaSGCount` 0x310,
`PciDmaFifoCount` 0x31c, `VideoWDma*` 0x580.., `VinControl*` 0x780.., `VinStatusHDMI` 0x7cc,
`VoutControlHDMI` 0x804, `Sri*` 0x880.. (serial bus to on-board chips, FIFO 512).

## Hardware observations (probe 2026-11-03, `notes/probe.txt`)
- PCIe Gen1 x1 endpoint, MSI capable (1 vector, 64-bit), legacy INTA, no AER.
- BAR0 decodes 4 KiB and is mirrored at 0x1000.
- Idle values: BoardStatus=0x05011000, GaiaVersion=0xf, GenlockStatus=0x10000010,
  InterruptStatus0=0x1c010000, PciDmaFifoCount=0xf, LqScalerFifoCount=0x1f, SriFifoCount=0x200.
- BAR1 (8 MiB) contains random-looking data → uninitialised on-board DRAM window.
- `tools/join.py` lines probe values up with register names.

## Tools
- `tools/pe.py info|dis <file>` – PE info and import-annotated disassembly.
- `tools/regmap.py <file>` – extract register tables.
- `tools/probe.py` – read-only hardware probe (run as root).

## Status / next steps
1. ~~Run `tools/probe.py` on the hardware~~ done; map confirmed.
2. Find the start-up sequence in the Gaia class (constructor 0x27d00 and its vtable).
3. Skeleton Linux kernel module: bind 8350, map BARs, debugfs register access, IRQ.
4. DMA engine (`MVKTRANSFER.sys` + `PciDma*` registers), then video input → V4L2.

## Windows driver internals (from Ghidra decompile, `decomp/`)
Ghidra 12.1.4 + JDK 21 live in `~/.local/opt`. `tools/ghidra/DecompileAll.java` dumps C;
`tools/annotate.py` adds Gaia register names to `FUN(this,vt+0x20)(this, idx)` lookups
(`decomp/MVKAVBUS.gaia.c`; names are only valid inside Gaia-class methods). `tools/fn.sh` prints one function.

### Register objects
- Built by `FUN_00013590` from the table. **Every writable register (flag 2/3) is written with its
  table "reset" value at creation** → that is the first part of the init sequence.
- Object: +0x38 offset, +0x3c flags|width, +0x10 cached value. vtable +0x08 read, +0x10 write.
- `FUN_00013770` re-writes all writable registers from the cache (restore).

### Gaia class (0x8350 vtable 0x56050, base 0x8330 vtable 0x561a0)
- 8350-only: slot +0x60 `FUN_00027c30` creates the "Clara" H.264 sub-device (skip).
- `FUN_00029c60` reset: clear bit31 of Audio/Video/Lq/PciDma *Control regs (bit31 = engine
  enable), write 0x80000000 to reg 0x8 ("GaiaVersion" – more likely a reset/control reg), restore all.
- `FUN_00029f90` **StartDevice**:
  1. GPIO 0..6 configured as outputs (GpOEnable |= 1<<n), objects via `FUN_000484e4`.
  2. Big I/O object `FUN_0002b2f8` gets Vin/Vout/HDMI/Sri/Audio/Genlock/ProcAmp/LUT regs.
  3. On-board memory = 64 MiB << BoardStatus[13:12] (→ 128 MiB here); top 0x1100000 reserved.
  4. PCI DMA engine `FUN_00049064(params {2,0x10,0xf,0x100,0xff})` with PciDma* regs.
  5. LQ scaler `FUN_00045a20`; Picoblaze dongle regs; then vtable +0x100, +0x60.
- Interrupts: `FUN_0002aca8` registers InterruptStatus0/Enable0/Clear0, valid mask 0xf0ddd0b3.
- Video capture DMA class (vtable 0x53880): `FUN_00014a14` programs
  VideoWDmaAddress (= on-board address & 0x3fffffff), VideoWDmaControl2 bits 16..26 (pitch),
  VideoWDmaControl bits 20.. (8/10-bit, chroma, scan). `FUN_00014c14` maps video mode → code 0..0x13.
- Idea for first image: capture into on-board memory and read it via BAR1 (no DMA/IRQ needed).

### SRI – serial register interface (FUN_00048898 write / FUN_00048aa8 read)
Sub-object at GaiaIO+0x30 (Gaia I/O class vtable 0x572d0). 2 banks × 1024 byte registers.
- Write (FIFO mode): `SriAddress = bank<<10 | addr`; `SriData` low byte = value
  (needs free FIFO space, SriFifoCount).
- Read (FIFO mode): write `SriAddress`; poll `SriData` until bit31 (valid); bit30 = error;
  bits 25..16 must echo the address; data = bits 7..0.
- "HP" mode: `SriControl=1`, wait `SriControl` bit31 clear, same with `SriDataHp`, then `SriControl=0`.
- Only reached through virtual calls (bus interface) → used by child drivers / user mode.
- `tools/sri.py` (via `run-sri.sh`) dumps both banks from the hardware.

### Other drivers
- `mvkOnBrdIOdsxle.sys` is child ID 0x2D, which MVKAVBUS never creates → **not used by the MXO2**
  (it drives ADV7402/7403 decoders, ADV7311/7321 encoders, PCM1850, CY22150, THS7353 on other boards).
- No user-mode DLL names any chip; the box's HDMI/analog chips are probably managed by the FPGA
  (Picoblaze soft CPU) and/or reached through SRI.

### SRI dump (2026-11-03, `notes/sri-dump.txt`, after power-up, no Windows init)
- Bank 0: all 0 except addr 4 = 0xff, addr 5 = 0x04. Bank 1: every read times out
  (address echoed, valid bit never set) → nothing attached / not enabled.
- GPIO pins are a resource pool (`FUN_000484e4` creates pin objects, `FUN_00048418` hands one out by
  number); the values driven come from other components, not visible in MVKAVBUS.
  GpIO read 0x1 then 0x2 at different times with GpOEnable=0 → inputs that change (status lines?).
- Next experiment: `run-watch.sh` (`tools/watch.py`) – watch readable regs while plugging sources.

### Watch test (2026-11-03, `notes/watch.txt`): HDMI 23–33 s, composite 58–69 s
- **No register reacted to either input.** GpIO bits 0..2 flicker randomly all the time →
  floating (undriven) pins. Video2Crc16 alternates 0x2d65/0x6659 (output CRC per field → output timing runs).
- "Clara"/"Mxo2" (FUN_00027c30 → FUN_00043b38) is only a hardware-info record
  (board codename Clara, type Mxo2, revision from BoardStatus[31:16]); not a controller.
- MVKBUS.sys is another board family (Tank/Oracle/Morpheus/Trinity…), not used.
- GPIO pin object (vtable 0x58fc8 via 0x58f98): +0x18 set = GpIO |= mask, +0x20 clear, +0x28 get (cached).
  At start Windows writes GpIO=0 (table default) then GpOEnable |= 0x7f → all 7 pins driven low;
  something else raises individual pins later.
- Next experiment: `run-gpio.sh` drives pins one at a time and snapshots SRI/VinStatus.

### GPIO test (2026-11-03, `notes/gpio-test.txt`)
- Pins 4,5,6 read back what is driven → real outputs. Pins 0..2 ignore writes and keep
  flickering → inputs (status from the box). Pin 3 never reads back.
- No effect on VinStatus*/InterruptStatus0. **SRI already failed at baseline** – both banks,
  every read → engine probably stuck since the bank-1 reads of the first SRI dump.
  InterruptStatus0 changed 0x1c010000 → 0x1c090800 (bits 11 and 19 new) at some point before.
- SRI FIFO: vt+0x28 = SriFifoCount (free slots, 0x200 = empty), vt+0x30 = FIFO size (0x200).
  Reads wait until the FIFO is empty; writes need a free slot.
- `tools/sri.py` now skips bank 1 unless `--bank1`. `run-diag.sh` (`tools/sri_diag.py`) tries
  normal and HP reads and prints raw SRI registers.

### SRI diagnosis (2026-11-03, `notes/sri-diag.txt`)
- `SriControl` reads 0x80000000 all the time (bit31 = busy) – even after writing 0. HP reads therefore
  never start (Windows waits for bit31 clear). Normal reads echo the address in `SriData` but never
  set the valid bit. FIFO stays empty (0x200).
- Bank bit (bit 10 of `SriAddress`) comes from a field of the SRI object (+0x130), not from the caller.
- The SRI object (`FUN_000487f4`) only gets the five Sri* registers → no hidden enable bit there.
- We never replayed the Windows register-creation step (`FUN_00013590`: every flag-2/3 register is
  written with its table default, in table order – incl. PllControl=0x0103fc60, PllControl2,
  AudioPllCtl, VinControl and the 0x80000000 pulse on reg 0x8). Unclocked/held engine is likely.
- Next experiment: `run-init.sh` (`tools/init.py`) writes all defaults, snapshots key registers and
  retries SRI bank-0 reads → `notes/init-test.txt`. If still stuck: power-cycle the box and retry.

### Init test (2026-11-03, `notes/init-test.txt`)
- After writing all Gaia defaults SRI works again: bank 0 = 00 00 00 00 ff 04 00 …, matching the first dump.
  `SriControl` showed 0x80000000 right after the defaults, 0 later. InterruptStatus0 bit31 set by the defaults.
- The SRI bank field is 0 at creation and never changed in MVKAVBUS → **Windows only uses bank 0**.

### MVKAVCIO.sys = box chip driver (decomp/MVKAVCIO.c)
SRI bank 0 holds tiny serial masters built in the FPGA (FUN_0003041c):
| SRI ctrl/data | object | |
|---|---|---|
| 1 / 2 | `leSPICtl` (FUN_0005bdd0) | SPI: ctrl=sel, sel|1, bytes…, sel|2, 0; read = write 0xff then read |
| 3 / 4 | `I2CCtl` (FUN_0005c7d0) | I2C bus with all box chips |
| 6 / 7 | `I2CCtl2` | second I2C bus |
I2C commands (FUN_0005cf2c read, FUN_0005c99c write): 6,7 start; 5,7 stop; per read byte ctrl 3 (ack) or
7 (nack, last), data=0xff, read data. Register read = start, addr, reg, start, addr|1, bytes, stop.
No ACK check (absent device → 0xff). Chips (8-bit addr, register count): AD9398 0x98/0xb2,
AD9889B 0x72/0xcd (GMP 0x70), ADV7341/7321 0x54, SI5338 0xe0/0x100, plus 0x42, 0xa2, 0x46, 0x5c, 0x58,
0xd2, 0x8c from other constructors. Chip register names are strings in the constructors (e.g. FUN_000111a8).
- Next experiment: `run-i2c.sh` (`tools/i2c.py --scan`, read-only) → `notes/i2c-probe.txt`.

### I2C probe (2026-11-03, `notes/i2c-probe.txt`) – **I2C over SRI works from Linux**
I2CCtl devices (8-bit addr): 0x42 ADV7181/74xx decoder (r0=00 r1=c8), 0x54 ADV7341/7321 encoder (r0=12),
0x72 AD9889B HDMI TX (+0x7e = its EDID memory, 0x70 reads all 0), 0x90 "Tnc75" temperature sensor
(FUN_0005ec18, r0=0x31 → 49 °C), 0x98 AD9398 HDMI RX (r0=0x14 chip revision), 0xa2 256-byte EEPROM
("MXO2" at 0x0c). Absent: SI5338 0xe0, 0x46, 0x58, 0x5c, 0xd2, 0x8c. I2CCtl2 reads 0 everywhere → not fitted.
- EEPROM object (FUN_00040578, vtable 0x83340) copies EEPROM to obj+0xe0, so obj+X = eeprom[X-0xe0]:
  eeprom[4..5] version, [0x0c..0x1b] product name (checked for "X264io"), [0x1c..0xc5] calibration
  passed to decoder (FUN_000296e8) / encoder (FUN_0002a858) objects, [0xe4],[0xe7],[0xf1..0xf6] board rev.
- Next experiment: `run-i2c-dump.sh` (`tools/i2c_dump.py`, read-only full dumps) → `notes/i2c-dump.txt`.
  Then: AD9398 init sequence in MVKAVCIO (AD9398 class vtable 0x82550) for HDMI capture.

### Chip dump (2026-11-03, `notes/i2c-dump.txt`)
- EEPROM: product "MXO2MINIMAX*" at 0x0c, serial number at 0xd4, calibration 0x1c..0xbf, checksum? 0xfe.
- AD9398 at power-on defaults (PLL divider 0x69d). ADV7181 already looks configured. Tnc75 = 0x32 → 50 °C.
- `tools/chipregs.py` extracts every chip's register map (index → address, name) from MVKAVCIO →
  `notes/MVKAVCIO.chipregs.txt` (1088 regs, 15 chip classes; FUN_000111a8 = AD9398).
  A few entries are missed by the regex (gaps in idx); default values are not recoverable this way.

### MXO2 board init in MVKAVCIO (FUN_000458c4), board type at obj+0x5bc
Our card is **type 0x2c** (Mini MAX): ADV7181 decoder 0x42 (FUN_00025030, mode tables DAT_00072520,
DAT_00074e10, DAT_000756e0), ADV7341 encoder 0x54 (FUN_0001a824), AD9889B HDMI TX 0x72 (FUN_00015170) + packet
memory 0x70 (FUN_00019cc8), AD9398 HDMI RX 0x98 (FUN_00011094, obj slot [0x68]), PCM3052 audio codec 0x8c
(FUN_0004d04c, 2-byte mode; reads ff so far), EEPROM 0xa2, Tnc75 0x90. Buses: I2CCtl + leSPICtl.
- Input select (FUN_000493d4): analog inputs 1/2/4 → AD9398 reg 0x26 bit0 = 1 (power down) + decoder on;
  HDMI (0x40) → reg 0x26 bit0 = 0 + decoder off (FUN_0002a798(dec,0)). FPGA side via MVKAVBUS vt+0x148.
- FUN_00014ce4: AD9398 reg 0x59 bits 1,2 pulsed (set then clear) – a reset.
- Next experiment: `run-i2c-watch.sh` (read-only) – plug HDMI 20–45 s, composite 65–90 s → `notes/i2c-watch.txt`.

### Receiver watch (2026-11-03, `notes/i2c-watch.txt`) – no reaction to HDMI or composite
- No AD9398/ADV7181 status change. VinStatusAnalog toggles 0xabe/0 about once per second regardless of input.
- Reason (likely): the AD9398 was never configured. MVKAVCIO loads a full register table at chip creation.

### AD9398 mode tables (`tools/ad9398_tables.py` → `notes/AD9398.modes.txt`, `tools/ad9398_modes.py`)
- DAT_0007e410: 12 modes × 174 {index, value}; FUN_00014a5c writes the 50 RW entries in order, then
  checks HDMI MODE (0x5b) bit3 → FUN_00014c10 (0x34 bit1 by STATUS3&0x60) + reset pulse 0x59|=6 / &=~6,
  else 0x34 |= 2. Mode 0 at creation; FUN_00014b28 picks by format (480: 1/2, 576: 3/4, 1080i: 5/6,
  720p: 7/8, 1080p: 9/10/11). RO columns are captured status snapshots (VIC 6, 21, 5, 4, 33, 34 …).
- chipregs.py regex fixed (whitespace in arguments) → AD9398 now complete (178 = 0xb2 regs).
- Next experiment: `run-ad9398.sh [mode]` loads mode 0 + mode N, watches status; plug HDMI at 15 s.

### AD9398 test (2026-11-03, `notes/ad9398-test.txt`) – **HDMI receiver locks**
- Mode 0 + mode 10 loaded, readback OK. Plugging the source: STATUS1 00→67, HDMI MODE 04→0c, PKT 00→03,
  HS/VS = 0x464 (1124 → 1125-line format), VIC 0x1f = 1080p50. FPGA VinStatusHDMI 0 → 0x1fd/0x1fe.

### Capture path (MVKAVBUS)
- Video format struct: +4 width, +8 height, +0x14 rate (1/2=24, 3=25, 4/5=30, 6=50, 7/8=60; 2/5/8 = 1000/1001),
  +0x18 scan (3 = progressive). FUN_00014c14 → FPGA mode code: 1080: p24 0, psf24 1, p25 2, i50 3, p30 4,
  i60 5, p60 0xc, p50 0xd; 720p: 24 0xa, 25 9, 30 7, 50 8, 60 6; SD: 480i 0x10, 576i 0x11, 480p 0x12, 576p 0x13.
- IOCrtc (FUN_00014f54): reg A bits 12:8 = code, bit0 = !(1000/1001); reg B bits 4:0 = code (A is probably
  VinControl – its reset 0xd00 = code 0x0d). CapCrtc (FUN_000145xx): reg bits 8:4 = code, bit0 enable.
- Capture DMA (FUN_00014a14): Address = addr & 0x3fffffff; Control2[26:16] = pitch/4;
  Control = bit31 | b26 | (p5&3)<<24 | 10bit<<23 | b22:20 from p7 (1/3→6+b26, 6→4+b26, 5→0, 0x35/36→1).
- Annotations in MVKAVBUS.gaia.c are only valid in Gaia methods – FUN_0001d280/FUN_0001bb74 are other boards.
- Next experiment: `run-capture.sh [rx_mode] [code]` – fingerprints BAR1, starts the WDMA with 4 Control
  variants, saves `notes/frame-N.ppm` (UYVY assumption) when on-board memory changes.

### Capture attempt 1 (2026-11-03, `notes/capture.txt`) – **hung the machine**
- AD9398 locked (STATUS1=67), VinControl=0xd01, then the 8 MiB BAR1 fingerprint never finished; i915 timeouts,
  hrtimer 150 ms → BAR1 reads stall (probably completion timeouts) after the Gaia defaults.
  The read-only probe (before any defaults) read BAR1 fine.
- capture.py now samples BAR1 sparsely and aborts on slow reads; run-*.sh use `timeout`.
- Next experiment: `run-bar1.sh` (`tools/bar1_test.py`) times single BAR1 reads as found / after defaults /
  with MemoryControl variants.

### BAR1 timing (2026-11-03, `notes/bar1-test.txt`)
- Single BAR1 dword reads take ~4 µs both before and after the Gaia defaults → defaults are fine.
  The hang was just the volume: md5 of 8 MiB × several passes of uncached reads starves the CPU/i915.
- capture.py now samples BAR1 and saves frames as raw (dword copy with pauses, ~5–10 s);
  `tools/yuv2ppm.py` converts raw → PPM (UYVY and YUYV guesses) without root.

### Capture attempt 2 (2026-11-03, `notes/capture.txt`) – nothing written
- VinControl=0xd01, 4 WDMA Control variants: VideoWDmaStatus stays 0, no BAR1 change.

### Input enable path (found after attempt 2)
- Capture start (capture vt+0x60, FUN_00013f9c) calls I/O+8 interface slot 0 (thunk 0x2f8c8) →
  I/O+0x28 interface vt+0x148 = **FUN_0002bf54(input code)**: VinControl bits 1:0 = 3 off / 0 analog
  (1,4: VinControlAnalog bit16=0; 2: bit16=1) / **HDMI 0x40: bit0=0, bit1=1**. Then FUN_0002ba64 sets
  AudioInputCtl bit4 (audio follows video). → attempt 2 never selected an input (bit0 was set).
- Gaia capture object binding (FUN_00028b34, from asm): only VideoWDmaControl/Control2/Address +
  VideoWDmaTimeStamp1/2 + the I/O interface. No separate capture CRTC register on Gaia.
- `tools/ioannotate.py FILE SHIFT FUN…` names I/O-object fields (SHIFT = interface offset 0/8/0x28).
  IOCrtc reg A is probably VoutControl (reset 0x1101 = code 0x11 + bit0), not VinControl.
- Next: `run-capture.sh` (now VinControl=0xd02).

### Capture attempt 3 (2026-11-03) – input locks, still no write
- VinControl=0xd02 → **VinStatusHDMI = 0xa50 = 2640 = total pixels/line of 1080p50** (FPGA measures input).
- WDMA variants: VideoWDmaStatus 0, BAR1 unchanged, Int0 constant 0x9c050800.
- Capture objects are exported to child drivers through the Gaia bus (Gaia+0x230 vt+0x18/+0x20, interface
  cap+8); the call order lives in MVKINPUT. capture vt+0x88 (FUN_0001496c, "set standard") writes the mode
  code into reg cap+0x68 bits 8:4 + bit0 — binding of +0x68 for Gaia not found yet.
- Next: `run-capture-diag.sh` – BAR1 write test, interrupt bits per frame, WDMA status/timestamps.

### Capture diag (2026-11-03, `notes/capture-diag.txt`)
- BAR1 write/readback at 0x100 reads 0 after each write (later reads differ) → BAR1 is not plain RAM
  (some access window/FIFO; needs set-up – don't rely on it for frames yet).
- Int0 bit 26 toggles steadily with the input selected (per frame/field). Clear via InterruptClear0 works.
- SystemClock ≈ 75 MHz. VideoWDmaStatus/TimeStamp1/2 stay 0 for all Control variants → engine not started.
- Clue: FUN_00022a34 (suspend) clears bit31 of VinControl together with other engine-enable registers →
  next diag also tries VinControl = 0x80000d02.

### Capture diag 2 (2026-11-03) – VinControl bit31 makes no difference; engine still idle
- Only Int0 bit 27 appeared once (bit 26 = steady tick; neither is in the Windows IRQ mask 0xf0ddd0b3).
- Ruled out: Gaia IOCrtc objects (one unbound, one = VideoRDma2Control2), Video1VH* (playback timing,
  bound to VideoRDma1 in FUN_000287b4), DRAM init in MVKAVBUS (MemoryControl only read as a flag).
- Machine: Core i3-2120 → no VT-d/IOMMU, so no VFIO-passthrough VM tracing. Plan: native Windows 7
  (sda2) with Matrox drivers – dump BAR0 with RWEverything idle vs. capturing; `tools/dumpdiff.py`
  decodes/compares the readable registers. Write-only registers stay invisible this way.

### Windows dumps 1 (2026-11-04, `notes/win/*.bin`) – wrong region
- idle/cap1/cap2 are identical and are the PCI **config space** (starts 2b 10 50 83), not BAR0.
- Under Windows: **BAR0 = 0xFAFFE000** (8 KiB, registers), BAR1 = 0xFB000000 (8 MiB). (Linux assigned
  0xFB000000 / 0xFA800000 – addresses differ per OS, read them from config 0x10/0x14.)
- Official tool only offers 1080p30 for capture → FPGA mode code 4 (1080p30), fine for the diff.
- dumpdiff.py now refuses config-space dumps and prints the BAR0 address.
- Next: RWEverything Memory window at 0xFAFFE000, save 4 KiB idle / capturing / capturing again.

### Windows dumps 2 (2026-11-04, `win/*.bin`) – still not the registers
- 4 KiB each, but only byte 0 of every dword is non-zero; values are noise plus a rising byte counter
  (idle: 9d a1 a3 a6 … wraps), different in every dump. Expected BoardStatus 0x0501xxxx, GaiaVersion 0xf.
  → either wrong address or RWEverything read with 8-bit accesses (FPGA probably only answers 32-bit).
- dumpdiff.py now warns when BoardStatus/GaiaVersion don't look like Gaia.
- Next: RWEverything Memory at BAR0 (check config 0x10 that boot), switch view to 32 bit, confirm
  dword 0 = 0501xxxx and dword 8 = 0000000f, then save idle / capturing / capturing.

### Windows capture helper (2026-11-04, `windows/`)
- Manual RWEverything dumps kept failing (config space, then 8-bit reads). `windows/mxo2-capture.cmd` →
  `mxo2-capture.ps1` (PowerShell 2.0) drives `Rw.exe /Min /Nologo /Stdout /Command="R32 0x…;R32 …"`
  (128 reads per call), finds BAR0 via WMI + PCI config (`SAVE … PCI b d f`), self-tests
  BoardStatus/GaiaVersion, then guided snapshots idle/preview/recording/stopped/closed → `NN-label.bin`
  (4 KiB, little-endian) + `raw/*.txt` (RW output) + `session.txt`.
- R32 output format was not verified (no Windows here): the parser takes "address … last 0x-value" per line,
  fallback = one value line per read in order. If it fails, fix `Parse-Reads` from `raw/selftest.txt`.
- `dumpdiff.py` now accepts the dump folder directly.
- Next: user runs it on Windows 7, copies `mxo2-dumps/<stamp>/` to `notes/win/`; diff idle vs recording.

### Windows dumps 3 (2026-11-04, `notes/win/mxo2-dumps/20261104-130850/`) – **first good BAR0 dumps**
- Helper worked: self-test BoardStatus 0x05011000 / GaiaVersion 0xf. 9 snapshots idle → preview → recording →
  stopped → closed (1080p30 in the Matrox tool; VinStatusHDMI = 0xa50, VinStatusAnalog 0x35a → 0x898).
  Each dump takes ~20 s (8 RW calls), so it is not an atomic snapshot.
- Static differences Windows vs. Linux (driver loaded, even idle):
  **MemoryTargetControl 0x028 = 0x0000000d** (Linux always 0), GpIO = 1,
  **InterruptEnable0 0x084 reads back 0xd0dd5030** (table says write-only, but it is readable),
  InterruptStatus0 = 0 idle (Windows ISR clears; bits 0,1,24 stay latched once preview starts – not in the mask).
- While preview **and** recording run: **PciDmaCommandCount 0x318** and **LqScalerCommandCount 0x648** change
  (PciDmaFifoCount 0xf / LqScalerFifoCount 0x1f = free slots, dip by 1–2) → frames go to host RAM by
  PCI bus-master DMA (+ LQ scaler for the preview), not via BAR1 reads.
- VideoWDmaTimeStamp1/2 (0x914/0x918) = AudioWDmaTimeStamp1/2 and advance; VideoWDmaStatus 0/1;
  AudioWDmaTBCStatus 0x100 → 0. Video1Crc16 fixed per input (0x1e71 no input, 0x6f90 HDMI), Video2Crc16 /
  AudioCrc16 change per frame (video2 = the moving picture?).
- SystemClock under Windows advances only ~250/s (0xb354 → 0x20732 in 349 s; Linux measured 75 MHz) and the
  WDMA timestamps track it → Windows programs some clock divider / mode we don't write (unknown register).
- Write-only registers (VinControl, VideoWDmaControl, PciDma*, …) read 0, so their values stay unknown.
- Next: `run-capture-diag.sh` now repeats BAR1 write test + WDMA variants with MemoryTargetControl = 0 and 0xd,
  and prints whether InterruptEnable0 reads back. Longer term: capture needs a kernel driver doing PCI DMA
  (PciDmaSGAddress/SGCount scatter-gather into host buffers) – user space can't do that.

### Capture diag 3 (2026-11-04, `notes/capture-diag.txt`) – MemoryTargetControl switches what BAR1 shows
- **No HDMI signal this run** (AD9398 STATUS1=00, VinStatusHDMI=0) → WDMA part says nothing.
- MemoryTargetControl reads back what was written (0xd). With 0: BAR1 = 80ac00ad 7eab82a9 … (noise-like,
  as before). With 0xd: 0x0/0x100000/0x400000 all read 0a09082c (aliasing → small target, not 8 MiB RAM),
  0x7ffffc = 67e0fa8b. Write/readback at 0x100 still 0 in both.
- InterruptEnable0 read 0 (never written on Linux, so readability still unproven).
- Code: Gaia FUN_00028d58 → FUN_000243dc builds an object with CodecMiscCtl/CodecFlashAddr/
  MemoryTargetControl/MiscControl and a 0x1100000 (17 MiB) region at (memory top − 17 MiB); it writes
  CodecFlashAddr = 0, (base>>20 & 0x3ff)|0x40000, then |0x80040000. Writer of MemoryTargetControl not found yet.
- Next: `run-mtc-scan.sh` (`tools/mtc_scan.py`) – read-only BAR1 samples for MemoryTargetControl 0..0x1f
  (+0x100, 0x10000, bit31), marks live (changing) words. HDMI source on for the next capture diag.

### Capture diag 4 (2026-11-04, with HDMI on) – MemoryTargetControl=0xd does not start the WDMA
- STATUS1=67, VinStatusHDMI=0xa50; all WDMA variants with MTC 0 and 0xd: status/timestamps stay 0.
- BAR1 samples identical to the no-signal run (both MTC values) → BAR1 doesn't show live video.
- (`run-mtc-scan.sh` not run yet – low priority now.)

### Plan: read the Windows driver's register shadows (all write-only values) with a kernel debugger
- Register object (pool tag 'MTX', 0x48 bytes, FUN_00013ae8): +0 vtable (RVA 0x43640 RW / 0x43778 WO /
  0x43758 RO), +8 owning bank, **+0x10 last written value**, +0x14 name (each char + 0x21, ≤0x20 bytes),
  +0x38 BAR0 offset, +0x3c flags|width, +0x40 pending value. Write FUN_00013a14 updates +0x10/+0x40.
- Bank = the Gaia object itself (vt+0x20 = FUN_00013920): +8 pointer array, +0x10 capacity, +0x14 count.
- 0x8350 Gaia object: ExAllocatePoolWithTag(0x29a0, 'MTX') in FUN_00011a34, vtable RVA 0x46050
  (Ghidra 0x56050, image base 0x10000). Pointer kept in BusDev object (0x858 bytes) at +0x850.
- With WinDbg/LiveKd during capture: find object whose qword 0 == MVKAVBUS+0x46050, walk +8 array,
  print offset/+0x10 for each → exact VinControl, VideoWDma*, PciDma*, clock … values.
- Built: `windows/mxo2-regs.cmd` + `mxo2-regs.wds` (kd -kl, symbols from msdl): FDO device extension
  → bus device object (vtable RVA 0x43420, FUN_00039640 stores `this` in the 8-byte extension) → +0x850
  Gaia object (vtable check) → prints "REG idx off flags value pending" for the whole array. Steps
  idle/preview/recording/stopped → `regs/*.txt`. Linux side: `tools/regshadow.py notes/win/regs`.
- Not verifiable here (no Windows): WinDbg script syntax, !drvobj output parsing, kd path.
- Next: user installs Debugging Tools (Win 8.1 SDK), runs mxo2-regs.cmd, copies `regs/` to notes/win/.

### mxo2-regs run 1 (2026-11-04, `notes/win/mxo2-regs_console-log.txt`) – failed before reading anything
- All 4 steps: `Couldn't resolve error at 'MVKAVBUS;…'` → `r $t0 = MVKAVBUS` could not resolve the module
  name; `$$><` makes the script one line, so this aborted everything. Debugger used: old
  "Debugging Tools for Windows (x64)" (WDK 7.1 era). The per-step `regs\*.txt` logs were not copied back.
- Suspect: nt symbols not downloaded (Win7 WinHTTP vs. HTTPS-only msdl) → no module list. Unconfirmed.
- Fix: new `windows/mxo2-diag.wds` (run with `$$<`, line by line: vertarget, .sympath, !sym noisy, .reload,
  x nt!PsLoadedModuleList, lm) before the dump; dump now takes the base from `lm m MVKAVBUS` output and
  prints "RESULT: MVKAVBUS module not found" instead of aborting; .cmd stops after a failed step.
- Next: user reruns mxo2-regs.cmd (copy mxo2-diag.wds too) and brings back the `regs\` folder.

### mxo2-regs run 2 (2026-11-04, `notes/win/{1-idle,2-preview,3-recording,4-stopped}.txt`) – **all write-only values read**
- Debugger script worked: nt symbols OK, module is `mvkAVBus`, Gaia object found, 131/131 registers per step.
  Decoded table: `notes/win-regshadow.txt` (`tools/regshadow.py notes/win`). Source 1080p30 HDMI.
- **Data path** (all addresses below are on-board DDR, not host):
  HDMI in → VideoWDma writes 1920×1080 YUV422 frames (0x3f4800 B) into a ring at k·0x3f4800
  (seen 0, 0x3f4800, 0x7e9000, 0xbdd800, 0x1baf800 = k 0,1,2,3,7 → probably 8 buffers) →
  LQ scaler (LqRDma reads the HQ frame, LqWDma writes 960×540, 0xfd200 B, ring at 0x2008000 + k·0xfd200) →
  PciDma copies one on-board frame (PciDmaFrame) to host via a scatter-gather list (PciDmaSGAddress = host phys).
  Audio WDMA/RDMA buffers at 0x1fb0800..0x1ffb800. On-board RAM is therefore > 32 MiB.
- Values (preview/recording; idle in brackets):
  VinControl 0xd02 [0xd00] · VideoWDmaControl 0x82000000 [0] · VideoWDmaControl2 0x41 [0x101] ·
  VideoWDmaAddress = current ring slot · GenlockControl 0x401 [0x1000] · GenlockFWControl 0xd180d1 ·
  SystemClockControl 1, SystemClockReload 0x184e8 [0xf0], ClockLoadCount 0x4b87f [0x37176]
  (explains the slow SystemClock on Windows) · InterruptEnable0 0xf0dd5030 · GpOEnable 0x7f ·
  MemoryTargetControl 0xd · CodecFlashAddr 0x8004006f.
  PciDmaCommand 0x90000000 · PciDmaPitchRes 0x3c0 (full res, = 3840 B / 4 → pitch in dwords) or 0x1e0 (LQ) ·
  PciDmaSGCount 0xbdf (full) / 0x2f7 (LQ) = 3 × page count (1013 / 253 pages) → SG entry = 3 dwords?
  LqWDmaControl 0x8a0003c0 (pitch in low bits) · LqWDmaCrtc 0x04380000/0x021c0000 (lines<<16) ·
  LqRDmaControl 0x890203c0 · LqRDmaCrop 0x780 · LqRDmaCrtc 0x04380780 · LqScalerH/VZoom 0x7fef/0x8000 (½) ·
  LqScalerHZoomInv 0x100220.
  Output side also configured (VoutControl 0x1303, VAnalogVoutCRTC*, HDMIHsync/Vsync, BramLut*, VideoRDma2).
- Shadows are "last written", so the ring/address registers are a snapshot of one moment.
- Next: (1) Linux test: program the input/WDMA/genlock/clock values above and check whether VideoWDmaStatus /
  timestamps start (no host DMA needed). (2) RE the SG entry format from MVKAVBUS.gaia.c (~l.7413, 8123,
  11912, 16118) and PciDmaCommand bits. (3) Host DMA test from user space: locked buffer + /proc/self/pagemap
  phys addresses + bus mastering (needs IOMMU off) – or a small kernel module.
- Built step (1): `run-wdma-replay.sh [ad9398_mode] [shadow_log]` → `tools/wdma_replay.py` → `notes/wdma-replay.txt`.
  Replays the 2-preview shadow values in stages (0 baseline, 1 system/genlock/clock, 2 input + WDMA,
  3 ring stepping of VideoWDmaAddress per frame, 4 ring + Control rewrite), 1 s watch each: prints
  "LIVE: …" if WDmaStatus/TS1/TS2/Video2Crc16 move, plus SystemClock rate (Windows ≈ slow).
  InterruptEnable0 deliberately not written (no IRQ handler on Linux).

### WDMA replay (2026-11-04, `notes/wdma-replay.txt`) – **video write DMA runs on Linux**
- HDMI locked (STATUS1=67). Stage 1 (system values) drops SystemClock from 74 MHz to ~240/s, like Windows.
- Stage 2 (VinControl 0xd02, WDmaControl2 0x41, Address 0x3f4800, Control 0x82000000): TS1 set **once**
  (0x185fd) → one frame captured, then it stops. Stage 3 (address stepping only): no new frames.
  Stage 4 (address + **rewrite Control 0x82000000 each frame**): 31 TS1/TS2 values in 1 s ≈ 30 fps,
  WDmaStatus 0..2. → **each Control write arms the capture of one frame**; the driver re-arms per frame.
- TS1/TS2 = SystemClock value at frame time (734 ticks across stages 2–4 ≈ 3 s × 240/s).
- InterruptStatus0: base 0x04000000, after system 0x14010000, after input 0x15800000 (bit 24 latched, as
  on Windows). Register 0x064 stayed 0x6f90 (no per-frame change here). VinStatusHDMI reads sometimes 0
  (and once 0xfff) between 0xa50 samples – read glitch or status bits toggling, unclear.
- Still unproven: that pixels land in on-board RAM at the given address.
- Next: get the pixels. Cheapest: sample BAR1 with several MemoryTargetControl values *while* frames are
  armed (look for words that change per frame). Otherwise: PCI DMA to host (SG format from
  MVKAVBUS.gaia.c, PciDmaCommand 0x90000000, PciDmaFrame = on-board frame address).
- Built: `run-bar1-live-scan.sh` → `tools/bar1_live_scan.py` → `notes/bar1-live-scan.txt` + `notes/bar1-frames/`.
  Capture as in replay stages 0–2, thread re-arms WDMA (address 0) at 30 Hz; per MemoryTargetControl value
  (0..0x1f, 0x100, 0x10000, bit31, 0x8000000d) counts BAR1 words (1 per 4 KiB) changing with capture on
  ("live") vs paused ("noise"), checks frames still arrive. Saves 0x3f4800 B (32-bit reads) for the 4 best
  + MTC 0/0xd → view with `tools/yuv2ppm.py`. Needs a moving picture.

### BAR1 live scan (2026-11-04, `notes/bar1-live-scan.txt`, `notes/bar1-frames/`) – **first captured frame on Linux**
- **MemoryTargetControl bits 0–3 select an 8 MiB window of on-board RAM for BAR1** (bit 4+ ignored,
  readback masks to 4 bits → 16 × 8 MiB = 128 MiB DDR). Window 0 with capture on: 1010 of 2048 sampled
  pages change per frame = one 0x3f4800 frame at on-board 0 (1013 pages); noise 0. Capture keeps running
  under every MTC value. Windows 6–0xc are filled with YUV black (0x80108010); 0xd = what Windows leaves.
- `mtc-00000000.raw` decodes to the HDMI camera picture (MXO2 box with cables):
  1920×1080, pitch 3840, byte order **Y0 U Y1 V** (YUYV). Luma clean. **Chroma bytes are mangled:**
  best smoothness score with value = rotl1(byte) ^ 0x80, i.e. stored bit 7 = real LSB and bit 6..0 =
  real bit 7..1 inverted at the top (see `tools/yuv2ppm.py` "mxo2" output). Decoded image
  `bar1-frames/mtc-00000000-decoded.png` still has a blue cast + green blotches in saturated reds and
  sparkle on flat areas → chroma bit mapping not fully right; probably an input-format setting
  (AD9398 output mode / VinControlHDMI / VinControl bits) rather than something to fix in software.
- BAR1 read of a full frame: 3.2 s from Python (32-bit reads) → fine for tests, far too slow for video.
- Next: (a) fix chroma at the source: compare AD9398 regs Windows vs. our mode 10 (output format /
  CSC registers) and try VinControl variants; (b) for real capture speed, the PCI DMA path.

### Colour fix, step 1 (2026-11-04)
- Datasheet not reachable (analog.com times out, mirrors 403) → working from the driver code.
- AD9398 methods in MVKAVCIO only touch 0x34, 0x59, 0x26 and read 0x5b/0x81 – CSC/output registers stay as
  in the mode table, same as ours. But FUN_00014c10 (CSC choice: 0x81 STATUS3 bits 6:5 = AVI colour space;
  RGB → 0x34 |= 2, else &= ~2) + FUN_00014ce4 (0x59 bits 1,2 pulse) are re-run by a monitor loop
  (MVKAVCIO ~l.30940, state +0x4bc); without HDMI it calls FUN_00014c90 (0x34 |= 2).
  ad9398-test showed STATUS3 = 0x20 → camera sends **YCbCr 4:2:2**, so bit1 must be 0. Our load_mode()
  decides once at load time – if the InfoFrame wasn't there yet, the CSC treats YCbCr as RGB.
- Built: `run-csc-test.sh` → `tools/csc_test.py` → `notes/csc-test.txt` + `notes/csc-frames/*.raw`:
  variants as-loaded / driver-logic / 0x34 bit1 on / off, prints AD9398 regs + chroma smoothness score
  (old dump: plain 17.6, rotl1^80 7.2; correct colours should give a low *plain* score).

### Colour fix, step 2 (2026-11-04, `notes/csc-test.txt`; datasheet now in `original/AD9398.PDF`,
text in `notes/datasheet/AD9398.txt`)
- STATUS3 was already 0x20 at load time → 0x34 bit1 (= **CSC_Enable**, datasheet) correctly 0. Driver logic /
  forcing off: chroma score plain ≈29, fixed ≈9 (no change). CSC on: 14.7/14.8, brighter. → not the cause.
- Datasheet: 0x25 = 0x4e → 1× clock, drive 00, **Output Mode 11 = 12-bit 4:2:2 (HDMI only)**, primary on.
  Table 10: 12-bit 4:2:2 puts CbCr[11:0] on R[7:0]+G[7:4] and Y[11:0] on G[3:0]+B[7:0].
  0x34 bit3 = up-conversion interpolate, bit2 = CrCb FIR. CSC: ROUT = A1·R+A2·G+A3·B+A4 etc., 13-bit 2's compl.
  Same receiver setup as Windows → the mangling is on the FPGA side (or Windows' memory format is the same
  and something downstream converts – VideoRDma/LqRDma "Chroma"/"YA" registers 0x8200/0x8040 suggest
  10-bit offsets 512/64 + a bit-15 flag).
- Per-bit flip rates (as-loaded frame): luma lanes behave like normal 8-bit (b7 2.7 % … b1 43 %).
  Chroma: b6 < b5 < b3 < b4 < b2 < **b7** < b1 < b0 (lane 3: 2.6, 4.0, 12.0, 17.3, 18.1, 22.6, 28.9, 44.0 %)
  → not a plain rotation; rotl1^0x80 was only an approximation.
- Built: `run-csc-sweep.sh` → `tools/csc_sweep.py` → `notes/csc-sweep.txt`: CSC with zero multipliers as a
  constant-colour generator (A4=Cr, B4=Y, C4=Cb, 12-bit), sweeps 0, 1<<k, 0xfff per channel, reads 256 words
  mid-frame → which stored bits each receiver bit drives. Restores the mode table afterwards.

### CSC sweep (2026-11-04, `notes/csc-sweep.txt`) – **exact chroma bit map**
- Memory word lanes: b0 = Y0, b1 = Cb, b2 = Y1, b3 = Cr (YUYV). Each byte = component[11:4].
- Luma perfect (Y[11:4] → byte, 256/256 identical words).
- Cr byte: bits 6..1 = Cr[10:5] ✔, bit0 = Cr[4] ✔, **bit7 = Cb[11]** (Cr[11] never appears).
  Cb byte: bits 7..1 = Cb[11:5] ✔, **bit0 = Cr[4]**. Cb[4] never appears.
  Chroma words only 137–215/256 identical (luma 256/256) → noisy sampling.
- CbCr[11] = R7 and CbCr[4] = R0 (datasheet Table 10, 12-bit 4:2:2) = outer wires of the chroma port →
  those two wires are latched at the wrong Cb/Cr phase: a timing problem, not a format setting.
  Explains why rotl1^0x80 "worked": Cr[11] lost, ~Cr[10] approximates it near 0x800.
- Same AD9398 setup as Windows (modes 9/10/11 identical RW values), so Windows must either differ in an
  FPGA input-timing setting we don't know, or have the same error.
- Built: `run-csc-timing.sh` → `tools/csc_timing.py` → `notes/csc-timing.txt`: 16 combos of 0x24 bit0 (clock
  invert) × 0x25[7:6] (1× / 1× 90°) × 0x25[5:4] (drive 0–3), 8 known-colour patterns each, reports exact
  matches + wrong bits + stability; then saves a camera frame with the best combo (`csc-frames/timing-best.raw`).

### Timing sweep (2026-11-04, `notes/csc-timing.txt`) – not a receiver timing problem
- All 16 combos (0x24 bit0 × 0x25 clock 1×/90° × drive 0–3): at most 1/8 colours exact, chroma 37–67 %
  stable; Cr byte bit7 wrong in every combo; drive strength shifts *which* bits are wrong (6e/7e: Cb bit0 ok,
  Cb bit7 + Cr bit4 wrong). 90° clock breaks all chroma bits. Camera frame with f1/ee: plain 36.2, fixed 8.4.
- **New suspect: bus contention with the ADV7181 analog decoder (0x42).** Dump: PwrManagement 0x0f = 00,
  output control 0x03 = 0c (TOD bit6 = 0 → outputs driven). Windows, when HDMI is selected
  (FUN_000493d4 → FUN_0002a798(dec,0)), writes decoder idx 0xd = reg 0x0f |= 0x22 (bit5 PWRDN). We never did.
  Two drivers on shared chroma wires fits: luma fine, chroma bits wrong + noisy, result depends on drive.
- Built: `run-decoder-off.sh` → `tools/decoder_off.py` → `notes/decoder-off.txt`: known-colour check before/after
  0x0f |= 0x22, then camera frame `csc-frames/decoder-off.raw`. Leaves the decoder powered down.
  (`csc_timing.measure()` is now a module function.)

### Decoder-off test (2026-11-04, `notes/decoder-off.txt`) – not contention
- ADV7181 0x0f was **already 0x22** (powered down) before the script wrote it (the power-up dump showed 00;
  something since – Windows boot? – left it down). 1/8 exact before and after; camera frame plain 29.0 / fixed 8.8.
- Remaining facts: wrong chroma bits are value-dependent and noisy only when Cr ≠ 0x800 (Cr sweep 142–215/256
  identical, Cb sweep with Cr=0x800 251–256/256, Y always 256/256). Cb=0xfff gave Cr byte 0x83 – not a
  pure wire mix-up.
- Open question: are Windows' colours correct with this camera? Unknown – no Windows frame was ever saved.
  If yes: get MVKAVCIO's chip register shadows (AD9398, ADV7181) with the kd method, like the Gaia ones
  (FUN_0002a798 uses the cached value at register object +0x10 too) and diff against ours.
- User (2026-11-04): Windows colours were also off with this HDMI camera → not a missing Linux setting.
  Camera sends YCbCr 4:2:2 (STATUS3 0x20). Even CSC-generated constant colours come out wrong with this
  input, so the AD9398 output path seems to depend on the input format (4:2:2 in → 12-bit 4:2:2 out).
- Next: same test with an **RGB** HDMI source (PC/laptop, 1920×1080 @ 50 Hz to match VinControl 0xd02 /
  mode code 0xd): `sudo sh run-decoder-off.sh` → STATUS3 should read 00, check 8/8 exact + plain score.
  If RGB is clean: the problem is 4:2:2 input handling → sweep 0x34 bits 3 (up-conversion) / 2 (CrCb FIR)
  and CSC identity for YCbCr input; else it's the FPGA side.

### Laptop test (2026-11-04, `notes/decoder-off.txt` overwritten, 720p50 RGB) – **capture path is fine with RGB**
- STATUS3 = 0x1e → AVI colour space 00 = RGB. Scripts are 1080p-only (mode 10, VinControl 0xd02, sample at line 540
  of a 3840-byte pitch), so the colour check read junk (0/8) – but the FPGA still wrote a proper **1280×720 frame,
  pitch 2560** (luma row similarity) despite the 1080p mode code.
- `csc-frames/laptop-720p-plain.png` (`tools/raw2png.py`, new: YUYV → PNG, plain|mxo2): **correct colours with
  plain YUYV decode**, no artefacts. → Only YCbCr 4:2:2 input (the camera) is broken – on Windows too (user).
- Datasheet: **0x50 "Test – must be written to 0x20 for proper operation"**, chip powers up with 0x80
  (i2c-dump), Matrox table never writes it; 0x56 must be 0x0f (is). 0x34 bit3 up-conversion interpolate,
  bit2 CrCb FIR.
- Built: `run-rx-fix.sh` → `tools/rx_fix_test.py` → `notes/rx-fix.txt` + `csc-frames/fix-*.raw`: variants base / r50 /
  upconv / filter / r50+upconv / r50+upconv+filter, known-colour check + camera frame score each.

### Receiver fix test (2026-11-04, `notes/rx-fix.txt`) – no register fix
- 0x50=0x20, up-conversion, CrCb filter and combinations: all 1/8 colours, chroma ~55–60 % stable, plain score 20–31.
- **New explanation: pixel clock.** Camera = 1080p50 = 148.5 MHz (AD9398 max 150 MHz; Matrox tool only offered up
  to 1080p30 for capture). Laptop = 720p50 = 74.25 MHz → clean. Chroma bus toggles Cb/Cr every clock (luma mostly
  constant in the patterns) → marginal timing shows on chroma only, is noisy, depends on drive strength, and
  Windows has it too. Likely 1080p50/60 is simply beyond what this box captures reliably.
- Colour check sample position (`csc_sweep.MID` → line 300) and chroma score rows (`csc_test.chroma_score`, first
  1.8 MB) now valid for 720p and 1080p.
- Next: camera at a slower format (1080p25/30, 1080i50 or 720p50) → `sudo sh run-decoder-off.sh`.
  If 8/8 + low plain score: done – 1080p50 is out of spec, not a register problem.

### Camera at 1080p30 (2026-11-04, `notes/decoder-off.txt`) – not the clock; **software decode for 4:2:2 input**
- 1080p30 (74.25 MHz, same clock as the clean laptop 720p50): still 1/8 colours, plain score 21.4 → clock theory wrong.
  Only difference left: input colour format (camera YCbCr 4:2:2, STATUS3 0x20; laptop RGB 0x1e).
- The CSC colour-generator check is not trustworthy with 4:2:2 input: its "noise" is most likely camera data leaking
  into the chroma bits (AD9398 4:2:2-in path does not fully go through the CSC).
- Decode of the real camera frame: stored chroma byte = [junk, ~C7, C6..C1] → **C = ((b << 1) | 1) ^ 0x80**
  (smoothness 7.31 = same as rotl1^0x80 7.34 → stored bit 7 carries nothing; 7-bit chroma).
  `csc-frames/camera-1080p30-mxo2.png`: natural colours (wood desk, green logo, red/blue/green jack rings),
  slight sparkle on bright saturated areas. `tools/raw2png.py … mxo2` uses this decode.
- So: RGB sources → plain YUYV; YCbCr 4:2:2 sources → chroma LUT above. A driver can pick by AD9398 0x81 bits 6:5.
  Root cause inside the AD9398 4:2:2 input path not found (Windows shows the same fault).
- Next: PCI DMA to host memory (SG list format, PciDmaCommand 0x90000000) for real-time capture.

### PCI DMA format (2026-11-04, from code)
- Gaia PCI DMA engine = "GTfrCtl" object at Gaia+0x1908 (ctor FUN_0003f664, base FUN_00048e38/FUN_00049064 command
  FIFO with params {2,0x10,0xf,0x100,0xff}); exported to child drivers as Gaia interface #10 (GUID DAT_00052cf8).
  Register pointers: +0x120 Command, +0x128 SGAddressLower, +0x130 Frame, +0x138 SGAddressUpper, +0x140 SGCount,
  +0x148 PitchRes.
- **Submit FUN_0003f800**(iface, onboard, pitch, width, dir, …, sg_phys, sg_count, flag):
  Frame = onboard & 0x3fffffff (32-byte aligned); SGAddressLower/Upper = sg_phys; SGCount = sg_count & 0x1fff;
  PitchRes = (pitch & 0x1fff) << 16 | (width & 0x7ff) (pitch multiple of 4); Command = (pitch ? 0x40000000) |
  (dir == 1 ? 0x20000000 : 0, dir must be 1 or 2) | (flag ? 0x10000000); written, then again | 0x80000000 (go).
  Windows capture: Command 0x90000000 (dir 2 = board → host, flag set), PitchRes 0x3c0 / 0x1e0.
- **SG list (MVKTRANSFER "GenSGMgr", FUN_00019fe0)**: 48 buffers × 0x7ff8 bytes; entry = 12 packed bytes
  {u64 phys addr, u32 length}; length < 0x20000 (longer runs split into 0x1f000 pieces, touching entries merged
  while < 0x20000). IoGetDmaAdapter MaximumLength = buffer*2/3/8 pages = buffer/12 entries.
  SGCount = list size in dwords (Windows 0xbdf = 3 × 1013 pages of a 1080p frame).
- This machine: no IOMMU (/sys/class/iommu empty) → phys = bus address. Huge pages: 0 reserved by default.
- Built: `run-pcidma.sh` (reserves 3 huge pages) → `tools/pcidma_test.py` → `notes/pcidma.txt` + `notes/pcidma-frame.raw`:
  frame into on-board 0, ground truth via BAR1 (64 KiB + 1 word / 4 KiB), SG list in a huge page (≈34 entries
  of ≤ 0x1f000 over two 2 MiB pages), padding entries → trash area in our own page, bus master on, DMA,
  compare, then time 10 DMAs.

### PCI DMA test (2026-11-04, `notes/pcidma.txt`) – **frame DMA to host RAM works**
- 34 SG entries (SGCount 0x66 dwords) in a huge page at phys 0xe600000, frame in two huge pages. Bus master on
  (PCI cmd 0102 → 0106). One DMA of a 1080p frame: **byte-exact** vs BAR1 (first 64 KiB 16384/16384, 1013/1013
  samples), nothing left unwritten, trash area untouched (→ SGCount = dwords confirmed), no dmesg noise.
- Command 0x10000000 then 0x90000000; PciDmaCommandCount 0 → 1 per DMA, FifoCount stays 0xf (free slots).
  **InterruptStatus0 bit 20 (0x00100000) set on completion** – candidate DMA-done interrupt (in Windows' enable
  mask 0xf0dd5030).
- Speed: 20.8 ms per 4 MB frame ≈ **199 MB/s, max ~48 fps** at 1080p YUYV (PCIe x1 gen1) → 1080p30 / 25 fine.
  `notes/pcidma-frame.png` (mxo2 chroma decode) = camera picture.
- Next: kernel driver (driver/mxo2.c): coherent/SG buffers via dma_map, WDMA re-arm + PCI DMA per frame, IRQ bit 20
  (and the per-frame input bit) instead of polling, chroma fix for 4:2:2 sources, then V4L2.

### Kernel driver stage 2 (2026-11-04, `driver/`) – capture with PCI DMA, polling
- Split: `mxo2.h` (shared struct), `mxo2_main.c` (old mxo2.c: probe, debugfs regs/bar0/bar1), `mxo2_capture.c`.
  `gaia_regs.h` table now carries the init values (131 regs, table order).
- Start (debugfs `capture` ← "start"): 2 host buffers of 1013 single pages (GFP_DMA32, dma_map_page, vmap) +
  coherent SG list each (entries merged ≤ 0x1f000); Gaia table defaults + Windows preview values (genlock, clock,
  Vin*, WDMA Control2 0x41, MTC 0); bus master on; kthread "mxo2-cap".
- Thread: poll VideoWDmaTimeStamp1 (0.5–1 ms sleeps, 200 ms timeout → re-arm); at change n arm on-board slot n%3,
  PCI-DMA slot (n-2)%3 into the non-latest host buffer (wait for PciDmaCommandCount change, 100 ms timeout), then
  publish it. 3 slots of 0x3f4800 at on-board 0.
- debugfs `status` (counters, fps, DMA µs, TS1/TS2/Int0) and `frame` (open = snapshot of newest frame, raw YUYV).
- AD9398 still from user space: `re/tools/rx_setup.py`. Test: `re/run-driver-capture.sh` → `notes/driver-capture.txt`
  + `notes/driver-frame.raw`. Builds clean with W=1.
- Next: IRQ (Int0 bit 20 = DMA done; find the per-frame bit) instead of polling, I2C/AD9398 in kernel, V4L2.

### Driver capture test (2026-11-04, `notes/driver-capture.txt`) – **kernel capture works**
- 466 frames copied in 10 s (≈46 fps), 0 TS / DMA timeouts, DMA 21–22 ms per frame, 473 SG entries per buffer
  (page runs merged), clean stop + rmmod, no kernel warnings. `notes/driver-frame.png` = camera picture.
- Camera was at 1080p50 again: the loop DMAs synchronously (22 ms) so on-board frames also run at ~46/s.
  1080p50 YUYV = 207 MB/s > measured PCI DMA ≈ 199 MB/s → full-rate 1080p50 is not possible on this link
  (Matrox's tool also stopped at 1080p30). 1080p25/30 and 720p50/60 fit.
- TS2 = TS1 − 4 ticks during capture (TS1 = frame end, TS2 = start is plausible).

### Interrupts (2026-11-04) – from code
- Windows: IoConnectInterrupt (line-based INTx, no MSI); ISR FUN_00049b24 → callback → DPC.
- Gaia interrupt object FUN_000473a4(status 0x80, enable 0x84, clear 0x88, mask 0xf0ddd0b3): enable is set/cleared
  per event bit (FUN_0004817c, within the mask); on start InterruptClear0 is pulsed 0xffffffff → 0.
  ISR body (status & enable → clear) not readable in the decompile.
- Built: `run-int-probe.sh` → `tools/int_probe.py` → `notes/int-probe.txt`: lspci -vv (MSI? pin), per-bit counts
  idle / during per-frame capture (vs frame count) / after one PCI DMA, single-bit clear test.

### Interrupt probe (2026-11-04, `notes/int-probe.txt`)
- lspci: **MSI capable** (1 vector, 64-bit); INTx pin A → IRQ 16 (shared with ehci usb1). Link 2.5 GT/s x1.
- InterruptStatus0 (latches with enable 0): **bit 23 = input frame boundary** (59/s idle and capturing, camera now
  1080p60), **bit 20 = PCI DMA done**, bit 28 = 240 Hz system clock tick (4 per frame), bit 16/26 rare, bit 24 always
  set and not clearable (not in Windows mask). No separate "WDMA done" bit.
- Clear: write the bit to InterruptClear0, then 0 – single bits clear independently.
- Driver: MSI preferred (else shared INTx), enable bits 23|20; hard IRQ clears + records events + wakes the capture
  thread; thread waits for vsync (then polls TS1 ≤ 2 ms) and for DMA done. `use_irq=0` keeps the polling mode.
  status shows irq type + count. `run-driver-capture.sh` now runs 10 s IRQ + 6 s polling (CPU via ps).

### Driver IRQ test 1 (2026-11-04, `notes/driver-capture.txt`) – MSI set up, **no interrupt delivered**
- MSI vector 33, InterruptEnable0 = 0x00900000: 0 interrupts in 10 s, status bits 20/23 latched (int0 0x15910000).
  Capture limped on via the 100/200 ms timeouts (9 fps, DMA "104 ms"). Polling run: 46 fps, 1.3 % CPU.
- Suspect a master enable among bits 28–31 (Windows writes 0xf0dd5030), or Gaia MSI unusable (Windows uses INTx).
- Driver: `use_irq` 0 poll / 1 MSI / 2 INTx, `int_enable` param + debugfs `int_enable` (hex) knob, handler acks
  every enabled pending bit (except sticky bit 24) and records `bits seen`.
- Built: `run-irq-test.sh` → `notes/irq-test.txt`: MSI with enable 0x00900000, 0x80…, 0x40…, 0x20…, 0x10…,
  0xf0900000, 0xf0dd5030 (1 s each); short INTx run only if MSI never fired.

### IRQ enable test (2026-11-04, `notes/irq-test.txt`) – **MSI works; InterruptEnable0 bit 31 = master enable**
- MSI (DisINTx+, MSI Enable+, data 0x24). Enable 0x00900000: 0 IRQs. **0x80900000: 108 IRQs/s** (bits 23+20 seen).
  0x40/0x20/0x10900000: none. 0xf0900000 adds bit 28 (240 Hz tick). Windows 0xf0dd5030 also fine.
- Driver default `int_enable` = 0x80900000; handler masks out bits 31 and 24. INTx phase skipped (not needed).

### Driver capture with MSI (2026-11-04, `notes/driver-capture.txt`) – **interrupt-driven capture works**
- IRQ mode: 475 frames in 10 s (47.3 fps), 0 timeouts, ~107 IRQs/s (bits 23+20), capture thread 0.1 % CPU.
  Polling mode: 46.2 fps, 1.5 % CPU. DMA 21–23 ms per 1080p frame (link limit, camera at 1080p60).
- Next: AD9398 setup in the kernel (I2C over SRI + mode tables), then V4L2.

### Kernel driver stage 3 (2026-11-04) – receiver setup in the driver (`driver/mxo2_rx.c`)
- SRI write/read (FIFO wait, valid/echo/error bits), I2C over SRI (ctrl 3 / data 4: start 6,7; stop 5,7; read =
  nack 7, data 0xff, wait FIFO empty, read), AD9398 mode 0 + `rx_mode` (default 10, -1 = untouched) from
  `driver/ad9398_modes.h` (generated by `re/tools/gen_ad9398_header.py`), then the CSC rule + 0x59 pulse.
  Runs at capture start after the Gaia defaults. Delayed work every 500 ms redoes the CSC rule when HDMI
  appears/disappears or the colour space changes (like Windows' monitor loop).
- debugfs `rx`: read = receiver status (STATUS1, 0x24/25/34, HDMI MODE, STATUS3, VIC…), write N = load mode N.
- `run-driver-capture.sh` no longer runs rx_setup.py; it checks frames stop with mode 0 (0x26 bit 7 three-states
  the outputs) and resume with mode 10 → proves the driver controls the receiver.

### Kernel driver stage 4 (2026-11-04) – V4L2 device (`driver/mxo2_v4l2.c`)
- Probe now does the hardware init (`mxo2_hw_init`: DMA mask 32, Gaia defaults + Windows values, AD9398 setup,
  500 ms monitor) so the input is known before streaming. Capture start only sets VinControl/WDMA Control2,
  IRQ, bus master, thread; `cap_mode` = debugfs (2 test buffers) or V4L2.
- Input detection (`mxo2_rx.c`): VIC table (16/31/34/33/32 = 1080p60/50/30/25/24, 4/19 = 720p60/50), else
  HSYNCs-per-VSYNC (≈1125 → 1080p, ≈750 → 720p); only when STATUS1 = 0x67. Shown in debugfs status/rx.
- V4L2: one input "HDMI", YUYV at the detected size (format follows the input; S_FMT can only confirm it),
  vb2 dma-sg (GFP_DMA32), per-buffer coherent card SG list built in buf_prepare (`mxo2_build_card_sg`, runs
  split at 0x1f000), PCI DMA straight into the buffer, PitchRes = width*2/4. Chroma LUT ((b<<1)|1)^0x80 in place
  for YCbCr 4:2:2 sources (`chroma_fix` -1 auto / 0 / 1). MMAP, DMABUF, read(). No buffer queued → frame
  dropped (`no_buffer`).
- Test: `run-v4l2-test.sh` (needs v4l-utils) → `notes/v4l2-test.txt` + `notes/v4l2-frame.raw`.

### V4L2 test 1 (2026-11-04, `notes/v4l2-test.txt`) – **/dev/video0 works**
- v4l2-ctl --all: 1920x1080 YUYV, Rec.709 limited, 60/1, input HDMI ok. 300 frames at ~43.3 fps, 0 timeouts,
  0 no_buffer; MSI ~719 IRQs. read() works. Frame `notes/v4l2-frame.png` (plain decode) = correct colours.
- v4l2-compliance -s: 56/57; fail G/S_PARM: frame intervals advertised without V4L2_CAP_TIMEPERFRAME → fixed.
- 43 vs 47 fps: chroma LUT ran in the capture thread → moved to an ordered workqueue ("mxo2-fix"), which also
  completes the buffer; stop_streaming flushes it before returning the rest.

### V4L2 test 2 (2026-11-04) – **57/57 compliance, 47.9 fps at 1080p60 input**
- Streaming 47.9 fps (link limit ≈ 21 ms DMA per 1080p frame), 0 timeouts / no_buffer; v4l2-compliance -s 57/57.
- Usable from apps: `modprobe -a videodev videobuf2-v4l2 videobuf2-dma-sg && insmod driver/mxo2.ko`,
  then e.g. `ffplay -f v4l2 /dev/video0`.
- Open: install (DKMS / modules dir + autoload), 720p input test through V4L2, interlaced formats, audio,
  root cause of the 4:2:2 chroma layout (7-bit chroma workaround), per-frame copy limit at 1080p50/60.

### Install (2026-11-04) – DKMS
- `driver/dkms.conf` (mxo2 0.1, AUTOINSTALL), `driver/install.sh` (apt: dkms, linux-headers-amd64 so new kernels
  get headers; copies *.c *.h Makefile dkms.conf to /usr/src/mxo2-0.1; dkms add/build/install; modprobe),
  `driver/uninstall.sh`. Module has MODULE_VERSION 0.1 and PCI alias pci:v102Bd8350sv102Bsd9000 → udev loads it
  at boot; modprobe pulls videodev/videobuf2-*. No Secure Boot here. User is in group `video`.
- After changing driver source: `sudo sh driver/install.sh` again (removes + reinstalls the DKMS copy).
  Test scripts in re/ still insmod ../driver/mxo2.ko – unload the installed one first (they rmmod mxo2).

### Audio capture – from code (2026-11-04)
- Gaia FUN_00028b34 gives the "ACaptur_" object (Gaia+0x4c3·8, ctor FUN_0001112c, max 8 channels, alignment
  param 0x80) AudioWDmaControl/Address + AudioWDmaTimeStamp1/2; idle Control = 0x48000000.
- **Start FUN_000112a8(addr, bytes, channels)**: Address = addr & 0x3fffffff; Control = 0x80000000 | 0x48000000 |
  ch_code << 21 (2→0, 4→1, 8→2, 16→3) | (bytes >> 2 & 0xfffe); bytes ≤ 0x40000.
  Windows 0xc8403200 = 8 ch, 51200 B = 1600 samples × 8 ch × 4 B (48 kHz @ 30 fps); blocks at 0x1fb0800 + k·0xc800,
  7 blocks up to 0x2008000 (where the LQ buffers start). AudioInputCtl 0x1 while previewing (idle 0x40001).
- Built: `run-audio-probe.sh` → `tools/audio_probe.py` → `notes/audio-probe.txt` + `notes/audio-raw.bin`:
  AD9398 audio status (N/CTS, channel status, audio InfoFrame), block timing for 25600/51200/102400 B with and
  without video capture, 6 chained blocks read via BAR1 window 3, per-channel stats + tone frequency.

### Audio probe 1 (2026-11-04, `notes/audio-probe.txt`, laptop 720p50, speaker-test 1 kHz)
- AD9398: audio packets present, N=6144 CTS=74250 (48 kHz at 74.25 MHz), channel status freq 02 (48 kHz),
  word length 0b, audio InfoFrame 01 01 00…
- **Data format: 8 interleaved 32-bit words per sample frame, 16-bit sample in the upper half (low 16 bits 0),
  48 kHz.** Tone found on ch1 exactly 1000 Hz (speaker-test cycles channels → only one active), amplitude ≈26270.
  Blocks are clean continuous sines (the "off-sine" counts were the laptop's tone drifting vs. 48 kHz).
- Timing: AudioWDmaTimeStamp1 changes ~10 ms after arming for every size (25.6–102.4 KB), video on or off →
  a block **starts at the next video-frame boundary**, TS1 = start time. Chained arming at TS1 changes gave
  starts 20/40 ms apart (frame = 20 ms) – long blocks (33 ms) delay the next start.
- Built: `run-audio-probe2.sh` → `tools/audio_probe2.py` → `notes/audio-probe2.txt`: blocks of exactly one frame
  (48000/fps samples, fps from VIC), strategy A = arm next at block start (queue), B = arm after the block ended;
  gap per boundary from the phase of a 997 Hz tone (validated on synthetic data with planted gaps). Left-only
  tone → channel order.
- Probe 2 run 1 (`notes/audio-probe2.txt`): VIC 19 → 50 fps, 960 samples/frame. Strategy A: TS1 deltas 4–5 ticks
  (= 1 frame), wall 20 ms each → one block per frame. Then crashed: 12 × 30720 B from 0x1fb0800 passes 0x2000000
  (end of BAR1 window 3). Test area moved to 0x1f00000.

### Audio probe 2 (2026-11-04, `notes/audio-probe2.txt`, laptop 720p50, 997 Hz left only) – **gapless rule found**
- Blocks of exactly one frame (960 samples @ 50 fps). **Strategy A (arm next as soon as TS1 shows the current
  block started): 11/11 boundaries gapless**, TS1 every 20 ms. Strategy B (arm after the block ended): every
  boundary loses exactly one frame (40 ms steps). **Left = data channel 0** (ch1 = right).

### Kernel driver stage 5 (2026-11-04) – ALSA capture (`driver/mxo2_audio.c`)
- Capture start/stop replaced by users: `mxo2_capture_get/put(MXO2_USER_DEBUGFS | V4L2 | AUDIO)`; thread runs
  while any user exists, video WDMA always re-armed (paces the loop), video copied only for a video user
  (frame_lock guards delivery vs. put). `mxo2_dma_to_host()` takes an on-board byte address + PitchRes width.
- Audio: on-board ring of 8 slots × 64 KiB at 0x1f00000; strategy A arming from the thread (wait loop wakes on
  vsync; during a video DMA a vsync wakes it too → `mxo2_audio_arm` polls ≤ 2 ms for the block start).
  Block size = htotal·vtotal·N/(128·CTS) as 40.24 fixed point with carried fraction (800.8 at 59.94), only
  if within 2 % of 48000/fps(VIC), else 48000/fps. htotal = Gaia VinStatusHDMI (last non-zero), vtotal =
  AD9398 HS/VS + 1, N/CTS from AD9398 0x7b–0x7f (read by the 500 ms monitor). Finished blocks (started − 1)
  PCI-DMA'd (width 8 dwords, one SG entry) into a coherent bounce buffer, ch0/ch1 upper 16 bits → S16_LE
  stereo ALSA buffer (vmalloc), period_elapsed. >6 pending → overrun skip.
- ALSA: card "MXO2 Mini", PCM "MXO2 HDMI", capture only, 48 kHz S16_LE 2 ch; prepare takes the AUDIO user
  (thread start may sleep), hw_free/close drop it; trigger only flips `running`. debugfs status has an audio line.
- Test: `run-audio-test.sh` (laptop tone) → `notes/audio-test.txt`, `notes/audio-only.wav`, `notes/audio-video.wav`;
  `tools/wav_check.py` reports per-channel level, tone and phase jumps per 10 ms (self-tested).

### Audio test 1 (2026-11-04 23:06) – blocks flowed but **all samples zero**; reload trouble
- dmesg: 250 audio blocks in 5 s (50/s at 720p50) alone, video+audio together fine; but audio-only.wav and
  audio-video.wav are digital silence (wav_check: RMS 0). Cause unknown: no data on board (tone not playing /
  AudioInputCtl) or the audio PCI DMA (PitchRes width 8 dwords is a guess) not delivering.
- New debugfs `audio`: counters + last block's first frames / per-channel peaks from the host bounce buffer AND
  from card memory via BAR1 (MemoryTargetControl = window, restored) → tells which side is zero.
- Re-runs failed: WirePlumber holds /dev/snd/controlC1 → `rmmod mxo2` fails (silently) → module stays loaded
  (install.sh at 23:08 also only "modprobe'd" the already loaded one). `re/reload-driver.sh` (sourced by test
  scripts): unload; if busy, stop the user's PipeWire units (incl. sockets) and restart them on exit; build
  driver/mxo2.ko if missing; insmod. run-audio-test.sh uses it.
- reload-driver.sh bug (run 2, `notes/audio-test.txt`): driver path came from `dirname $0` after the script had
  already cd'ed into re/ → "re/re/../driver" when started as `sh re/run-audio-test.sh` → "/mxo2.ko missing".
  Now `cd ../driver` relative to re/ (all run scripts cd there first); simulated the user's call – resolves.

### Audio test 3 (2026-11-04, `notes/audio-test.txt`) – **driver audio works, gapless**; test tone was intermittent
- audio-only.wav: 997.7 Hz on ch0 (left), peak 26271, **0 phase jumps over 118 consecutive 960-sample blocks**,
  then digital silence starting mid-block (sample ~750 of block 118) → the source went quiet (speaker-test plays
  ~2.7 s periods with pauses), not a block/DMA problem. Blocks kept arriving 50/s, 0 overruns, 0 DMA errors;
  debugfs `audio` shows the silent last block zero in both host copy and card memory (copy path consistent).
  audio+video run: silent throughout (fell into a speaker-test pause); video 230 frames meanwhile.
- spf 960.000 from N 6144 / CTS 74250 / htotal 1980 / vtotal 750 (720p50) – formula confirmed.
- reload-driver.sh worked: stopped/restarted PipeWire (WirePlumber held controlC1).
- Next run: continuous stereo tone (python | aplay one-liner in run-audio-test.sh, L 20000 / R 10000).

### Audio test 4 (2026-11-04) – **ALSA audio capture works, gapless, also alongside video**
- Laptop still ran speaker-test (peak 26271, R silent – the python tone would be L 20000 / R 10000; aplay was
  probably blocked by the running speaker-test). Both 5 s recordings: tone on ch0, **0 phase jumps**; exactly one
  ~0.9 s silent stretch each, starting mid-block (speaker-test's channel pause) → source, not driver.
  Audio+video at the same time: 230 video frames + 250 audio blocks, 0 overruns/DMA errors.
  Debug peek: host copy == card memory. Right channel = data ch1 (probe 1).
- driver/install.sh now stops/restarts the user's PipeWire when the loaded mxo2 is held (as reload-driver.sh).
- Open: 44.1/32 kHz sources (driver assumes 48 kHz; channel status freq could be checked), multichannel (>2),
  PipeWire/OBS end-to-end test.

### Audio sample rate (2026-11-04) – detection + ALSA at the source rate
- Rate sources: IEC 60958 channel status AD9398 0x61[3:0] (0→44.1, 2→48, 3→32, 8→88.2, A→96, C→176.4, E→192 kHz;
  datasheet) and clock regeneration fs = pclk·N/(128·CTS) with pclk from the VIC table (148.5 / 74.25 MHz),
  snapped to a standard rate within 1 %. The latter wins (independent of what the source claims); mismatch
  logged once. Without VIC (lines-only detection) only channel status. rx debugfs + status show both.
- Samples per frame = htotal·vtotal·N/(128·CTS) is rate-independent; fallback now rate/fps.
- Audio ring slots 128 KiB (4096 samples: 96 kHz @ 23.98 fps), 8 slots = 0x1f00000..0x2000000; bounce 128 KiB,
  card SG split at 0x1f000 (≤ 5 entries).
- ALSA: rates 32–192 kHz advertised, open() constrains to the current source rate; if the source rate changes
  mid-stream, silence + warning until reopened (no wrong-speed audio). Read-only control "HDMI Sample Rate" (PCM
  iface) = current rate.
- Test: `run-audio-rate-test.sh` (laptop continuous tone at R = 44100 / 48000) → `notes/audio-rate-test.txt`:
  rate from rx/amixer, offered hw params, 4 s at that rate, wrong rate refused, tone pitch (997 Hz only if right).
- Rate test 1 (`notes/audio-rate-test.txt`, laptop aplay at 44100): **source still sent 48 kHz** (channel status and
  clock regeneration both 48000, N 6144 / CTS 74250) → the laptop resamples; probably the MXO2 EDID only offers
  48 kHz (check the laptop's /proc/asound/card*/eld#* "sad" rates). 48 kHz path perfect: 997.00 Hz, L peak 20001 /
  R 10001 (channel order confirmed), gapless, 0 quiet. Only RATE 48000 offered; -r 44100 fell back to 48000 (refused).
  Script bugs fixed: amixer needs iface=PCM for the control; wrong-rate check now shows arecord's warning.
- Rate test 1 (`notes/audio-rate-test.txt`, laptop aplay at 44100): **source still sent 48 kHz** (channel status and
  clock regeneration both 48000, N 6144 / CTS 74250) → the laptop resamples; probably the MXO2 EDID only offers
  48 kHz (check the laptop's /proc/asound/card*/eld#* "sad" rates). 48 kHz path perfect: 997.00 Hz, L peak 20001 /
  R 10001 (channel order confirmed), gapless, 0 quiet. Only RATE 48000 offered; -r 44100 fell back to 48000 (refused).
  Script bugs fixed: amixer needs iface=PCM for the control; wrong-rate check now shows arecord's warning.
- Rate test 2 (laptop really at 44.1 kHz): detection **correct** (channel status 44100, clock regeneration 44100,
  N 6272 / CTS 82500, control 44100, only 44100 offered, 48000 refused). But capture: **tone 916.23 Hz =
  997·44100/48000** and arecord EIO after 10–20 blocks (blocks of 882 samples armed). → the card's audio engine
  apparently samples at its own 48 kHz (AudioPllCtl, never changed by the Windows Gaia code, stays 2); a 44.1 kHz
  source is sample-and-held to 48 kHz and arrives as 960 samples/frame.
- Driver: experiment params `audio_clock` (default 48000: blocks = clock·htotal·vtotal·N/(128·fs·CTS), ALSA rate =
  clock; 0 = follow source) and `audio_pll` (AudioPllCtl at audio start, default 2). `run-audio-clock-test.sh`
  compares A (48000/pll 2) with source-following + pll 0/1/3 → `notes/audio-clock-test.txt`.

### Audio clock test (2026-11-05, `notes/audio-clock-test.txt`, laptop at 44.1 kHz) – **card is always 48 kHz**
- A (audio_clock 48000, AudioPllCtl 2): **997.00 Hz, gapless**, 150 blocks in 3 s (960/frame), L 20000 / R 10000.
- Follow-source (882/frame, ALSA 44100) with AudioPllCtl 0 / 1: tone 915.8 Hz (48 kHz data labelled 44.1) and
  EIO stall after 10 blocks; AudioPllCtl 3: 890.7 Hz, 11 phase jumps, stall. → No PLL setting follows the
  source; the FPGA resamples to 48 kHz (matches Matrox spec: 48 kHz only).
- Final: ALSA always 48 kHz; block = 48000 × frame time, frame time from htotal·vtotal·N/(128·fs·CTS) with fs =
  detected source rate (so 44.1/32/96 kHz sources keep correct block sizes); "HDMI Sample Rate" = source rate.
  Defaults audio_clock 48000 / audio_pll 2 = this config; params remain for experiments.

### Interlaced input (2026-11-05) – start
- VideoWDmaControl (FUN_00014a14, decoded): 0x80000000 | u3<<26 | (p5&3)<<24 | 10bit<<23 | fmt_code<<20, fmt_code from
  p7 (1/3→6 & u3, 6→4 & u3, 5→0, 0x35/0x36→1); Control2[26:16] = pitch/4 (Windows used pitch 0 = packed).
  Progressive capture = p5 2. p5's source not found (FUN_00014a14 is main-vtable slot +0x18, not in the
  child interface vtable 0x538f0; no direct 7-arg call located) → empirical.
- Built: `run-interlace-probe.sh` → `tools/interlace_probe.py` → `notes/interlace-probe.txt` + `notes/interlace/*.raw`:
  AD9398 mode 5 (1080i table), VinControl code 0x0d / 0x03 / 0x05 × p5 0–3: TS1 changes per second, rows written
  (marker fill via BAR1 if writable), row-to-row luma similarity, raw slot dumps.
- Probe 1 (`notes/interlace-probe.txt`, camera 1080i50, VIC 20, HS/VS 562): **each WDMA arm captures one field**:
  50 TS1 changes/s, 540 rows written packed (pitch 3840) for p5 0/1/2 with VinControl code 0x0d or 0x05; code 0x03
  captures nothing. BAR1 not writable (marker fill impossible; rows found by diffing dumps). Field image correct
  (`notes/interlace/0d02-p2.png`: top half = squashed field, colours right – source RGB).
- Built: `run-interlace-probe2.sh` → `tools/interlace_probe2.py` → `notes/interlace-probe2.txt`: (1) Control2 pitch
  field = 7680 → expect only even rows changed (hardware weave possible); (2) 6 consecutive fields into separate
  2 MiB areas + per-field VideoWDmaStatus/TS1/TS2/Int0/AD9398 STATUS2, parity by half-line row matching
  (validated on a progressive frame: top→bottom 3.4 vs 6.2).
- Probe 2 run 1 (`notes/interlace-probe2.txt`): Control2 pitch 7680 with p5 2 → rows 0–539 changed contiguously =
  **pitch ignored** (FUN_00013e60 confirms obj+0x70 = VideoWDmaControl2 gets the pitch, so it is the right register).
  Parity test crashed: 6 × 2 MiB areas exceed the 8 MiB BAR1 window → reads now switch MemoryTargetControl.
  Test 1 now repeats the pitch check for p5 0–3. Fallback if no mode honours the pitch: weave with the PCI DMA
  (per-field card SG list, one entry per line → every other line of the host buffer).
- Probe 2 run 2 – **hardware weave exists**: with pitch 7680, p5 0 wrote rows 0,4,8…1076 and p5 1 rows 2,6,10…1078
  (270 rows: the engine doubles the pitch itself, p5 1 adds one pitch, stops at row 1080); p5 2/3 ignore the pitch.
  → pitch 3840: p5 0 = field to even rows, p5 1 = to odd rows.
  Parity: consecutive fields alternate; **InterruptStatus0 bit 24 toggles per field** (95…/94… – "always set" with
  progressive input); read right after the TS1 change, bit24 = 1 came before the field that lies half a line
  ABOVE (top). TS2 of field k = TS1 of field k−1 (TS1 = end, TS2 = start), 4–5 ticks per field (50 Hz).
- Built: `run-interlace-probe3.sh` → `tools/interlace_probe3.py`: pitch 3840, p5 from bit 24 per field, rule A (as
  derived) vs B (inverted), comb ratio (mean |row i−row i+1| / mean |row i−row i+2|, all i; validated: correct
  0.70, swapped 0.97) → `notes/interlace/weave-A/B.raw`.
- Probe 3 (`notes/interlace-probe3.txt`): **weave confirmed**. Rule A (bit24 = 1 → top next, p5 0) comb ratio 0.876,
  inverted rule 1.140 (> 1 = combing); `notes/interlace/weave-A-crop.png` clean at full resolution.

### Kernel driver: 1080i (2026-11-05)
- rx: VIC 5 / 20 = 1080i60 / 1080i50 (frame rate 30/25, pclk 74.25 MHz), lines fallback HS/VS 555–570 → 1080i;
  in_vtotal = 2·HS/VS + 1 (1125) for interlaced. Monitor loads AD9398 table 5 for interlaced input, back to
  rx_mode for progressive.
- capture: thread mode fixed at start (cap_interlaced). Interlaced loop: Control2 = 0x41 | 960 << 16 (pitch 3840),
  per field end read Int0 bit 24 → top: next slot, p5 0; bottom: same slot, p5 1; frame complete when bottom
  follows top in the same slot, delivered one field later; ts_changes counts frames. Progressive loop unchanged
  (now capture_loop_progressive); deliver_frame() shared.
- V4L2: 1920x1080 YUYV, field V4L2_FIELD_INTERLACED_TB (buffer field from cap_interlaced), 25/30 fps.
- audio: blocks follow vsync = per field for 1080i → block = htot·vtot(1125)·N/(128·CTS·2) (960 @ 1080i50).
- Test: `run-v4l2-interlace-test.sh` → `notes/v4l2-interlace-test.txt`.
- **1080i driver test passed** (`notes/v4l2-interlace-test.txt`, camera 1080i50 YCbCr 4:2:2): AD9398 table 5 loaded
  automatically, V4L2 1920x1080 "Interlaced Top-Bottom" 25/1, 100 frames at 25.00 fps, 0 timeouts / no_buffer,
  comb ratio 0.901, image clean with correct colours (chroma fix applies), audio 200 blocks in 4 s (50/s = per
  field) at 960 samples, 0 overruns (camera mic, no tone to check gaps), v4l2-compliance 57/57.

### SD formats (2026-11-05) – driver support, untested
- AD9398 0x30[3:0] = HDMI pixel repetition; "clock and data outputs automatically de-repeat" (datasheet) → 480i/576i
  (1440 wide, 2x) reach the card 720 wide.
- rx: one format table (VIC pair, size, fps, TMDS clock, interlaced, HS/VS range, AD9398 table): 480p VIC 2/3 (table 2),
  576p 17/18 (4), 480i 6/7 (1), 576i 21/22 (3), 1080i 5/20 (5), others rx_mode. Line fallback: 525/625 progressive,
  262/312 per field interlaced (in_vtotal = 2·lines+1). Monitor loads the table per format. rx debugfs shows
  pixel repetition + wanted table.
- capture: weave pitch = input width·2 (cap_line_bytes, fixed per run); `vin_code` param (VinControl bits 12:8,
  default 0x0d; SD codes 0x10–0x13 for experiments).
- audio: htotal (de-repeated, from VinStatusHDMI) × pixel repetition for the N/CTS formula (576i: 960/field, 480p 800.8).
- Test: `run-v4l2-sd-test.sh [params]` → `notes/v4l2-sd-test.txt`, `notes/sd-frame.png`.
- **576p50 test passed** (`notes/v4l2-sd-test.txt`, camera VIC 18, YCbCr 4:2:2): detected 720x576p @ 50, AD9398 table 4
  loaded automatically, pixel repetition 1x, V4L2 720/576 SMPTE 170M 50/1, 100 frames at 50.00 fps, 0 timeouts,
  `notes/sd-frame.png` correct (default vin_code 0x0d fine for SD too), audio 150 blocks in 3 s at 960 samples
  (htotal 864, vtotal 625, CTS 27000), v4l2-compliance 57/57.
  Not done: VIC 18 is 16:9 anamorphic → VIDIOC_G_PIXELASPECT (SD 4:3/16:9) missing; 576i/480i/480p untested.
- Pixel aspect: `in_wide` from the VIC pair (second VIC = 16:9: 3/7/18/22) or AVI InfoFrame 0x82[5:4] (10 = 16:9);
  VIDIOC_G_PIXELASPECT (also feeds CROPCAP) returns height/width per BT.601 as vivid: 576 lines 54/59 (4:3) or
  81/118 (16:9), 480 lines 11/10 or 33/40, HD 1/1. SD test prints `v4l2-ctl --get-cropcap`.
- Aspect test 1 (`notes/v4l2-sd-test.txt` 01:03, VIC 18): rx shows "picture aspect 16:9", but `v4l2-ctl --get-cropcap`
  printed nothing → VIDIOC_CROPCAP not enabled: the V4L2 core only enables it with vidioc_g_selection.
  Added read-only g_selection (CROP_BOUNDS / CROP_DEFAULT = full frame, CROP → -EINVAL). Needs a rerun
  (check "Pixel Aspect: 81/118" and that v4l2-compliance stays 57/57).
- Aspect test 2 (01:05): **"Pixel Aspect: 81/118"** for VIC 18 (576p 16:9), v4l2-compliance 57/57 with the read-only
  g_selection, 50.00 fps, 0 timeouts. 4:3 (VIC 17 → 54/59) reported working by the user but not in a saved log.

### Analog inputs (2026-11-05) – from code
- Box input select MVKAVCIO FUN_000493d4: analog inputs 1 / 2 / 4 → AD9398 power down (FUN_00014d58(rx,0)), ADV7181 on
  (FUN_0002a798(dec,1): PwrManagement 0x0f &= ~0x22), FUN_0003f328 (decoder input/mode), board vt+0x230(4,1); FPGA
  (MVKAVBUS FUN_0002bf54): VinControl bits 1:0 = 0, VinControlAnalog bit16 = 1 only for input 2.
- ADV7181 class (ctor FUN_00025030 "ADV7181_", I2C 0x42): 187 registers (chipregs.py now also parses the WO ctor
  FUN_00019a24: 0x13, 0x2d–0x30, 0x8f–0x91, 0xb2/b3, 0xed share addresses with status regs), 14 mode tables
  (DAT_00072520, mask DAT_00074e10, or DAT_000756e0). Load FUN_0002976c: 0x0f |= 0x80 (reset), 20 ms, writable
  regs in index order = (mode & mask) | or → `tools/adv7181_modes.py` (`tools/adv7181_tables.py`), 144 writes/mode.
- **Mode choice (vt+0x48 = 0x29224, from asm)**: width ≤ 720: PAL (rate code 3 = 25): input 1 → 3, 4 → 4, 2 → 5;
  else NTSC: 1 → 0, 4 → 1, 2 → 2. 1080 lines: 29.97 → 6, 30 → 7, 25 → 8. 720p: 59.94 → 9, 60 → 10, 50 → 11.
  → input 2 = component (only one with HD modes; reg 0x05 PrimaryMode 1 = component processor for 6–11, INSEL 0x00);
  inputs 1/4 = composite and S-video (INSEL x5 / x6; 0x5x NTSC, 0x8x PAL).
- After the table (vt+0x70 = FUN_00028f04): modes 0/1/3/4: 3e=64 3f=9e 0e=80 52=46 54=00 7f=ff 81=30 90=c9 91=40
  92=3c 93=ca 94=d5 b1=ff b6=08 c0=9a cf=50 d6=dd d7=e2 e5=51 0e=00 (ADI hidden-register script); modes 2, 5–8:
  b7=1b; 9–11: nothing; ≥ 12: 0e=80 52=46 54=00 0e=00.
- vt+0x78 = FUN_000293c4: per-input calibration from EEPROM (composite/S-video gains via FUN_00029ba8, component
  tables via FUN_00029f08) – later.
- Source available: composite only (user). Built: `run-analog-probe.sh` → `tools/analog_probe.py` →
  `notes/analog-probe.txt` + `notes/analog/frame.png`: AD9398 powered down, ADV7181 modes 3/4/0/1 tried (lock +
  detected standard), FPGA analog input (VinControl bits 1:0 = 0, VinControlAnalog 0xc0000000) with codes
  0x0d / 0x11 / 0x10 (WDMA captures per second), then a woven frame (pitch 1440, bit-24 field order).
- Probe 1 (`notes/analog-probe.txt`, NTSC source): ADV7181 ident 0x19. **Composite = input 1** (modes 3 and 0 lock,
  fsc lock; modes 4/1 = input 4 = S-video don't). AD_RESULT NTSC M/J → mode 0 used. FPGA: **0 captures** with
  VinControl 0x0d00 / 0x1100 / 0x1000 (VinControlAnalog 0xc0000000); VinStatusAnalog 0x898 (0x0d/0x11) or 0 (0x10).
  FUN_0002bf54 confirms Windows only sets VinControl[1:0] = 0 and VinControlAnalog bit 16 (component).
- Built: `run-analog-probe2.sh` → `tools/analog_probe2.py`: code 0x12/0x10/0x0d/0x00 × VinControlAnalog
  0/0x4…/0x8…/0xc…0000: captures/s, VinStatusAnalog, Int0 bit counts; first capturing combo → woven frame2.
- Probe 2 (`notes/analog-probe2.txt`): codes 0x12/0x10/0x0d/0x00 × VinControlAnalog 0/4/8/c…: **0 captures in all 16**;
  VinStatusAnalog mostly 0 (0xabe once, 0x35a = 858 once, 0x898); Int0 bit 26 ~7/s, bit 28 240/s (clock tick).
- Input select's board call vt+0x230(4,1): pointer search puts FUN_000493d4 in a long run of code pointers at
  Ghidra 0x83510..0x84110 (several vtables back to back); +0x230 from that start = FUN_00038890 (generic chain of
  vt calls) – start uncertain, not pursued.
- Built: `run-analog-probe3.sh` → `tools/analog_probe3.py`: GpIO 0, 1 (Windows value), single bits, 0x7f: decoder lock,
  AD9398 alive, captures with VinControlAnalog c… / 0. GpIO restored to 0.

### mxo2-regs run 3 (2026-11-05, Windows, NTSC composite) – `notes/win/analog/`, `notes/win-regshadow-analog.txt`
- The run overwrote `notes/win/*.txt`; analog logs moved to `notes/win/analog/`, HDMI run 2 restored from the Trash
  (tools default to `notes/win/2-preview.txt` = HDMI; `regshadow.py notes/win` matches `win-regshadow.txt` again).
- Analog preview/recording vs HDMI: **VideoWDmaControl2 0x01680101** (bit 8 instead of bit 6, width 360 dwords = 720 px,
  one line, no weave pitch) – probes 1–3 used 0x41 → likely Control2 bit 6/8 = WDMA source HDMI/analog.
  VideoWDmaControl 0x80000000 / 0x81000000 (bit 25 clear, bit 24 toggles = field), VinControl 0x0d00 (bits 1:0 = 0),
  VinControlAnalog 0xc0000000, GenlockControl 0x1000 (HDMI 0x401), SystemClockReload 0x8f48 (HDMI 0x184e8),
  AudioInputCtl 0x40001 (HDMI 1), VinStatusHDMI 0, Int0 bit 24 not seen. Ring slots 0x356600 / 0x6acc00.
  Lq path 720×486 (LqRDmaCrop 0x2d0, Crtc 0x1e6 rows) → NTSC is 486 lines; recording upscales to 1920×1080.
- Built: `run-analog-probe4.sh` → `tools/analog_probe4.py`: Windows analog system values, sweep Control2
  (0x01680101, 0x101, 0x01680141, 0x01680041, 0x41, 0x01680100, 0x01680001) × Control (0x80/0x82000000) → captures/s;
  first capturing combo → `notes/analog/field4-{0,1}.png` (pitch 1440, bit 24 = 0/1) and `frame4.png` (weave 2880).
- Probe 4 (`notes/analog-probe4.txt`, NTSC composite, 240p console menu): **first analog capture.** Control2 bit 8 (0x100)
  is the analog source select: every Control2 with 0x100 (0x01680101, 0x101, 0x01680141) gives 60 captures/s, every
  one without it (0x41, 0x01680041, 0x01680001) 0, and 0x01680100 also 0 → bit 0 is needed too. Control 0x80 / 0x82… both work.
  With width 360 dwords (one 720-px line) the WDMA **weaves in hardware**: Control bit 24 = 0 writes the even rows,
  1 the odd rows (field4-0/1: exactly the other parity unchanged). `notes/analog/field4-1.png` = clean full frame.
  Int0: bit 23 ≈ 60/s (field event?), bit 24 constantly set → not the field flag for analog (weave via bit 24 of
  Int0 wrote even rows only, frame4). Source is 240p (both fields identical), so field order is still unknown.
  Colours read with raw2png "plain" – not yet checked against the source. Only 480 rows read (Windows: 486).

### Analog colours + 240p (2026-11-05)
- Built: `run-analog-probe5.sh` → `tools/analog_probe5.py` (test pattern on composite): decoder regs 0x00–0x1f and
  Status3 bit 6 (interlaced; probe 1–4 read 0x09 = not interlaced → 240p console), two woven frames into fresh areas
  (0x600000/0x700000) → `notes/analog/colour-{a,b}.raw/png` + rows changed (lines per frame), and 240p layout tests
  (Control2 0x101 with width 360/0/180/720, Control bit 25) → which 1440-byte rows each capture writes.
- Probe 5 (`notes/analog-probe5.txt`, 240p Test Suite SMPTE bars on NES, NTSC composite): Status3 bit 6 = 0 (not interlaced).
  **NTSC field = 243 lines, frame 486** (rows written by the capture). Layout: Control bit 25 clear → line stride =
  2 × Control2 width (dwords), bit 24 = +1 line (weave; w720 → every 4th row); **bit 25 set (0x82000000) → packed lines**
  (243 rows with width 360 / 180 / 0); bit 25 clear with width 0 writes 1 row. → 240p = Control 0x82000000, one field per frame.
- Colours (`notes/analog/colour-a.png`): plain YUYV, clean (sd < 1 on flat areas, a == b), **no bit mangling**; hue right.
  Levels low: black Y 1 (0x04 = 0xcd, bit 0 = extended range 1–254), grey bar 77, bottom white 112, chroma deviations
  ~0.45× → overall ~2× too low (NES palette is not 75 % bars: yellow = cyan Y, green = magenta = red Y).
  Dot pattern (Y sd 5–6) in coloured bars = subcarrier left in luma: 0x17 = 0xb3 (YSFM 0x13 wide band, relies on comb).
- Windows calibration (vtable 0x82880 = ADV7181: +0x78 FUN_000293c4 → +0x88 FUN_000292b4(record)): manual gains,
  luma = u16 rec+0x0a, chroma = u16 rec+0x08 (composite) / +0x0e (S-video) → regs 0x2d/0x2e (CMG[11:8]/[7:0]) and
  0x2f/0x30 (LMG). Records from EEPROM (FUN_000296e8, EEPROM object +0xe0 = eeprom[0]): PAL 0x1c, NTSC 0x2c,
  NTSC with obj+0xd4 0x3c. Ours: NTSC chroma 0x2e5, luma 0x4cb (table 0x2cc / 0x4a6) → only +3 %, not the 2× gap.
- Built: `run-analog-probe6.sh` → `tools/analog_probe6.py`: 30 decoder configs (EEPROM gains, gains ×2, limited range,
  AGC 0x2c modes, luma filter 0x17, comb 0x19) → per-bar Y (sd) / Cb / Cr, black, from one packed field each.
- Probe 6 (`notes/analog-probe6.txt`): **0x17 = 0xa2 removes the dots** (Y sd 5–6 → 0.5–0.9, levels unchanged; other
  YSFM values and comb 0x19 variants no effect). **Luma AGC** 0x2c = 9e/ae/be/ce/de (sync reference) all give grey
  bar 154 (0.60 = NES $10) = same as manual gains ×2 → table/EEPROM manual gains are half of what this card needs;
  0xee (peak white) clips white at 252; fe/8e = manual. Chroma AGC (bits 1:0 = 10) → yellow Cb 75 instead of 93.
  0x04 = 0xcc → black 16 (NES black is below the 7.5 IRE setup, clipped to 16), luma otherwise unchanged.
- Built: `run-analog-probe7.sh [REG=VAL ...]` → `tools/analog_probe7.py`: default overrides 04=cc 17=a2 2c=ce,
  saves `notes/analog/p7-field.png` (240p packed field) and `p7-frame.png` (weave), top/bottom bar levels.
- Probe 7 (`notes/analog-probe7.txt`, 04=cc 17=a2 2c=ce): **colours correct** (`notes/analog/p7-field.png` 240p packed
  720×243, `p7-frame.png` 720×486 weave): black 16, white (bottom bar) 220, grey bar 153, no dots (Y sd ≤ 1.8),
  chroma from burst AGC. → driver: decoder mode table + 04=cc 17=a2 2c=ce (instead of the EEPROM manual gains).

### Kernel driver: composite input (2026-11-05) – untested
- `driver/adv7181_modes.h` (`tools/gen_adv7181_header.py`): ADV7181 modes 0 (NTSC) / 3 (PAL) composite + hidden script +
  overrides 04=cc 17=a2 2c=ce. rx: `dec_load()` (0x0f power up + reset, 20 ms, tables), `dec_detect_locked()`
  (Status1 lock + AD_RESULT 50/60 Hz → table 3/0, Status3 bit 6 → 480i/576i, else 240p/288p at 60/50 fps),
  `mxo2_select_input()` powers the other chip down (AD9398 0x26 bit 0 / ADV7181 0x0f |= 0x22; back to HDMI reloads
  AD9398 tables 0 + rx_mode). Monitor runs the decoder detection while composite is selected.
- Inputs: V4L2 0 = HDMI, 1 = Composite (S_INPUT refused while any capture user, incl. audio, is active);
  module params `input=1`, `composite_interlaced` (-1 auto / 0 / 1), `analog_field_swap`.
- capture: analog = Control2 0x101 | 360 << 16, VinControl 0x0d00, GenlockControl 0x1000, SystemClockReload 0x8f48,
  ClockLoadCount 0x37176 (HDMI values restored per start); 240p = progressive loop (Control 0x82000000, packed);
  480i = interlaced loop with fields alternating (no field flag known; order may be swapped → `analog_field_swap`).
  NTSC: `cap_skip` = 2 (240p) / 4 (480i) lines so 243/486 → 240/480. Field = V4L2_FIELD_INTERLACED for analog.
  Pixel aspect 240 lines 11/5 (16:9 33/20), 288 lines 108/59 (81/59). Analog audio not supported (rate 0).
- Test: `run-v4l2-analog-test.sh` → `notes/v4l2-analog-test.txt`, `notes/analog-240p.png`, `notes/analog-480i.png`.
- **Composite driver test passed** (`notes/v4l2-analog-test.txt`, NES 240p Test Suite colour bars): auto-detected
  720x240p @ 60 (Status3 09, not interlaced), pixel aspect 11/5, 100 frames at 60.10 fps, 0 timeouts / no_buffer,
  bar levels = probe 7 (wht 153, blu 17; chroma slightly stronger: yel Cb 60 vs 69), `notes/analog-240p.png` clean.
  Input switching HDMI ↔ composite works (AD9398 mode 10 reloaded, format 1920x1080 ↔ 720x240).
  v4l2-compliance 82/82 (two inputs). Forced 480i (`composite_interlaced=1`): 720x480 Interlaced @ 30.05 fps,
  0 timeouts, `notes/analog-480i.png` clean (comb 0.718; both fields equal with a 240p source).
  Open: field order with a real interlaced source, PAL/288p, analog audio, S-video.

### Composite 240p at 256/320 wide (2026-11-05) – untested
- NES picture in the 720-sample line (`notes/analog-240p.raw`): samples 43 .. 687 = 644 = 256 px × 13.5/5.37 MHz;
  Mega Drive H40 320 px × 13.5/6.71 MHz ≈ 644 too.
- Driver: for composite 240p/288p ENUM_FRAMESIZES lists 720/320/256 × 240 (288); S_FMT picks the width (`req_width`).
  Scaled widths: PCI DMA of the 720-wide field into a bounce host buffer (`mxo2_hbuf_alloc(bytes)`, now sized), then
  area-average (1/256-sample taps, ≤ 4 per pixel; chroma per pixel pair) into the vb2 buffer in the capture thread.
  Crop: params `retro_left` (43) / `retro_span` (644), read at stream start. Pixel aspect from the crop: 256 →
  704/805 (≈ 7/8, NES 8:7 PAR), 320 → 176/161 (≈ 35/32). Offline simulation of the scaler: clean 256×240.
- Test: `run-v4l2-retro-test.sh` → `notes/v4l2-retro-test.txt`, `notes/retro-{256,320,720}.png`.
- **Retro widths test passed** (`notes/v4l2-retro-test.txt`): ENUM_FRAMESIZES 720/320/256 × 240; 256×240 (122880 B,
  pixel aspect 704/805), 320×240 (153600 B, 176/161) and 720×240 each 120 frames at 60.1 fps, 0 timeouts /
  no_buffer; `notes/retro-256.png` / `retro-320.png` clean, picture fills the width; v4l2-compliance 82/82 at 256×240.
  (NES is natively 256 wide; 320 needs a 320-px console such as a Mega Drive to judge the crop.)

### OBS / PipeWire hardening (2026-11-05)
- OBS log 02:38 (`~/.config/obs-studio/logs`): user used "Video Capture Device (PipeWire)" (camera portal →
  PipeWire SPA v4l2, which can't select an input → stuck on HDMI without signal); Mic/Aux defaulted to the MXO2
  ALSA source and got 0 packets (no blocks without input vsync). `tools/obs_sim.py` (OBS linux-v4l2 call sequence,
  runs as user): composite 240p OK, 59.6 fps, second handle while streaming OK.
- System suspend (S3, 02:45 → 08:43) left the card unconfigured: decoder reads ff/ff were taken as a 480i signal.
- Fixes: (1) dev_pm_ops: suspend stops the capture thread (users kept), resume = hw_setup (Gaia + AD9398 + input +
  decoder) and restarts capture. (2) decoder status ff/ff = no signal. (3) `input=-1` auto (new default): while no
  video capture and no vb2 buffers, the monitor picks the input with a signal (HDMI STATUS1 0x67 first, else decoder
  lock), 2 rounds in a row; both chips stay powered while idle, the unused one is powered down during a capture
  (`mxo2_input_power`). VIDIOC_S_INPUT locks the choice until the last handle closes. (4) ALSA silence when no block
  for 150 ms (real-time, 5 ms chunks; thread wakes ≥ every 20 ms with an audio user). (5) a video start on an
  audio-only thread with a stale mode restarts the thread; S_INPUT / auto switch allowed with audio only (thread
  restarted). Composite audio: AudioInputCtl 0x40001 (Windows analog value) – untested.
- V4L2 inputs now 0 = Auto (default for VLC, which always sets `v4l2-input`, default 0), 1 = HDMI, 2 = Composite;
  S_INPUT 0 switches at once (`mxo2_auto_input_now`). Built, not yet installed.
- Tests as user (installed build before the Auto input): VLC 60 s composite 720x240 3598 frames = 60.1 fps, picture
  OK; OBS V4L2 source 256x240 + ALSA MXO2: 650 frames / 10.8 s, no select timeouts, audio continuous; OBS PipeWire
  camera source: negotiated 256x240@60, streams. PipeWire fails with "no more input formats" when the OBS source's
  saved size is not offered by the device any more (e.g. after switching source type) - OBS asks only for the saved
  size; reselect the resolution in the source properties (the V4L2 source just takes the driver's size).
  ALSA as user: 5 s in 5.25 s wall, no gaps.

### Analog audio (2026-11-05)
- Windows composite: AudioInputCtl 0x40001, AudioWDmaControl 0xc8000c84 = ch_code 0 (bits 22:21 = 0; HDMI 2 = 8 ch)
  + 0xc84 dwords = 1602 stereo samples x 8 B (one 29.97 Hz frame). Driver used the HDMI 8-ch layout for analog.
- Built: `run-analog-audio-probe.sh` → `tools/analog_audio_probe.py`: A InputCtl × layout (blocks/s, word peaks),
  B samples per field (largest block keeping one block per field), C long block → `notes/analog-audio-long.wav` +
  tone frequency, D 5 s of field blocks → `notes/analog-audio-blocks.wav`.
- Probe 1 (`notes/analog-audio-probe.txt`, NES + audio): data in the 2-ch layout (8 B/sample, left = word 0, upper
  16 bits), long block has audio (peak 1727). NES field rate 60.101 Hz (798.66 samples). **Only block sizes ~792–802
  give one block per field** (smaller and larger both skip fields) → block size must track the field length.
  Blocks of 400 samples: 0.7 blocks/s. Built probe 2 (`run-analog-audio-probe2.sh`): sizes 790–802 and 2-field
  blocks, 4 s each, audio per second + click metric at block boundaries, WAVs `notes/analog-audio2-<n>.wav`.
- OBS: the MXO2 audio is the PipeWire source "MXO2 Mini Stereo" (add an Audio Input Capture source; video sources
  carry no audio).

### Composite sizes (2026-11-05) – built, not installed
- Every composite signal offers 720×480 (576), 720/320/256 × 240 (288). Full height: woven from 480i (30 fps),
  line-doubled from 240p (60 fps, packed field via the bounce buffer); field height: one packed field per frame
  (60 fps, also from 480i). `mxo2_cap_weave()` = composite: interlaced signal and format height ≥ 480.
  S_FMT stores req_width/req_height, kept across signal changes (OBS/PipeWire saved size stays valid).
- Test (as user, after install): `tools/mode_test.sh OUTDIR` – every size, compliance, VLC defaults.
- Probe 2 (`notes/analog-audio-probe2.txt`): one-field blocks only 797–799 (fragile, differs from probe 1);
  **two-field blocks 1590 / 1596 / 1600: steady 30/s, ~47.7k samples/s, click metric ~1** → use one block per two
  fields (Windows: 1602 per 29.97 Hz frame).
- Installed build (Auto input + composite sizes) tested as user (`tools/mode_test.sh`): 720x480 (line-doubled 240p,
  Field None, 60.09 fps), 720/320/256x240 60.1 fps, v4l2-compliance 107/107, VLC without options → Auto →
  720x480, 895 frames / 14.9 s, pictures OK.
- Driver analog audio (built, not installed): stereo layout (ch_code 0, 8 B/sample, AudioInputCtl 0x40001),
  block = round(2 × 48000 × field period), field period = vsync IRQ interval averaged over 64 fields
  (`field_ns`, status "field period"); nominal 59.94/50 Hz until measured. debugfs audio dump follows the layout.

### Composite sharpness / 1:1 console pixels (2026-11-05) – built, not installed
- User: 256x240 from a Famicom looks blurry. True 1:1 sampling is impossible: the ADV7181 always samples at 13.5 MHz
  line-locked (858 samples per NTSC line = 2.516 per NES dot, 341 dots/line). Both clocks are locked to H sync,
  so the console pixel grid sits at fixed sample positions.
- Main blur source was our decoder luma filter: edge 10–90 % rise 0x17=0xb3 (wide band, table value) 2.1 samples =
  0.8 NES px; 0xa2 (notch, chosen in probe 6 against the dots) 5.6 samples = 2.2 px.
- Dots = colour subcarrier left in luma (DFT: 3.586 MHz, period 3.76 samples, ±6 % of grey on colours). Software
  notch: mean at ±P/4 (P = 3.771 samples) = 3-tap 121/14/121 per 256 → green bar sd 5.5 → 0.5, edges ~1.5 px.
- Driver: 256/320 now point-sampled at console pixel centres (linear between 2 samples): pitch = 858 (PAL 864) /
  dots per line (`retro_dots256` 341, `retro_dots320` 420), `retro_center` 36536 (1/100 sample; NES bars
  43.29..687.42); chroma at pixel-pair centres. V4L2_CID_SHARPNESS (0..2, default 2): 0 = 0xa2, 1 = 0xb3 + software
  notch (bounce path per line; 720-wide composite frames in the fix workqueue), 2 = 0xb3 only. Pixel aspect from
  the span (256 → ≈ 8:7).
- User: sharper now. Switching HDMI ↔ composite in OBS "impossible": OBS log 13:07 = PipeWire camera source; HDMI
  appeared (1080p60) → Auto took HDMI → PipeWire "no more input formats" (saved 256x240 not offered by HDMI).
- Tests as user (installed sharpness build, HDMI 1080p60 + Famicom): OBS V4L2-source sequence switches fine
  (HDMI: asks 256x240, gets 1920x1080 and streams; composite streams). Race found: right after S_INPUT 2 the
  decoder (reloaded after the HDMI capture powered it down) was not locked yet → "no signal" → default 720x480.
  **HDMI 1080p60 copies at ~47 fps** (4 MB/frame over the card's PCIe link) – hardware limit for full frames;
  Windows previews via the LQ scaler (960x540).
- Fixes (built, not installed): select_input waits up to 1 s for decoder lock; both chips always stay on while idle
  (powered down only for the other input's capture); "Input" menu control (custom id USER_BASE+0x1f00: Auto / HDMI
  / Composite, default = input= parameter) = persistent choice for apps that don't select inputs (PipeWire camera
  in OBS shows camera controls); V4L2 input 0 follows it ("Default (HDMI/Composite)" when fixed); applied at once
  when idle, else at stop_streaming (before a PipeWire renegotiation).
- Installed "Input control" build: composite via PipeWire still "no more input formats" (OBS log 13:16–13:18: Input
  changed, PipeWire never re-reads formats; libspa-v4l2 subscribes no V4L2 events). Composite itself works:
  real OBS + V4L2 source (tools/obs_live_test.py) 256x240, 532 frames, **analog audio in the recording (mean -27 dB)**.
- **OBS V4L2 source crash = OBS bug** (Debian obs-studio 30.2.3+dfsg-3): reproduced over obs-websocket
  (`tools/obs_ws_test.py`, CreateInput v4l2_input + property lists): SIGSEGV / "munmap_chunk(): invalid pointer"
  after the source restarts its capture; same "free(): double free detected" with NO video devices at all (private
  mount namespace without /dev/video*), and with our controls hidden (fake libv4l2 via LD_LIBRARY_PATH - OBS loads
  plugins with RTLD_DEEPBIND, LD_PRELOAD does not reach v4l2_ioctl).

### Two video devices (2026-11-05) – built, not installed
- One V4L2 device per input: "Matrox MXO2 Mini HDMI" (video0) and "Matrox MXO2 Mini Composite" (video1), each with a
  stable format list (PipeWire caches formats). struct mxo2_vid per device (queue, lock, format, scaler, controls:
  Sharpness on composite only); m->streaming = device owning the capture (cmpxchg in start_streaming, other device
  EBUSY); start_streaming switches the card to the device's input. m->sig[input] = detected signal per input:
  active input from rx/dec detection, the other probed while idle (hdmi_probe_locked / dec_probe_locked, both chips
  on). Composite device always lists its 4 sizes (NTSC, PAL after a 50 Hz signal). Auto input / Input control /
  S_INPUT switching removed (each device has input 0); input= still picks the idle input (audio follows it).
- Decoder 50/60 Hz table change needs two readings in a row (AD_RESULT flapped right after lock); select_input
  retries composite detection up to 1 s so captures never start without a format.
- Tests updated: run-v4l2-*.sh find their device by name; analog test switches devices; tools/mode_test.sh uses the
  Composite device; tools/obs_sim.py checks EBUSY on the other device while streaming.
- Two-device build installed (13:4x). User: VLC shows both, HDMI "does not work" = no HDMI signal since 13:17:50
  ("HDMI input gone"); OBS PipeWire showed one camera: WirePlumber names both nodes v4l2_input.pci-0000_02_00.0
  (node.name from the shared PCI path) and OBS keys cameras by node.name. Fix: `driver/51-mxo2.conf`
  (monitor.v4l2.rules matching api.v4l2.cap.card → node.name v4l2_input.mxo2-mini-hdmi / -composite), installed
  by install.sh to /etc/wireplumber/wireplumber.conf.d/ (+ wireplumber restart), removed by uninstall.sh; also
  copied to ~/.config/wireplumber/wireplumber.conf.d/ for testing.
- **Real OBS, PipeWire camera source switched by device** (`tools/obs_pw_switch_test.py`, obs-websocket
  SetInputSettings device_id): Composite 720x480@60 streaming → HDMI 1920x1080@30 streaming (no signal) →
  Composite streaming again, no format errors, OBS survived; screenshots show the live game before/after.
- Log showed AD_RESULT wrong for > 100 ms after a switch (PAL table loaded, 720x288p reported) and a transient
  480i reading → 50/60 Hz change must hold 400 ms; select_input waits for two equal readings 100 ms apart (≤ 2 s).
  Built, not yet installed.

### Release preparation (2026-11-05)
- User: VLC and OBS (PipeWire source) show both devices working; OBS V4L2 source works in the Flathub OBS.
- Repository layout: README.md, LICENSE (GPL-2.0), .gitignore (excludes original/, re/bin, re/decomp,
  re/ghidra-proj, re/notes data, build output), driver/, packaging/, re/ (NOTES.md, README.md, tools, run-*.sh,
  windows). EEPROM serial redacted. driver/test-load.sh moved to re/run-load-test.sh.
- driver/mxo2_compat.h: guards for kernels 6.1 .. current (asm vs linux unaligned.h < 6.12, PCI_IRQ_LEGACY and
  min_buffers_needed < 6.8, vb2 wait_prepare/finish only < 6.13). Built only against 6.12 here.
- ALSA card longname "Matrox MXO2 Mini audio (HDMI or analog)", PCM name "MXO2 Mini".
- Packaging (all DKMS, + WirePlumber rule in /usr/share/wireplumber/wireplumber.conf.d/): packaging/make-tarball.sh,
  debian/build-deb.sh (dpkg-deb; built and verified: module compiles from the packaged source), fedora/
  mxo2-dkms.spec + build-rpm.sh, arch/PKGBUILD (not built here: no rpmbuild/makepkg/containers).
- Project renamed **Open-MXO2-Mini** (folder, README, packages `open-mxo2-mini-dkms`, tarball
  `open-mxo2-mini-<version>.tar.gz`). Kept: kernel module / DKMS name `mxo2` (/usr/src/mxo2-<version>), ALSA card
  "MXO2 Mini", PipeWire node names v4l2_input.mxo2-mini-hdmi / -composite (existing OBS sources keep working).

### PAL composite (2026-11-05)
- Untested so far: field lines (driver assumes 288, no skip), decoder levels with the NTSC-tuned overrides, 256/320
  crop centre (864 samples/line, NTSC centre), audio 1920 samples per two fields.
- Built: `run-pal-probe.sh` → `tools/pal_probe.py` (ADV7181 mode 3 + overrides): decoder status/standard/interlace
  flag, field rate, rows written for a packed field and a woven frame, picture extent (content rows/columns) →
  `notes/pal/field.png`, `frame.png`. `analog_audio_probe.setup(mode)` now takes the decoder table.
- PAL probe 1 (`notes/pal-probe.txt`, PAL SNES): forced PAL table → AD_RESULT stays "NTSC M/J" (no autodetect with
  a forced standard - the driver's 50/60 Hz choice relies on it!), Status3 0x8d (bit 7 PAL swinging-burst lock,
  bit 2 50 Hz output), 243 lines per field, 75 fields/s, images = sync garbage (dotted diagonals).
  NTSC vs PAL Windows tables differ only in 0x00 (0x55 NTSC-M / 0x85 PAL-B/G forced), 0x17, 0x19 (fd/f1), 0x2e/0x30.
  VinControl code: Windows mode table says 0x11 = 576i, 0x10 = 480i (we always use 0x0d = 1080p50 code).
- Built: `run-pal-probe2.sh` → `tools/pal_probe2.py`: 0x00 ∈ {05 auto NTSC-J, 15 auto NTSC-M, 85 PAL, 65 PAL60,
  55 NTSC-M} × VinControl code {0x0d, 0x11}: AD_RESULT, Status1/3, field rate, rows, extent, colour, images
  `notes/pal/p2-<00>-<code>.png`.
- PAL probe 2 (`notes/pal-probe2.txt`, PAL SNES): AD_RESULT "NTSC M/J" for every 0x00, also autodetect 0x05/0x15 →
  **cause: our tables write 0x07 (autodetect enable mask) = 0x00** (all standards off). 75 fields/s and 243 rows in
  every combo. 0x85 (PAL): colour right (45), picture **sheared** diagonally (line length mismatch) – `p2-85-0d.png`;
  NTSC standards: no shear but no colour. VinControl code 0x11 makes no difference.
- Windows PAL composite run (`notes/win/pal/`, `regshadow.py notes/win/pal`) vs NTSC (`notes/win/analog/`):
  VinControl **stays 0x0d00**; VideoWDmaControl2 **0x01680111** (bit 4 new; VideoRDma1/2Control2 0x111 too),
  GenlockControl **0x1101** (0x1000), SystemClockReload **0xf988** (0x8f48), ClockLoadCount **0x42060** (0x37176),
  AudioW/RDmaControl 0xc8000**f00** (0xc84: 3840 vs 3204 → 1920 vs 1601.6 samples per frame), LqWDmaCrtc rows 0x240
  = 576, VideoWDmaControl 0x81000000 in preview. Also HDMIHsync/Vsync/HalfLine 0x850/0x854/0x858 = 0x00940016 /
  0x00060003 / 0x377 (NTSC 0x00a00024 / 0x00070004 / 0x37e; HDMI 0x00820056 / 0x000a0005 / 0x75e) – the driver never
  writes these for any input, so likely output timing. VAnalogVoutCRTC0/3 and HDMIProcAmpHueSat differ too (output).
- Built: `run-pal-probe3.sh` → `tools/pal_probe3.py`: part 1 autodetect with 0x07 = 0x7f (0x00 = 0x15 / 0x05);
  part 2 forced PAL with the Windows PAL values one at a time (ctl2 bit 4, genlock, clocks), ctl2+genlock, all
  (`win`), all + 0x850..0x858 (`win-sync`): fields/s, packed-field rows, extent, colour → `notes/pal/p3-<combo>.png`,
  woven frames `p3-win-frame.png` / `p3-win-sync-frame.png`.
- **PAL probe 3** (`notes/pal-probe3.txt`, PAL SNES): with **0x07 = 0x7f** autodetect (0x00 = 0x15 or 0x05) reads
  **PAL B/G/H/I/D** (S1 0x45). Forced PAL 0x85 + **VideoWDmaControl2 bit 4 (0x01680111) alone fixes it: 50 fields/s,
  288 rows per packed field, clean picture, colour right** (`p3-ctl2.png`); genlock / clocks / 0x850.. alone change
  nothing. Woven frame (Control 0x80/0x81…) = **574 rows** (287 per field, gaps 1), `p3-win-frame.png` clean.
  (run script's name pattern missed `p3-ctl2*` – fixed; converted by hand.)
- Driver (untested): `dec_load()` writes 0x07 = 0x7f and 0x00 = 0x1<insel> (autodetect, NTSC-M pedestal) after the
  tables, so detection sees 50 Hz and loads table 3; `cap_pal` (analog and in_vtotal 625) → Control2 | 0x10 and
  input_values column 2 (GenlockControl 0x1101, SystemClockReload 0xf988, ClockLoadCount 0x42060); no line skip
  for PAL; restart when cap_pal changes. Audio already does 20 ms blocks at 25/50 fps.
  Open: 576i woven frame has 574 card rows → last 2 delivered rows are stale.
- Test: `run-v4l2-pal-test.sh` → `notes/v4l2-pal-test.txt`, `notes/pal/v4l2-{288p,256,576i}.png` (last-rows luma
  printed for the 574/576 question). NTSC must be rechecked with `run-v4l2-analog-test.sh` (decoder now in autodetect).
- **PAL driver test passed** (`notes/v4l2-pal-test.txt`, PAL SNES): autodetect → "50 Hz detected, decoder mode 3",
  720x288p @ 50.01 fps, 256x288 @ 49.95, forced 576i 720x576 @ 25.00; 0 timeouts / no_buffer; v4l2-compliance
  57/57; `notes/pal/v4l2-576i.png` clean. 576i rows 574/575 (not written by the card) are border black here, so
  staleness not visible with this source.
- **NTSC recheck passed** (`notes/v4l2-analog-test.txt`, NES flash-cart menu – not bars, so bar levels not
  comparable): mode 0, 240p @ 60.10, 480i @ 30.05, comb 0.606, v4l2-compliance 57/57, picture clean. HDMI switch
  streamed 0 frames (no HDMI source connected?).
- PAL 256 wide was shifted left (cut left edge, black right): PAL picture sits further left in the line. SNES edges
  29.3 .. 677.0 (span 647.7 = 864 × 256/341 ✓) → centre 353.2. New param **`retro_center_pal` = 35320** (NTSC
  `retro_center` 36536), chosen in `build_taps()` by line length 864. Untested – recheck `notes/pal/v4l2-256.png`.
- PAL 256 wide after `retro_center_pal`: user reports PAL works. **But 288p jitters up/down slightly** (576i fine);
  not known whether it predates the autodetect change.
- Built: `tools/wobble.py` (per-frame vertical/horizontal offset vs frame 0 from row/column luma profiles;
  self-test finds a 1-row / 2-px shift), `tools/pal_probe4.py` (40 packed fields each with decoder auto 0x15+mask
  7f / forced 0x85 (0x65 at 60 Hz) mask 0 / forced + mask 7f; Status3 values seen), `run-pal-wobble-test.sh [tag]`
  (probe 4, then driver progressive + forced interlaced 100-frame streams → `notes/pal-wobble-<tag>.txt`,
  `notes/pal/wobble-<tag>-{prog,il}.png`). Tags: pal50, pal60.
- **PAL 60 / PAL M / NTSC 4.43** (driver, untested): AD_RESULT now gives line system and colour system separately:
  50 Hz = PAL B/G/H/I/D, SECAM, PAL N (625 timing); decoder table 3 for every PAL-colour standard incl. PAL 60 /
  PAL M, and SECAM; table 0 for NTSC M/J, NTSC 4.43, SECAM 525. PAL 60 → table 3 + 525 timing (240p/480i, NTSC line
  skip, NTSC retro centre). dmesg / debugfs rx print the standard name.
- **Wobble test** (`notes/pal-wobble-pal50.txt`, `-pal60.txt`): PAL 50 288p packed fields **alternate by exactly
  one line** (0,+1,0,+1 …) with every decoder setting (auto / forced 0x85 / forced + mask) and in the driver
  stream → not caused by autodetect. 576i steady. **PAL 60** (console 60 Hz switch): AD_RESULT "PAL 60" (S1 35),
  driver loads table 3, 240p @ 59.55 / 480i @ 29.77, 0 timeouts, no vertical movement, picture/colour fine
  (240p Test Suite). Its "horizontal 0,0,1,1" is a profile-match artefact: sub-pixel shift 0.00 px; frames differ
  by the subcarrier dot pattern (4-frame cycle), not by position.
- Hypothesis for 288p: ADV7181 PAL PVBEG 0xE8 = 0x65 has bit 6 (assumed DELE: V-bit begin one line later on even
  fields) set; NTSC NVBEG 0xE5 = 0x25 has no delay bits. ADV7181 datasheet download failed (analog.com timeout).
- Built: `run-pal-probe5.sh` → `tools/pal_probe5.py`: 0xE8 = 65/25/a5/e5, 0xEA = 63/23 → packed-field jitter.
- **PAL probe 5** (`notes/pal-probe5.txt`): table e8=65 → field offsets 0/-1 alternate; e8=25 mostly 0 (3 × -1);
  **e8=a5, e8=e5, ea=23 (with e8 25 or 65): 0 over all 40**. Caveat: probe grabs every ~2nd field with drifting
  parity, so "steady" can be sampling luck → verify through the driver's consecutive stream.
- Driver: param **`pal_prog_timing`** (0644, default 0xa563 = e8 a5 / ea 63; 0x6563 = table; -1 = untouched),
  written in `dec_detect_locked()` for PAL table + 50 Hz progressive only; 576i gets 0x6563 (table). `dec_e8ea`
  tracks what was written, reset by `dec_load()`.
- Test: `run-pal-timing-test.sh` → `notes/pal-timing-test.txt`: 288p with 6xxx/a563/e563/2523/6523/2563 at
  runtime (100 consecutive frames each, wobble.py), then 576i.
- **Timing test** (`notes/pal-timing-test.txt`, driver stream, 100 consecutive 288p frames each): **every**
  0xE8/0xEA setting (6563 a563 e563 2523 6523 2563) still alternates by one line (50/50); they only move the whole
  picture by a line. Probe 5's "steady" = sampling luck. 576i steady. → `pal_prog_timing` doesn't help; to be
  removed again once a fix exists. Plan: skip one line on every other field in the driver (cap_skip per frame),
  needs the field parity.
- Built: `run-pal-probe6.sh` → `tools/pal_probe6.py`: 80 consecutive packed fields (double buffer), parity from
  the picture, all side-effect-free readable Gaia registers snapshotted per field → bits following the parity,
  TS1 delta per parity, raw VinStatusAnalog / WDmaStatus / TS2 / Int0 / GenlockStatus / WDmaDebug.
- **PAL probe 6** (`notes/pal-probe6.txt`): 80 consecutive packed fields, parity strictly 0/+1 alternating, TS1
  +4 per field (no length difference). **InterruptStatus0 bit 24 (read right after the field) = field flag:
  100 %** (Int0 0x94810000 ↔ 0x95810000); bit 24 = 1 → picture one line higher (row 29), 0 → lower (row 30).
  (TS1/TS2 bit 2 and SystemClock bit 2 also match, but only because they count +4 per field.) For NTSC 240p the
  bit stays set (analog probe 4) – consistent with no NTSC wobble. VinStatusAnalog 0x360 (= 864 samples/line).
- Driver: `pal_prog_timing` removed again. New **`pal_field_fix`** (0644, default 1; -1 inverted, 0 off): in the
  progressive loop with `cap_pal`, Int0 bit 24 is read right after the field landed (before re-arming); bit 24 = 0
  → `slot_skip[slot]` = one line; copies use `mxo2_slot_offset()` (= slot base + cap_skip + slot_skip). The
  lower fields' last delivered row is the card's unwritten row 288 (stale; border black on consoles).
  Idea for later: the same flag may give the real field order for analog 576i/480i (now just alternating).
- Test: `run-pal-field-test.sh` → `notes/pal-field-test.txt`: 288p with pal_field_fix 1/0/-1 (runtime), 256 wide,
  576i.
- **Field-fix test** (`notes/pal-field-test.txt`): pal_field_fix 1 → **288p vertical 0 over 100 consecutive
  frames** (720 and 256 wide); 0 → 0/+1 (old jitter); -1 → 0/+2 (flag meaning confirmed). 576i steady.
  But shifted fields' last row = content (last content row 287 in half the frames) → stale garbage, not black.
- Driver: shifted fields repeat the line above as their last line (direct DMA path: memcpy in the vb2 buffer;
  bounce/scaled path: source row clamped to the last valid field line). Untested – rerun `run-pal-field-test.sh`:
  expect content rows (29, 252) in every 288p frame with fix 1.
- **Field-fix retest passed** (`notes/pal-field-test.txt`): fix 1 → content rows (29, 252) in all 100 frames at
  720 and 256 wide, vertical 0; fix 0 / -1 as before; 576i steady. User confirms PAL works.
- README: composite NTSC / PAL / PAL 60 auto-detected; params `retro_center_pal`, `pal_field_fix` listed.
- Open: Int0 bit 24 as real field order for analog 576i/480i; 576i rows 574/575 (card writes 574).

### Component (2026-11-05)
- User source: SD component only (480i / 576i); Windows dumps possible.
- Windows tables: 2 = 525 component, 5 = 625 component (0x00 = 0x00, 0x05 PrimaryMode 0, 0x06 = 0x0e / 0x0f,
  0x03 = 0x80 8-bit 656, 0x04 = 0x41), 6–8 1080i (29.97/30/25), 9–11 720p (59.94/60/50) with 0x05 = 1 and
  0x03 = 0x0c (wider output bus). "Modes" 12/13 = mask / or tables, so no 480p/576p table. Hidden script for
  2, 5–8: b7=1b only. FPGA: VinControlAnalog bit 16 for component.
- Built: `run-comp-probe.sh` → `tools/comp_probe.py`: tables 2 / 5 × VinControlAnalog c000…/c001…: decoder
  status, fields/s, packed-field rows, picture/colour → `notes/comp/c1-*.png`, woven frames `c1-{2,5}-frame.png`.
- **Component probe 1** (`notes/comp-probe.txt`, SNES 60 Hz component, 240p menu): table 2 locks, 60 fields/s,
  243 rows. **VinControlAnalog bit 16 needed**: with 0xc0010000 colours right + picture in place
  (`notes/comp/c1-2-c001.png`); 0xc0000000 → Cb/Cr swapped (blue → red) and picture ~72 px right. In component
  mode AD_RESULT / Status3 mean nothing (S1 4b "PAL", S3 2c with bit 2 = 50 Hz on a 60 Hz source; mask 0x07 = 0).
- **Windows dumps** (user, moved from `dumps/` to `notes/win/{component,svideo}_{ntsc,pal}`,
  `svideo_{ntsc_720x486,pal_720x576}`): vs composite only **VinControlAnalog 0xc0010000 for component**; S-video
  = composite on the FPGA side. Standard-dependent values as for composite (Control2 bit 4, genlock 0x1101, clocks).
  SystemClockReload differs per session (8f48, 4a060, 6b698, 8f688, 7c420, 38ae0) → computed, not important.
  The "720x486/576" recording setting only changes Windows' Lq scaler / WDMA packing (Control 0x82000000,
  Control2 0x101, LqWDmaCrtc 0xf3 = 243 / 0x120 = 288 rows per field) – recording path, not the input.
- Driver (untested): **four video devices** (user choice: one per input, OBS PipeWire can't pick V4L2 inputs):
  HDMI, Composite, S-Video, Component (`mxo2_input_names`, WirePlumber names `…-svideo`, `…-component`).
  Decoder table = input - 1 (+3 for 625 lines); `dec_load()` uses `adv7181_modes[0..5]` (header now has all six
  SD tables), hidden script SD or b7=1b, composite overrides / 0x17 sharpness only for composite; autodetect
  mask 0x07 = 0x7f for all. Component: table follows the line system (pal), not the colour. Capture writes
  VinControlAnalog 0xc0010000 for component. 240p / 256 / 320 formats for every analog input. Auto input picks
  HDMI, else the decoder's current input (composite while idle). Module param `input` 0–3.
- Test: `run-v4l2-input-test.sh svideo|component|composite [tag]` → `notes/v4l2-<input>[-tag]-test.txt`,
  `notes/inputs/*.png` (levels printed). Open: component 50/60 detection (AD_RESULT with mask 0x7f untested in
  component mode; fallback: field rate), component/S-video levels.

### One video device, inputs via S_INPUT (2026-11-10) – built (7.3 headers), not installed
- User (Fedora 44, kernel 7.3, PipeWire 1.6.11, WirePlumber 0.5.19): driver works, "PipeWire no picture"; wants one
  device with the inputs on the input selector. Not reproducible from the sandbox (no PipeWire socket). Direct
  V4L2 at that time: Composite device streamed (240p console), **HDMI device no signal → a stream there never
  gets a frame**. With four devices, PipeWire apps that take the first camera got "MXO2 Mini HDMI" (lowest
  /dev number = highest WirePlumber priority.session) → black. Likely the "no picture"; to be confirmed with
  `run-pipewire-test.sh`.
- Driver: one device **"Matrox MXO2 Mini"** (querycap card + vdev name); `m->vid` single; V4L2 input n =
  MXO2_INPUT_n (HDMI, Composite, S-Video, Component), G_INPUT = m->input. S_INPUT switches at once
  (`mxo2_switch_input`), EBUSY while vb2 buffers exist and the input differs; locks the choice until the last
  handle closes (`mxo2_release`, v4l2_fh_is_singular_file). **"Input" menu control** back (MXO2_CID_INPUT =
  USER_BASE+0x1f00: Auto / HDMI / Composite / S-Video / Component, default = `input=`) for PipeWire apps: sets
  input_pref, clears the S_INPUT lock, switches now if idle (`mxo2_auto_input_now`), else the monitor applies it
  after the stream stops. Format list follows m->input; HDMI keeps its last size (hdmi_width/height, 1080p at
  first) without a signal; `fmt_input` = input the format was made for, start_streaming switches back to it if
  Auto moved between S_FMT and REQBUFS. Sharpness control on the one device (acts on composite only).
  ENUMINPUT status only for the selected input (others: decoder table not loaded → unknown).
- WirePlumber rule: one entry, card "Matrox MXO2 Mini" → node.name `v4l2_input.mxo2-mini`. Old OBS PipeWire
  sources (…-hdmi / …-composite) must be re-pointed.
- Tests: all run-*.sh / mode_test.sh find "Matrox MXO2 Mini" and set the Input control (HDMI tests 1, composite
  tests 2, input test IDX+1); analog test switches HDMI ↔ composite via the control and `-i 1`; obs_sim.py
  checks S_INPUT EBUSY + second-handle REQBUFS while streaming. New `run-pipewire-test.sh` (as user):
  node + EnumFormat from pw-dump, per input 60 frames through GStreamer pipewiresrc + PNG `notes/pw/<input>.png`,
  WirePlumber journal.
- Build check here: host headers /run/host/usr/src/kernels/7.3.3-200.fc44.x86_64 with the sandbox gcc 15 needs
  `KCFLAGS="-include ccfix.h"` (redefines `__counted_by_ptr` empty; gcc 16 feature). Compile check only.
- **PipeWire test** (`notes/pipewire-test.txt`, single-device build installed): node "Matrox MXO2 Mini" created
  (formats cached from composite), but every pipewiresrc stream failed ("target not found"); journal:
  `spa.v4l2: '/dev/video2' mmap: Invalid argument`. Cause (PipeWire 1.6 v4l2-utils.c `userptr_init`): when the
  consumer brings its own buffers, PipeWire asks for **USERPTR** (driver: EINVAL), falls back to MMAP and maps
  them **PROT_READ + MAP_PRIVATE** → vb2_mmap needs VM_SHARED → EINVAL. v4l2-ctl / OBS's V4L2 source map shared
  → unaffected. Reproduced as user: `tools/mmap_check.py` (shared ok, private EINVAL, dmabuf ok).
- Fix: `mxo2_mmap` turns a read-only private mapping into a shared one without VM_MAYWRITE (can never be written,
  so no difference). USERPTR not added: the card's 32-bit DMA mask means swiotlb bouncing without an IOMMU, and
  vb2's finish sync would then overwrite our CPU-written frames (chroma fix, notch, 256/320 scaling, line
  doubling). Built, not installed. Check: `python3 -I tools/mmap_check.py /dev/videoN` → RESULT OK, then
  `run-pipewire-test.sh`.
- **PipeWire test passed** (mmap fix installed; `tools/mmap_check.py` RESULT OK): pictures through pipewiresrc for
  Auto (composite, 720x480), Composite (NES, clean) and HDMI (camera 1080p). HDMI colours: magenta/posterized
  highlights = the known AD9398 4:2:2 7-bit chroma fault ("Camera at 1080p30"); direct v4l2-ctl capture identical,
  chroma LUT applied (all chroma bytes odd) → not a PipeWire issue. Test script: the 60-frame run used fakesink
  without caps → "target not found" (no media class to link); now `! video/x-raw !`.
- PipeWire test rerun: 60 frames in 1.1 s (composite / Auto) and 1.5 s (HDMI 1080p60, ~47 fps limit), pictures OK.
- User: **720x240 / 720x288 removed**. Analog sizes now 720x480/576 (woven or line-doubled) + 320/256 x 240/288
  (scaled). Any other request (old saved 720x240) → 720 x full height, also the default for 240p (was 720x240).
  run-v4l2-retro-test.sh: 256 / 320 only. PAL field/wobble tests' "288p" stream is now 720x576 line-doubled
  (row offsets count double). Built, not installed.

## Video outputs (2026-11-10) – research
- Box outputs = same connectors as the inputs: HDMI OUT via **AD9889B** HDMI transmitter (I2C 0x72, EDID memory
  0x7e, packet memory 0x70), analog OUT (composite / S-video / component) via **ADV7341** encoder (I2C 0x54).
  On Linux both sit at power-on defaults (i2c-dump: AD9889B 0x41 = 0x50 = powered down, ADV7341 0x00 = 0x12 =
  DACs off) → outputs dark.
- FPGA output path (Gaia regs): VideoRDma1/2 (read on-board frames), Video1VH* (playback timing, unused by
  Windows), VoutControl 0x800, VoutControlHDMI 0x804, VAnalogVoutControl/CRTC0-3 0x810-0x820, BramLut* (LUT),
  HDMIProcAmp* (gain/hue/sat), Vout1/2ClippersControl, HDMIHsync/Vsync/HalfLine 0x850-0x858, AudioRDma 0x280.
- **Windows mirrors the input to the outputs** (shadow dumps `notes/win*`): during preview VideoRDma2 reads the
  same on-board ring the capture writes (VideoRDma2Address = ring slot, ring stride 0x3f4800 = ours), AudioRDma
  runs too. Output timing follows the input format:
  | input | VoutControl | VoutHDMI | HDMIHsync | HDMIVsync | HalfLine | VAnalogCRTC0..3 | RDma2Control |
  |---|---|---|---|---|---|---|---|
  | idle (no app) | 1101 | 2 | 00a00024 | 00070004 | 37e | 40 40 0 44030000 | 20000000 |
  | HDMI 1080p30 | 1303 | 4 | 00820056 | 000a0005 | 75e | 08980028 2c 840 05050084 | a2000000 |
  | NTSC comp/S-video | 1303 | 2 | 00a00024 | 00070004 | 37e | 40 40 0 44030000 | a0/a1000168 |
  | PAL comp/component | 1303 | 2 | 00940016 | 00060003 | 377 | 30 40 0 33020000 | a0/a1000168 |
  Also BramLutR/G/B 201fffff/1fffff/1fffff, Vout1/2Clippers 3, HDMIProcAmpHueSat differs per standard.
  RDma2Control 0x168 = 360 dwords = 720 px YUYV pitch; bit 24 toggles (field?).
- Chip setup is not static in MVKAVCIO: every chip register is an object (FUN_000150b8: +0x10 value, +0x14 name,
  +0x38 address, +0x3c flags, +0x40 pending), set through virtual calls from the output classes. Board object
  (driver class, FUN_00043dc4, stored in the driver object extension; init FUN_000458c4) holds chip pointers:
  +0x2d8 ADV7181, +0x338 AD9889B (vtable VA 0x825e0), +0x340 AD9398, +0x358 ADV7341 (vtable 0x826a0),
  +0x370 AD9889B packet memory. Ghidra VAs = image base 0x10000 + RVA.
- Built: `windows/mxo2-output.cmd` + `mxo2-chips.wds` (kd, like mxo2-regs): per step (idle, HDMI preview,
  composite preview, playback) the Gaia shadows + every chip register value + a note what the TV shows →
  `windows/output/*.txt` → copy to `notes/win/output/`. Untested (no Windows here); the driver-object path
  (DriverExtension +0x30 → ClientDriverExtension +0x28 → data +0x10) is printed step by step for fixing.
- Possible features: (1) loop-through monitor of the selected input on HDMI + analog OUT (FPGA side known from
  the table above, chip values from the Windows dump); (2) V4L2 output device = play video from Linux (needs
  host→card PciDma direction + free-running output timing; MVKOUTPUT.sys / MVKTRANSFER.sys not decompiled yet;
  Ghidra not installed on this machine); (3) analog encoder colour bars as a first sign of life (ADV734x has an
  SD test-pattern generator; bit not confirmed – datasheets from analog.com blocked here).
- **Chip tables found statically** (no Windows run needed for the chips): AD9889B DAT_0007a0f0, 12 modes × 0xb3
  {index, value}, loader FUN_000194cc (writable only, in order), mode by format FUN_00019640 (0 init, 1 480i,
  2 480p, 3 576i, 4 576p, 5 1080i50, 6 1080i60, 7 720p50, 8 720p60, 9 1080p24, 10 1080p25, 11 1080p30 – no
  1080p50/60). ADV7341 DAT_00076e50, 24 modes × 0x87 u32, loader FUN_0001d698 (+ PWR_MODE 0xfc after modes 0/6),
  mode FUN_0001d81c with user props +0xf4 (prop 0x22, NTSC pedestal?), +0xfc (prop 0x66, analog output type,
  default 3), +0xf8 = 2: 480 → 1, 576 → 7, 720p50/60 → 0x13/0x11, 1080i60/50 → 0xb/0xd, 1080p → 0x17; then
  EEPROM DAC calibration (FUN_0001d970 / FUN_0001dbb0, skipped for now). AD9889B helpers: FUN_000195b4 = power
  by HPD (0x42 bit 6 → 0x41 bit 6), FUN_000197e8 = YCbCr (3) / RGB output (0x3b CSC, 0x16[7:6], AVI 0x45),
  FUN_00019730 = audio channels (0x50/0x51), FUN_00019564 = re-write 0x96 (clear interrupts).
  chipregs.py misses AD9889B idx 0x11/0x12 (regs 0x11/0x12) and ADV7341 idx 0x13 (reg 0x17 SOFT_RST), all
  FUN_00019a24 objects. `tools/output_tables.py` → `notes/AD9889B.modes.txt`, `notes/ADV7341.modes.txt`,
  `tools/output_modes.py`.
- Built: `run-vout-probe.sh` → `tools/vout_probe.py` (interactive: asks what the TV shows per step) →
  `notes/vout-probe.txt`. NTSC composite: chip tables (ENC 0→1, TX 0→1, HPD power), FPGA output regs from
  win/analog/2-preview, then a woven frame grabbed to 0x400000 shown via VideoRDma2 (Control a0000168, once /
  re-armed per field with bit 24 alternating), then live (3 slots, output = newest). Logs Video2Crc16 and
  Video2DebugRDmaStatus per step.
- Plan (user): pass-through by default, output from Linux optional (V4L2 output device).
- User has Secure Boot on → kernel lockdown blocks user-space BAR access (probes need SB off). So the output
  test moved into the driver (also the goal: pass-through by default).
- **Driver pass-through** (`driver/mxo2_output.c`, built against 7.3, untested on hardware): module param
  `output` (default 1). New capture user MXO2_USER_OUTPUT, taken by `mxo2_output_poll()` (rx_work, 500 ms) while
  the selected input has a signal the outputs can show (analog: any; HDMI: 1920x1080p30 only); output/audio-only
  captures restart on format change (cap_mode_stale) and keep both input chips powered (Auto keeps working;
  thread_start(video) powers the other chip down only for V4L2/debugfs). thread_start → `mxo2_output_start()`:
  ADV7341 mode 0 + N, AD9889B mode 0 + N (driver/output_modes.h, generated by output_tables.py), HPD power,
  FPGA out_regs per format (NTSC / PAL / 1080p30 from the Windows previews); thread_stop → RDma2 off. Capture
  loops call `mxo2_output_frame()` after every frame / field: RDma2Address = slot, RDma2Control = 0x20000000 |
  capture control (0x82 progressive, 0x80/0x81 field) | analog pitch (0x168) → HDMI 1080p a2000000 and analog
  480i a0/a1000168 as Windows; 240p packed fields give a2000168 (guess). `output_rdma` param overrides the
  control word for experiments. Monitor follows HDMI OUT hot plug (reload TX table on plug-in). debugfs status
  shows "outputs". mxo2_remove now cancels the monitor before stop_all.
- **Output probe 1** (`notes/vout-probe.txt`, NTSC 240p console, Secure Boot now off): **picture on both
  outputs** – still frame and live (226 frames / 15 s, polled). HDMI OUT: colour, **green tint**; composite OUT:
  **no colour**. Step 1 (RDma2Control 0x20000000) showed a distorted picture → 0x20000000 does not stop the read
  (old card memory shown). AD9889B HPD + rx sense seen (0x42 = e0). Chips were already set by the installed
  pass-through driver before the probe.
- Green tint: mode tables leave the TX in YCbCr out (0x16 = c4, CSC 0x3b bit 0 off) with AVI 0x45 = 0x18 (Y =
  RGB) → TV decodes YCbCr as RGB. Windows FUN_000197e8 (RGB) fixes 0x3b/0x16/0x45 → driver now applies it
  (tx_rgb) after the TX table and on hot plug. No colour on composite: guess = NTSC mode 1 is a YPbPr variant /
  DACs 4-6 off (0x00 = 0x1c; Windows uses 0xfc after mode 0); or a PAL TV without NTSC 3.58 colour.
- Built: `run-vout-probe2.sh` → `tools/vout_probe2.py` → `notes/vout-probe2.txt`: HDMI RGB vs YCbCr (FUN_000197e8
  both ways), encoder NTSC modes 0/1/2 with all DACs on; asks per step, keeps the best analog mode.
- **Output probe 2** (`notes/vout-probe2.txt`): HDMI **RGB (FUN_000197e8 RGB branch) = correct colours**; YCbCr
  branch (= the table state, 0x16 c4 / 0x3b 8a / 0x45 18) green tint. Encoder with 0x00 = 0xfc: **mode 0 =
  composite in colour**, modes 1 and 2 no colour.
- Datasheet ADV7342/ADV7343 Rev. E now in `original/ADV7342_7343.pdf` (same register map family). 0x84 SD Mode 4:
  bit 7 luma/chroma swap, **6 colour bars**, **5 burst disable**, 4 chroma disable, 3 active length, 2:1 SFL.
  Table 37 (SD output routing by 0x02 bit 5 + 0x82 bits 2:1): mode 0 (0x02 20, 0x82 4a) = DAC1 CVBS, DAC2/3 Y/C,
  DAC4-6 YPbPr; mode 1 (0x02 28, 0x82 49, 0x84 21) = DAC1-3 YPbPr, burst off, only DAC1-3 on (0x00 1c) →
  composite jack = DAC1 shows Y only. So Windows "analog output type": modes 0/6 = composite + S-video (Windows
  sets 0x00 = 0xfc after them), 1/7 = component. Driver: `output_analog` 0 (default, modes 0/6 + 0xfc) or 1
  (1/7); HD always component (23 for 1080p). HDMI: tx_rgb confirmed.
- **Driver pass-through works** (installed build with tx_rgb + output_analog 0): user: both outputs (HDMI OUT,
  composite OUT) show the NTSC 240p console in colour, no app open. Not yet checked: pass-through while
  capturing (OBS / 256x240), PAL, S-video/component inputs, HDMI 1080p30 in, S-video / component OUT.
- Test hygiene (user: card still in use after tests): `reload-driver.sh` now cleans up on every exit (also errors,
  Ctrl-C): if the test unloaded or replaced the driver, remaining holders are ended and the installed (DKMS)
  driver is modprobe'd again, then PipeWire restarted for every user it was stopped for (state in
  /run/mxo2-test.<pid>, since test steps run in `{ } | tee` subshells). Scripts' `trap … INT` now exit.
  load_driver rebuilds driver/mxo2.ko when a source is newer (it was a stale Nov-5 four-device build).
  run-load-test.sh uses it too; run-pipewire-test.sh ends leftover gst streams and resets Input to Auto on exit.
  New `mxo2-who.sh [--close]`: lists (and ends) programs using the MXO2.
- After the test-cleanup change: VLC needed /dev/video0 (MXO2 moved from video2; webcam gone), OBS (Flathub, logs
  not visible from the sandbox) worked after re-selecting the device "Matrox MXO2 Mini" + resolution in the
  source (old sources pointed at the per-input devices / 720x240). Driver capture verified here: 30 frames 720x480
  in 0.5 s, NES menu picture clean, with pass-through running.

## Video output from Linux (2026-11-10) – built, not installed
- Host → card DMA = the capture's PCI DMA with Command bit 29 (0x20000000, FUN_0003f800 direction 1); same SG
  list format, PitchRes width = dwords per line. Driver: `mxo2_dma(…, to_card)` (mxo2_dma_to_host wraps it).
- V4L2 output device **"Matrox MXO2 Mini Output"** (`driver/mxo2_play.c`, VFL_DIR_TX, MMAP/DMABUF/write(),
  YUYV): 720x480 NTSC 29.97 (woven, field INTERLACED), 720x576 PAL 25, 1920x1080 30p – the formats with known
  output settings (mxo2_output_setup(f), refactored out of the pass-through start). Thread "mxo2-play": next
  queued buffer → PCI DMA into output slot k (card 0x1000000 + k·0x3f4800, 3 slots; NTSC 4 lines down to match
  the capture layout) → buffer done → wait for the frame tick (hrtimer, fps of the format) → RDma2Address = slot,
  Control 0xa0000168 (SD, written once per frame – probe 1 showed a still frame with one write) / 0xa2000000
  (1080p). No new buffer: the last frame stays.
- mxo2_play_claim(): drops the pass-through user; EBUSY if any capture thread still runs (V4L2, audio) – one DMA
  engine, one output timing; sets genlock/clock (set_timing: input_values column 1 NTSC, 2 PAL, 0 HDMI – the
  idle/free-run behaviour of these values without an input is untested), bus master on. capture_get() → EBUSY
  while playing. Release: pass-through returns at the next monitor round.
- Open: field alternation for 480i/576i (bit 24 per field, needs an output field signal), whether 1080p30 timing
  free-runs without an HDMI input (genlock 0x401), audio out (AudioRDma), pacing vs the card's real output rate.
- Test: `run-play-test.sh [ntsc] [pal] [1080p]` (as user): v4l2-ctl test patterns 10 s per format + ffmpeg
  testsrc2, asks what the TV showed → `notes/play-test.txt`.
- **Play test 1** (`notes/play-test.txt`): **NTSC and PAL playback work** (moving v4l2-ctl pattern on the TV,
  299/250 frames in 10.1 s = exact pacing). **1080p30: nothing on screen** although 297 frames were shown by the
  driver – likely genlock 0x401 (HDMI-input lock) without an HDMI input. ffmpeg: never started – the script ran it
  under `timeout` without --foreground, ffmpeg reads the tty for keys → SIGTTIN, stopped; sandbox ffmpeg 7 with
  -nostdin plays 30 frames fine (Fedora's libavdevice 62 has the v4l2 outdev too). Script: ffmpeg -nostdin;
  1080p now tried with play_genlock 0 (auto 0x401) / 0x1000 / 0x400 / 0x1 (new module param, applied after the
  timing values at playback start).
- **Play test 2**: 1080p30 with play_genlock 0x401 / 0x1000 / 0x400 / 0x1: TV **"not supported"** every time
  (a signal, wrong timing) → genlock is not it. Likely: no 74.25 MHz pixel clock without an HDMI input (with
  HDMI in, the receiver's clock probably drives the video timing; SD works because the decoder's 27 MHz runs).
  VoutControl was 0x1303 in every Windows preview (code 0x13 = 576p in the input code table?) – meaning unclear.
  FUN_0001e778 = generic board I/O object; register annotations there unreliable. **ffmpeg -nostdin works**
  (moving testsrc2 on HDMI). Needed: Windows dump during HD timeline playback (windows/mxo2-output.cmd step 4).
  Driver: 1080p hidden unless `play_hd=1`.
