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
apt-get install -y dkms linux-headers-amd64 "linux-headers-$(uname -r)"

# A loaded mxo2 is usually held by the desktop sound server (WirePlumber opens every ALSA card);
# stop the user's PipeWire while swapping the module, restart it afterwards.
PW_USER=${SUDO_USER:-}
PW_UNITS="wireplumber pipewire-pulse pipewire pipewire-pulse.socket pipewire.socket"
pw_ctl() {
	[ -n "$PW_USER" ] || return 1
	runuser -u "$PW_USER" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$PW_USER")" \
		systemctl --user "$1" $PW_UNITS 2>/dev/null
}
PW_STOPPED=
trap '[ -n "$PW_STOPPED" ] && pw_ctl start' EXIT
if grep -q "^$NAME " /proc/modules && ! rmmod $NAME 2>/dev/null; then
	echo "== $NAME in use, stopping PipeWire for $PW_USER during the install"
	pw_ctl stop && PW_STOPPED=1
	sleep 1
	rmmod $NAME || { echo "still in use - close programs using the MXO2 (OBS, VLC, ...) and retry"; exit 1; }
fi

echo "== remove a previous copy"
if dkms status -m $NAME -v "$VER" | grep -q .; then
	dkms remove -m $NAME -v "$VER" --all
fi
rm -rf "/usr/src/$NAME-$VER"

echo "== copy source to /usr/src/$NAME-$VER"
mkdir -p "/usr/src/$NAME-$VER"
cp Makefile dkms.conf ./*.c ./*.h "/usr/src/$NAME-$VER/"

echo "== build and install"
dkms add -m $NAME -v "$VER"
dkms build -m $NAME -v "$VER"
dkms install -m $NAME -v "$VER"

echo "== WirePlumber rule: unique PipeWire camera names for the HDMI and Composite devices"
install -D -m 644 51-mxo2.conf /etc/wireplumber/wireplumber.conf.d/51-mxo2.conf

echo "== load"
modprobe $NAME
sleep 1.5
grep -q MXO2 /proc/asound/cards && echo "sound card: $(grep -A1 MXO2 /proc/asound/cards | head -1)"
dkms status -m $NAME
modinfo -n $NAME
for n in /sys/class/video4linux/video*; do
	grep -q MXO2 "$n/name" 2>/dev/null && echo "device: /dev/$(basename "$n") ($(cat "$n/name"))"
done
dmesg | grep mxo2 | tail -5
if [ -n "$PW_STOPPED" ]; then
	pw_ctl start
	PW_STOPPED=
fi
[ -n "$PW_USER" ] && runuser -u "$PW_USER" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$PW_USER")" \
	systemctl --user restart wireplumber 2>/dev/null   # picks up the rule
echo "done - the driver now loads automatically at boot"
