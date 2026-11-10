#!/bin/sh
# Build open-mxo2-mini-dkms_<version>-1_all.deb (no build dependencies besides dpkg-deb).
# Usage: sh build-deb.sh [OUTDIR]   (default: ../../dist)
# Install: sudo apt install ./dist/open-mxo2-mini-dkms_<version>-1_all.deb
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
TOP="$(cd "$HERE/../.." && pwd)"
OUT="$(mkdir -p "${1:-$TOP/dist}" && cd "${1:-$TOP/dist}" && pwd)"
VER=$(sed -n 's/^PACKAGE_VERSION="\(.*\)"/\1/p' "$TOP/driver/dkms.conf")
MAINT="${DEBFULLNAME:-Open-MXO2-Mini} <${DEBEMAIL:-maintainer@example.invalid}>"
S="$HERE/build/open-mxo2-mini-dkms"
rm -rf "$HERE/build"
mkdir -p "$S/DEBIAN" "$S/usr/src/mxo2-$VER" "$S/usr/share/wireplumber/wireplumber.conf.d" \
	"$S/usr/share/doc/open-mxo2-mini-dkms"
cp "$TOP"/driver/mxo2_*.c "$TOP"/driver/*.h "$TOP/driver/Makefile" "$TOP/driver/dkms.conf" "$S/usr/src/mxo2-$VER/"
cp "$TOP/driver/51-mxo2.conf" "$S/usr/share/wireplumber/wireplumber.conf.d/"
cp "$TOP/README.md" "$S/usr/share/doc/open-mxo2-mini-dkms/"
cat > "$S/usr/share/doc/open-mxo2-mini-dkms/copyright" <<EOT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Open-MXO2-Mini

Files: *
License: GPL-2
 On Debian systems the full text of the GNU General Public License version 2 is in
 /usr/share/common-licenses/GPL-2.
EOT
cat > "$S/DEBIAN/control" <<EOT
Package: open-mxo2-mini-dkms
Version: $VER-1
Architecture: all
Section: kernel
Priority: optional
Maintainer: $MAINT
Depends: dkms (>= 2.8)
Recommends: linux-headers-generic | linux-headers-amd64
Installed-Size: $(du -sk "$S/usr" | cut -f1)
Description: Open-MXO2-Mini: Matrox MXO2 Mini capture box driver (DKMS)
 Unofficial, reverse-engineered kernel driver for the Matrox MXO2 Mini with its
 PCIe card (102b:8350): HDMI and composite video capture (V4L2, two devices) and
 audio (ALSA). Built with DKMS for every installed kernel. Includes a WirePlumber
 rule that gives the two cameras distinct PipeWire names.
EOT
cat > "$S/DEBIAN/postinst" <<EOT
#!/bin/sh
set -e
if [ "\$1" = configure ]; then
	dkms add -m mxo2 -v $VER >/dev/null 2>&1 || true
	for k in \$(ls /lib/modules); do
		if [ -e "/lib/modules/\$k/build" ]; then
			dkms install -m mxo2 -v $VER -k "\$k" || echo "mxo2: building for kernel \$k failed (kernel headers?)" >&2
		fi
	done
	modprobe mxo2 2>/dev/null || true
	echo "mxo2: log out and in (or: systemctl --user restart wireplumber) so PipeWire names both cameras"
fi
exit 0
EOT
cat > "$S/DEBIAN/prerm" <<EOT
#!/bin/sh
set -e
case "\$1" in
remove|upgrade|deconfigure)
	rmmod mxo2 2>/dev/null || true
	dkms remove -m mxo2 -v $VER --all >/dev/null 2>&1 || true
	;;
esac
exit 0
EOT
chmod -R u=rwX,go=rX "$S"
chmod 755 "$S/DEBIAN/postinst" "$S/DEBIAN/prerm"
dpkg-deb --root-owner-group --build "$S" "$OUT/open-mxo2-mini-dkms_${VER}-1_all.deb" >/dev/null
rm -rf "$HERE/build"
echo "$OUT/open-mxo2-mini-dkms_${VER}-1_all.deb"
