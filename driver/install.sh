#!/bin/sh
# Install the mxo2 driver with DKMS: rebuilt for every new kernel, loaded at boot when the card
# is present (PCI ID alias), dependencies (videodev, videobuf2-*) load automatically.
# Run with: sudo sh install.sh        (re-run after changing the driver source)
set -e
cd "$(dirname "$0")"
NAME=mxo2
VER=$(sed -n 's/^PACKAGE_VERSION="\(.*\)"/\1/p' dkms.conf)
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

echo "== packages (dkms, kernel headers that follow kernel updates)"
KREL=$(uname -r)
if command -v apt-get >/dev/null; then
	apt-get install -y dkms linux-headers-amd64 "linux-headers-$KREL"
elif command -v dnf >/dev/null; then
	# Fedora's dkms pulls in kernel-devel-matched, which follows kernel updates
	dnf install -y dkms "kernel-devel-$KREL"
	# videodev, videobuf2-* and snd-* live in kernel-modules, not in kernel-modules-core
	modinfo videodev >/dev/null 2>&1 || dnf install -y "kernel-modules-$KREL" || true
elif command -v zypper >/dev/null; then
	zypper --non-interactive install dkms kernel-default-devel
elif command -v pacman >/dev/null; then
	pacman -S --needed --noconfirm dkms linux-headers
else
	echo "unknown package manager: install dkms and the headers for kernel $KREL yourself"
fi
command -v dkms >/dev/null || { echo "dkms missing - install it and retry"; exit 1; }
[ -d "/lib/modules/$KREL/build" ] || { echo "kernel headers for $KREL missing (/lib/modules/$KREL/build)"; exit 1; }
for m in videodev videobuf2-v4l2 videobuf2-dma-sg snd-pcm; do
	modinfo $m >/dev/null 2>&1 || { echo "kernel module $m missing for $KREL - install your distro's full kernel modules package (Fedora: kernel-modules-$KREL)"; exit 1; }
done

# A loaded mxo2 is held by PipeWire (WirePlumber opens every camera and sound card) and by apps (OBS, VLC, ...):
# stop PipeWire and end those programs, unload; PipeWire is started again at the end.
. ./mxo2-unload.sh
trap mxo2_pw_restart EXIT
echo "== unload the running driver"
mxo2_unload

echo "== remove a previous copy"
if dkms status -m $NAME -v "$VER" | grep -q .; then
	dkms remove -m $NAME -v "$VER" --all
fi
rm -rf "/usr/src/$NAME-$VER"

echo "== copy source to /usr/src/$NAME-$VER"
mkdir -p "/usr/src/$NAME-$VER"
cp Makefile dkms.conf ./mxo2_*.c ./*.h "/usr/src/$NAME-$VER/"

echo "== build and install"
dkms add -m $NAME -v "$VER"
dkms build -m $NAME -v "$VER"
dkms install -m $NAME -v "$VER"

echo "== WirePlumber rule: stable PipeWire camera name for the MXO2 video device"
install -D -m 644 51-mxo2.conf /etc/wireplumber/wireplumber.conf.d/51-mxo2.conf

echo "== load"
if ! modprobe $NAME; then
	# With Secure Boot on, DKMS signs the module with /var/lib/dkms/mok.key; the kernel only accepts it once
	# that key is enrolled as a machine owner key (MOK).
	if mokutil --sb-state 2>/dev/null | grep -q enabled &&
		! mokutil --test-key /var/lib/dkms/mok.pub 2>/dev/null | grep -q "already enrolled"; then
		echo "Secure Boot is on and the DKMS signing key is not enrolled yet. Enrol it once:"
		echo "  sudo mokutil --import /var/lib/dkms/mok.pub    (choose a one-time password)"
		echo "then reboot and pick 'Enroll MOK' in the blue MOK manager screen. The driver loads after that."
		exit 0
	fi
	echo "modprobe $NAME failed, see: dmesg | tail"
	exit 1
fi
sleep 1.5
grep -q MXO2 /proc/asound/cards && echo "sound card: $(grep -A1 MXO2 /proc/asound/cards | head -1)"
dkms status -m $NAME
modinfo -n $NAME
for n in /sys/class/video4linux/video*; do
	grep -q MXO2 "$n/name" 2>/dev/null && echo "device: /dev/$(basename "$n") ($(cat "$n/name"))"
done
dmesg | grep mxo2 | tail -5
mxo2_pw_restart
# a WirePlumber that kept running picks up the rule with a restart
for u in $(ps -C wireplumber -o user:64= 2>/dev/null | sort -u); do
	mxo2_pw_ctl "$u" restart wireplumber || true
done
echo "done - the driver now loads automatically at boot"
