# Sourced by install.sh and uninstall.sh (as root; works with set -e).
# mxo2_unload: unload a loaded mxo2 even while programs use it. Finds every process that has one of the driver's
# device files open or mapped (/dev/videoN "Matrox MXO2 Mini", the "MXO2 Mini" ALSA card, its debugfs files), stops
# PipeWire for the users running it (WirePlumber opens every camera and sound card, and systemd would restart it if
# just killed), then ends the remaining programs (SIGTERM, after 3 s SIGKILL) and unloads.
# mxo2_pw_restart: start PipeWire again for those users (call it on exit).

MXO2_PW_UNITS="wireplumber pipewire-pulse pipewire pipewire-pulse.socket pipewire.socket"
MXO2_PW_USERS=

mxo2_pw_ctl() {  # $1 = user, $2 = stop|start|restart, [$3 = units, default: all of PipeWire]
	_uid=$(id -u "$1" 2>/dev/null) || return 1
	[ -d "/run/user/$_uid" ] || return 1
	runuser -u "$1" -- env XDG_RUNTIME_DIR="/run/user/$_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$_uid/bus" \
		systemctl --user "$2" ${3:-$MXO2_PW_UNITS} 2>/dev/null
}

mxo2_pw_restart() {
	for _u in $MXO2_PW_USERS; do
		echo "== starting PipeWire for $_u again"
		mxo2_pw_ctl "$_u" start || echo "  failed - log out and in again, or: systemctl --user start pipewire wireplumber"
	done
	MXO2_PW_USERS=
	return 0
}

mxo2_nodes() {  # the device files of the loaded driver, one per line
	for _n in /sys/class/video4linux/video*; do
		grep -q MXO2 "$_n/name" 2>/dev/null && echo "/dev/$(basename "$_n")"
	done
	_card=$(awk '/MXO2/ {print $1; exit}' /proc/asound/cards 2>/dev/null)
	if [ -n "$_card" ]; then
		for _f in /dev/snd/controlC"$_card" /dev/snd/pcmC"$_card"D* /dev/snd/hwC"$_card"D*; do
			[ -e "$_f" ] && echo "$_f"
		done
	fi
	return 0
}

mxo2_holders() {  # PIDs that have one of them open or mapped (V4L2 buffers stay mapped after close)
	_args=
	_re='^/sys/kernel/debug/mxo2/'
	for _f in $(mxo2_nodes); do
		_args="$_args -o -lname $_f"
		_re="$_re|^$_f\$"
	done
	{
		# shellcheck disable=SC2086
		find /proc/[0-9]*/fd -maxdepth 1 -type l \( -lname '/sys/kernel/debug/mxo2/*' $_args \) 2>/dev/null |
			cut -d/ -f3
		for _m in /proc/[0-9]*/maps; do
			awk -v re="$_re" '$6 ~ re { found = 1; exit } END { exit !found }' "$_m" 2>/dev/null &&
				echo "$_m" | cut -d/ -f3
		done
	} | grep -v -x "$$" | sort -u
	return 0
}

mxo2_show() {  # $@ = PIDs
	for _p in "$@"; do
		[ -d "/proc/$_p" ] && echo "  pid $_p $(cat "/proc/$_p/comm" 2>/dev/null) (user $(ps -o user:64= -p "$_p" 2>/dev/null))"
	done
	return 0
}

mxo2_unload() {
	grep -q "^mxo2 " /proc/modules || return 0
	rmmod mxo2 2>/dev/null && return 0
	_pids=$(mxo2_holders)
	echo "== mxo2 is in use:"
	mxo2_show $_pids
	# PipeWire first, through systemd, so it doesn't come straight back
	for _u in $(for _p in $_pids; do ps -o user:64= -p "$_p" 2>/dev/null; done | sort -u); do
		if pgrep -u "$_u" -x 'pipewire|wireplumber|pipewire-pulse' >/dev/null 2>&1; then
			echo "== stopping PipeWire for $_u during the install"
			mxo2_pw_ctl "$_u" stop && MXO2_PW_USERS="$MXO2_PW_USERS $_u"
		fi
	done
	for _round in 1 2 3; do
		sleep 1
		rmmod mxo2 2>/dev/null && return 0
		_pids=$(mxo2_holders)
		[ -n "$_pids" ] || continue     # nobody left: the module count drops a moment after the last close
		echo "== ending programs that still use the MXO2:"
		mxo2_show $_pids
		kill -TERM $_pids 2>/dev/null || true
		for _i in 1 2 3 4 5 6; do
			sleep 0.5
			_left=
			for _p in $_pids; do [ -d "/proc/$_p" ] && _left="$_left $_p"; done
			[ -n "$_left" ] || break
		done
		[ -n "$_left" ] && { echo "  not ended after 3 s, killing:$_left"; kill -KILL $_left 2>/dev/null || true; }
	done
	sleep 1
	rmmod mxo2 2>/dev/null && return 0
	echo "mxo2 still in use (module use count $(cat /sys/module/mxo2/refcnt 2>/dev/null)); remaining users:"
	mxo2_show $(mxo2_holders)
	echo "reboot, then run the install again"
	return 1
}
