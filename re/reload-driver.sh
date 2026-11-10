# Sourced by the run-*.sh test scripts (as root, after they cd into re/): load the driver build from
# ../driver/.
# If the loaded mxo2 can't be unloaded because the desktop sound server (PipeWire/WirePlumber) holds
# its ALSA card open, the user's PipeWire services are stopped for the test and restarted on exit.
. ./mxo2-env.sh
DRV_DIR="$(cd ../driver 2>/dev/null && pwd)"
[ -n "$DRV_DIR" ] || { echo "ERROR: run from re/ (driver/ not found next to it)"; exit 1; }
DRV_KO="$DRV_DIR/mxo2.ko"
PW_USER=${SUDO_USER:-}
PW_STOPPED=
PW_UNITS="wireplumber pipewire-pulse pipewire pipewire-pulse.socket pipewire.socket"

pw_ctl() {  # $1 = stop|start
	[ -n "$PW_USER" ] || return 1
	runuser -u "$PW_USER" -- env XDG_RUNTIME_DIR="/run/user/$(id -u "$PW_USER")" \
		systemctl --user "$1" $PW_UNITS 2>/dev/null
}

pw_restore() {
	if [ -n "$PW_STOPPED" ]; then
		echo "# restarting PipeWire for $PW_USER"
		pw_ctl start
		PW_STOPPED=
	fi
}
trap pw_restore EXIT INT TERM

unload_driver() {
	grep -q '^mxo2 ' /proc/modules || return 0
	rmmod mxo2 2>/dev/null && return 0
	echo "# mxo2 is in use (holders below); stopping PipeWire for the test"
	fuser -v /dev/snd/* /dev/video* 2>&1 | grep -v '^ *$' | head -10
	pw_ctl stop && PW_STOPPED=1
	sleep 1
	rmmod mxo2 || { echo "ERROR: mxo2 still in use, close programs using the MXO2 and retry"; exit 1; }
}

load_driver() {  # extra args = module parameters
	unload_driver
	if [ ! -f "$DRV_KO" ]; then
		echo "# $DRV_KO missing, building it"
		make -C "$DRV_DIR" >/dev/null || { echo "ERROR: build failed"; exit 1; }
	fi
	modprobe -a videodev videobuf2-v4l2 videobuf2-dma-sg snd snd-pcm || exit 1
	mxo2_insmod "$DRV_KO" "$@" || { echo "ERROR: insmod $DRV_KO failed"; exit 1; }
	sleep 1.5
}
