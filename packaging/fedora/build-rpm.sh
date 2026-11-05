#!/bin/sh
# Build the noarch RPM with rpmbuild (sudo dnf install rpm-build). Usage: sh build-rpm.sh [OUTDIR] (default ../../dist)
# Install: sudo dnf install ./dist/open-mxo2-mini-dkms-<version>-1.<dist>.noarch.rpm
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
TOP="$(cd "$HERE/../.." && pwd)"
OUT="$(mkdir -p "${1:-$TOP/dist}" && cd "${1:-$TOP/dist}" && pwd)"
R="$HERE/rpmbuild"
rm -rf "$R"
mkdir -p "$R/SOURCES"
sh "$TOP/packaging/make-tarball.sh" "$R/SOURCES" >/dev/null
rpmbuild --define "_topdir $R" -bb "$HERE/open-mxo2-mini-dkms.spec"
cp "$R"/RPMS/noarch/*.rpm "$OUT/"
rm -rf "$R"
ls "$OUT"/open-mxo2-mini-dkms-*.noarch.rpm
