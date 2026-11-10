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
| Builds on | Fedora 44, kernel 7.2 (GCC 16) |

## What works

| Input | Formats (YUYV 4:2:2) | Notes |
|---|---|---|
| **HDMI** | 1080p 24/25/30/50/60, 1080i 50/60, 720p 50/60, 576p (480p, 576i, 480i implemented, untested) | RGB and YCbCr sources, 16:9/4:3 pixel aspect for SD |
| **Composite** | 720×480/576 (480i/576i woven, or 240p line-doubled), **320×240/288** and **256×240/288** | Retro consoles' 240p supported. 256/320 are sampled on the console's pixel grid (NES/SNES/Famicom 341 dots per line, Mega Drive/PlayStation 420) |
| **Audio** | 48 kHz stereo | HDMI audio, or the analog audio inputs together with composite |

- One capture device **"Matrox MXO2 Mini"** for all inputs. Its V4L2 inputs are 0 HDMI, 1 Composite, 2 S-Video and
  3 Component (the input selector in OBS's V4L2 source, VLC, `v4l2-ctl -i`). Apps that can't select inputs
  (PipeWire) use the **Input** control: Auto (default: the input with a signal, HDMI first) or one fixed input.
  The format list follows the selected input. The input can't change while the device streams.
- One ALSA capture device **"MXO2 Mini"** (PipeWire: "MXO2 Mini Stereo"). It follows the input that is capturing.
  Without a signal it delivers silence instead of stalling.
- **Video outputs** (experimental): HDMI OUT and the analog outputs show the selected input (pass-through), also
  while no app captures. Works for NTSC / PAL analog inputs and HDMI 1080p30 so far; other formats leave the
  outputs dark. `output=0` turns it off.
- **Video output from Linux** (experimental): a second V4L2 device **"Matrox MXO2 Mini Output"** plays video
  to HDMI OUT and the analog OUT: YUYV 720×480 (NTSC) and 720×576 (PAL) (1080p: not working yet). While it plays,
  pass-through and capture are off. Example: `ffmpeg -re -i clip.mp4 -vf scale=720:480 -pix_fmt yuyv422 -r
  30000/1001 -f v4l2 /dev/videoN` (`v4l2-ctl --list-devices` shows N).
- **Sharpness** control (composite only). 2 = sharpest (default; faint composite dot texture on colours), 1 = sharp
  with the colour-subcarrier dots filtered out, 0 = soft.
- System suspend/resume, MSI interrupts, v4l2-compliance clean.

### Known limitations

- HDMI 1080p **60** fps reaches about 47 fps. A full frame (4 MB) at 60 fps is more than the card's PCIe link moves.
  1080p30, 1080i, 720p and all SD/composite modes run at full rate.
- S-video and component (SD) are new and barely tested. The video outputs are experimental (pass-through only).
  Not supported: the hardware scaler, the H.264 encoder (MXO2 Mini MAX).
  Composite NTSC, PAL and PAL 60 are detected automatically (tested with NES, SNES and a 50/60 Hz-switchable PAL
  console); real interlaced 480i/576i sources are implemented but barely tested.
- Composite field order (480i/576i) is not detected. Use `analog_field_swap=1` if motion combs.

## Installation

Packages build the module with **DKMS** for every installed kernel. They also install a WirePlumber rule that gives
the camera a stable PipeWire name (`v4l2_input.mxo2-mini`, "Matrox MXO2 Mini").

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

Fedora has Secure Boot on by default: enrol the DKMS key once (see [Secure Boot](#secure-boot)). The test tools used
below are in `v4l-utils` and `alsa-utils` (`sudo dnf install v4l-utils alsa-utils`).

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

With Secure Boot on, the kernel only loads signed modules. DKMS signs the module with its own key
(`/var/lib/dkms/mok.key`, created on the first install; Debian, Ubuntu and Fedora alike). Enrol that key once:

```sh
sudo mokutil --import /var/lib/dkms/mok.pub    # choose a one-time password
```

Reboot, pick **Enroll MOK** in the blue MOK manager screen and enter the password. After that the driver loads on
every boot (`mokutil --test-key /var/lib/dkms/mok.pub` shows whether the key is enrolled). Ubuntu uses
`/var/lib/shim-signed/mok/MOK.der` instead and asks for the enrolment itself during the install.

## Usage

The examples use `/dev/video0`; `v4l2-ctl --list-devices` shows the number of "Matrox MXO2 Mini".

- **OBS Studio**: **Video Capture Device (V4L2)** source: pick "Matrox MXO2 Mini" and the input in **Input**.
  **Video Capture Device (PipeWire)** source: pick "Matrox MXO2 Mini"; choose the input with the **Input** camera
  control (or leave it on Auto), then reselect the resolution, as PipeWire reads the format list only when the
  source starts. For sound, add **Audio Input Capture (PulseAudio/PipeWire)** with "MXO2 Mini Stereo". Debian's
  OBS 30.2.3 crashes in the V4L2 source by itself, even with no capture device at all; the Flathub OBS works.
- **VLC**: `vlc v4l2:///dev/video0:input=1` (composite; 0 HDMI, 2 S-video, 3 component), plus
  `:input-slave=alsa://hw:Mini` for audio.
- **ffmpeg**: `v4l2-ctl -d /dev/video0 -c input=2` (Input control: Composite), then
  `ffmpeg -f v4l2 -video_size 256x240 -i /dev/video0 -f alsa -i hw:Mini out.mkv`
- **v4l2-ctl**: `v4l2-ctl -d /dev/video0 --list-inputs`, `-c input=2` (0 Auto, 1 HDMI, 2 Composite, 3 S-Video,
  4 Component), `--list-framesizes=YUYV`, `-c sharpness=1`

Retro consoles: 256×240 for NES/Famicom/SNES, 320×240 for Mega Drive/PlayStation, 720×480 for a line-doubled
progressive picture. Pixel aspect is reported (≈ 8:7 at 256), so scale the source to 4:3 in OBS.

### Module parameters

Set them with `options mxo2 ...` in `/etc/modprobe.d/mxo2.conf`. The ones you may need:

| Parameter | Default | Meaning |
|---|---|---|
| `retro_center` | 36536 | Composite 256/320: picture centre in 1/100 decoder samples (shifts the pixel grid) |
| `retro_center_pal` | 35320 | The same for PAL 288p |
| `pal_field_fix` | 1 | PAL 288p: align the card's alternating fields (stops a one-line up/down jitter); 0 off, -1 inverted |
| `retro_dots256`, `retro_dots320` | 341, 420 | Console dots per line for the 256/320 formats |
| `composite_interlaced` | -1 | -1 auto, 0 force 240p/288p, 1 force 480i/576i |
| `analog_field_swap` | 0 | Composite 480i/576i field order |
| `input` | -1 | Default of the Input control (audio follows the input): -1 auto, 0 HDMI, 1 composite, 2 S-video, 3 component |
| `output` | 1 | Video outputs show the selected input (pass-through); 0 off |
| `output_analog` | 0 | Analog OUT for SD: 0 composite + S-video, 1 component YPbPr |
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
