# Open-MXO2-Mini

An open, unofficial, reverse-engineered Linux kernel driver for the **Matrox MXO2 Mini** capture box with its PCIe
adapter card. Matrox only ever shipped Windows and macOS drivers. This driver turns the box into standard Linux
video and audio devices (V4L2 + ALSA) that work with OBS Studio, VLC, ffmpeg, PipeWire and anything else that
speaks V4L2.

> Not affiliated with or endorsed by Matrox. Use at your own risk.

## Supported hardware

| | |
|---|---|
| PCIe card | Matrox "Gaia" FPGA, PCI ID `102b:8350`, subsystem `102b:9000` |
| Box | MXO2 Mini (HDMI receiver AD9398, video decoder ADV7181) |
| Tested on | Debian 13, kernel 6.12, OBS (Debian 30.2 and Flathub), VLC 3.0, PipeWire 1.4 |

## What works

| Input | Formats (YUYV 4:2:2) | Notes |
|---|---|---|
| **HDMI** | 1080p 24/25/30/50/60, 1080i 50/60, 720p 50/60, 576p (480p, 576i, 480i implemented, untested) | RGB and YCbCr sources, 16:9/4:3 pixel aspect for SD |
| **Composite** | 720×480/576 (480i/576i woven, or 240p line-doubled), 720×240/288, **320×240** and **256×240** | Retro consoles' 240p supported. 256/320 are sampled on the console's pixel grid (NES/SNES/Famicom 341 dots per line, Mega Drive/PlayStation 420) |
| **Audio** | 48 kHz stereo | HDMI audio, or the analog audio inputs together with composite |

- Two capture devices, one per input: **"Matrox MXO2 Mini HDMI"** (usually `/dev/video0`) and **"Matrox MXO2 Mini
  Composite"** (`/dev/video1`). Only one of them can capture at a time. Starting one switches the box to that input.
- One ALSA capture device **"MXO2 Mini"** (PipeWire: "MXO2 Mini Stereo"). It follows the input that is capturing.
  Without a signal it delivers silence instead of stalling.
- Composite has a **Sharpness** control. 2 = sharpest (default; faint composite dot texture on colours), 1 = sharp
  with the colour-subcarrier dots filtered out, 0 = soft.
- System suspend/resume, MSI interrupts, v4l2-compliance clean.

### Known limitations

- HDMI 1080p **60** fps reaches about 47 fps. A full frame (4 MB) at 60 fps is more than the card's PCIe link moves.
  1080p30, 1080i, 720p and all SD/composite modes run at full rate.
- Not supported: S-video and component inputs, the outputs, the hardware scaler, the H.264 encoder (MXO2 Mini MAX).
  PAL composite and real 480i/576i composite sources are implemented but barely tested.
- Composite field order (480i/576i) is not detected. Use `analog_field_swap=1` if motion combs.

## Installation

Packages build the module with **DKMS** for every installed kernel. They also install a WirePlumber rule that gives
the two cameras distinct PipeWire names (without it, OBS's PipeWire source shows only one of them).

### Debian / Ubuntu

```sh
sh packaging/debian/build-deb.sh            # -> dist/open-mxo2-mini-dkms_<version>-1_all.deb
sudo apt install ./dist/open-mxo2-mini-dkms_*_all.deb linux-headers-$(uname -r)
```

### Fedora

```sh
sudo dnf install rpm-build dkms kernel-devel
sh packaging/fedora/build-rpm.sh            # -> dist/open-mxo2-mini-dkms-<version>-1.<dist>.noarch.rpm
sudo dnf install ./dist/open-mxo2-mini-dkms-*.noarch.rpm
```

### Arch Linux

```sh
cd packaging/arch && sh ../make-tarball.sh . && makepkg -si
```

(`linux-headers` or the headers of your kernel must be installed.)

### Any distribution, without a package

```sh
sudo sh driver/install.sh      # DKMS install + WirePlumber rule; uninstall: sudo sh driver/uninstall.sh
```

### Secure Boot

With Secure Boot on, the kernel only loads signed modules. Debian/Ubuntu's DKMS signs modules with a machine owner
key (MOK) and asks you to enrol it on the next boot. On Fedora, set up `mokutil` signing for DKMS, or turn off Secure
Boot.

## Usage

- **OBS Studio**: add **Video Capture Device (PipeWire)** and pick "Matrox MXO2 Mini HDMI" or "... Composite". To
  switch inputs, change the device in the source's properties. For sound, add **Audio Input Capture
  (PulseAudio/PipeWire)** with "MXO2 Mini Stereo". The **Video Capture Device (V4L2)** source works in the Flathub
  OBS. Debian's OBS 30.2.3 crashes in that source by itself, even with no capture device at all.
- **VLC**: `vlc v4l2:///dev/video1` (composite) or `v4l2:///dev/video0` (HDMI), plus `:input-slave=alsa://hw:Mini`
  for audio.
- **ffmpeg**: `ffmpeg -f v4l2 -video_size 256x240 -i /dev/video1 -f alsa -i hw:Mini out.mkv`
- **v4l2-ctl**: `v4l2-ctl -d /dev/video1 --list-framesizes=YUYV`, `v4l2-ctl -d /dev/video1 -c sharpness=1`

Retro consoles: 256×240 for NES/Famicom/SNES, 320×240 for Mega Drive/PlayStation, 720×480 for a line-doubled
progressive picture. Pixel aspect is reported (≈ 8:7 at 256), so scale the source to 4:3 in OBS.

### Module parameters

Set them with `options mxo2 ...` in `/etc/modprobe.d/mxo2.conf`. The ones you may need:

| Parameter | Default | Meaning |
|---|---|---|
| `retro_center` | 36536 | Composite 256/320: picture centre in 1/100 decoder samples (shifts the pixel grid) |
| `retro_dots256`, `retro_dots320` | 341, 420 | Console dots per line for the 256/320 formats |
| `composite_interlaced` | -1 | -1 auto, 0 force 240p/288p, 1 force 480i/576i |
| `analog_field_swap` | 0 | Composite 480i/576i field order |
| `input` | -1 | Input while no device captures (audio follows it): -1 auto, 0 HDMI, 1 composite |
| `use_irq` | 1 | 0 poll, 1 MSI, 2 legacy INTx |

The rest (`rx_mode`, `vin_code`, `int_enable`, `chroma_fix`, `audio_clock`, `audio_pll`, `allow_write`) are for
debugging; see `modinfo mxo2`. Status and register access for debugging: `/sys/kernel/debug/mxo2/*/`.

## Repository layout

| Path | |
|---|---|
| `driver/` | Kernel module source, `dkms.conf`, WirePlumber rule, `install.sh` / `uninstall.sh` |
| `packaging/` | Debian, Fedora and Arch packaging (all DKMS; package `open-mxo2-mini-dkms`, kernel module `mxo2`) |
| `re/` | Reverse-engineering notes and tools: [`re/NOTES.md`](re/NOTES.md) is the full research log (registers, chip setup, every probe and its result). `re/tools/` holds the Python probes and test tools, `re/run-*.sh` the hardware test scripts, `re/windows/` the Windows-side register capture scripts |

Matrox's software, the binaries the research is based on, and the raw capture data are not part of this
repository (see `.gitignore`).

## License

GPL-2.0 (see `LICENSE`).
