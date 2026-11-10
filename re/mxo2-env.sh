# Sourced by the run-*.sh scripts (after they cd into re/). Works on any distribution (Debian, Fedora, ...).
# - MXO2_DEV: PCI address of the card (e.g. 0000:25:00.0), found by its PCI ID 102b:8350. Set MXO2_DEV
#   yourself to pick a card. Exported, so the Python tools (gaia.find_dev) use the same one.
# - mxo2_insmod: insmod that first signs the module when the kernel only accepts signed modules
#   (Secure Boot / lockdown, the default on Fedora with Secure Boot on).
# - mxo2_locked_down / mxo2_need_unlocked: kernel lockdown blocks user-space BAR access and writable debugfs files.
[ -n "$MXO2_ENV_LOADED" ] && return 0
MXO2_ENV_LOADED=1

if [ -z "$MXO2_DEV" ]; then
	for d in /sys/bus/pci/devices/*; do
		if [ "$(cat "$d/vendor" 2>/dev/null)" = 0x102b ] && [ "$(cat "$d/device" 2>/dev/null)" = 0x8350 ]; then
			MXO2_DEV=$(basename "$d")
			break
		fi
	done
fi
[ -n "$MXO2_DEV" ] || { echo "ERROR: no Matrox MXO2 card (PCI ID 102b:8350) found; set MXO2_DEV=0000:bb:dd.f"; exit 1; }
export MXO2_DEV

mxo2_locked_down() {  # true when lockdown is integrity or confidentiality
	grep -q '\[integrity\]\|\[confidentiality\]' /sys/kernel/security/lockdown 2>/dev/null
}

mxo2_need_unlocked() {  # $1 = what needs it
	mxo2_locked_down || return 0
	echo "ERROR: $1 is blocked by kernel lockdown ($(cat /sys/kernel/security/lockdown))."
	echo "  Secure Boot is on. Turn it off in the firmware setup (or: sudo mokutil --disable-validation) for this test."
	exit 1
}

mxo2_sign_needed() {
	mxo2_locked_down && return 0
	[ "$(cat /sys/module/module/parameters/sig_enforce 2>/dev/null)" = Y ]
}

# Sign with the key DKMS uses (/var/lib/dkms/mok.key, created by the first DKMS install; Debian and Fedora alike).
# It must be enrolled with mokutil once, see README "Secure Boot".
mxo2_sign() {  # $1 = .ko
	tail -c 28 "$1" | grep -q 'Module signature appended' && return 0
	KEY=${MXO2_SIGN_KEY:-/var/lib/dkms/mok.key}
	CERT=${MXO2_SIGN_CERT:-/var/lib/dkms/mok.pub}
	SIGN=/lib/modules/$(uname -r)/build/scripts/sign-file
	if [ ! -f "$KEY" ] || [ ! -f "$CERT" ]; then
		echo "ERROR: the kernel needs signed modules, and $KEY / $CERT are missing."
		echo "  Install the driver once with DKMS (sudo sh ../driver/install.sh), which creates the key, and enrol it."
		exit 1
	fi
	[ -x "$SIGN" ] || { echo "ERROR: $SIGN missing (kernel headers / kernel-devel installed?)"; exit 1; }
	"$SIGN" sha256 "$KEY" "$CERT" "$1" || { echo "ERROR: signing $1 failed"; exit 1; }
}

mxo2_insmod() {  # $1 = .ko, rest = module parameters
	if mxo2_sign_needed; then
		mxo2_sign "$1"
	fi
	insmod "$@"
}
