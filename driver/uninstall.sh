#!/bin/sh
# Remove the DKMS-installed mxo2 driver. Run with: sudo sh uninstall.sh
cd "$(dirname "$0")"
VER=$(sed -n 's/^PACKAGE_VERSION="\(.*\)"/\1/p' dkms.conf)
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
rmmod mxo2 2>/dev/null
dkms remove -m mxo2 -v "$VER" --all 2>/dev/null
rm -rf "/usr/src/mxo2-$VER"
rm -f /etc/wireplumber/wireplumber.conf.d/51-mxo2.conf
depmod -a
echo "mxo2 removed"
