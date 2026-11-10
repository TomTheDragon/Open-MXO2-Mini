#!/bin/sh
# Source tarball for the packages: open-mxo2-mini-<version>.tar.gz with the module source, dkms.conf, the WirePlumber rule,
# README and LICENSE. Usage: sh make-tarball.sh [OUTDIR]   (default: ../dist). Prints the tarball path.
set -e
TOP="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mkdir -p "${1:-$TOP/dist}" && cd "${1:-$TOP/dist}" && pwd)"
VER=$(sed -n 's/^PACKAGE_VERSION="\(.*\)"/\1/p' "$TOP/driver/dkms.conf")
T="$OUT/open-mxo2-mini-$VER.tar.gz"
cd "$TOP"
tar -czf "$T" --owner=0 --group=0 --mode=u=rwX,go=rX --sort=name --transform "s|^driver/||;s|^|open-mxo2-mini-$VER/|" \
	driver/mxo2_*.c driver/*.h driver/Makefile driver/dkms.conf driver/51-mxo2.conf README.md LICENSE
echo "$T"
