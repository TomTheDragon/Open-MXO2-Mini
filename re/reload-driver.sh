# Sourced by the run-*.sh test scripts (as root, after they cd into re/).
# - unload_driver: unload mxo2 even while it is in use (driver/mxo2-unload.sh, as install.sh: stops PipeWire for
#   the users holding the card, ends other programs that have its video / sound devices open).
# - load_driver [params]: load the development build ../driver/mxo2.ko (rebuilt first if a source is newer).
# - Cleanup, always at the end of a test (also on errors and Ctrl-C, and when a script calls pw_restore): if the
#   test unloaded or replaced the driver, programs still holding the card are ended and the installed driver
#   (DKMS) is loaded again; PipeWire is restarted for every user it was stopped for. So after a test the card is
#   free and works as before.
# State lives in /run/mxo2-test.<pid>, because test steps often run in a subshell ({ ... } | tee).
. ./mxo2-env.sh
DRV_DIR="$(cd ../driver 2>/dev/null && pwd)"
[ -n "$DRV_DIR" ] || { echo "ERROR: run from re/ (driver/ not found next to it)"; exit 1; }
DRV_KO="$DRV_DIR/mxo2.ko"
. "$DRV_DIR/mxo2-unload.sh"
MXO2_TEST_STATE=${MXO2_TEST_STATE:-/run/mxo2-test.$$}
export MXO2_TEST_STATE

save_pw_users() {
	[ -n "$MXO2_PW_USERS" ] && echo "$MXO2_PW_USERS" >> "$MXO2_TEST_STATE.pw"
	MXO2_PW_USERS=
	return 0
}

# Restore after a test (idempotent; the name is kept from older scripts).
pw_restore() {
	if [ -e "$MXO2_TEST_STATE" ]; then
		rm -f "$MXO2_TEST_STATE"
		echo "# test cleanup: loading the installed driver again"
		mxo2_unload || echo "# WARNING: the test's mxo2 could not be unloaded"
		save_pw_users
		if grep -q '^mxo2 ' /proc/modules; then
			:
		elif modprobe mxo2 2>/dev/null; then
			sleep 1.5
			for n in /sys/class/video4linux/video*; do
				grep -q MXO2 "$n/name" 2>/dev/null && echo "# installed driver loaded: /dev/$(basename "$n")"
			done
		else
			echo "# no installed driver to load (sudo sh ../driver/install.sh installs it)"
		fi
	fi
	save_pw_users
	if [ -e "$MXO2_TEST_STATE.pw" ]; then
		MXO2_PW_USERS=$(tr ' ' '\n' < "$MXO2_TEST_STATE.pw" | sort -u | tr '\n' ' ')
		rm -f "$MXO2_TEST_STATE.pw"
		mxo2_pw_restart
	fi
	return 0
}
trap pw_restore EXIT
trap 'exit 130' INT TERM

unload_driver() {
	touch "$MXO2_TEST_STATE"
	mxo2_unload || { echo "ERROR: mxo2 could not be unloaded"; save_pw_users; exit 1; }
	save_pw_users
}

load_driver() {  # extra args = module parameters
	unload_driver
	if [ ! -f "$DRV_KO" ] || [ -n "$(find "$DRV_DIR" -maxdepth 1 \( -name '*.c' -o -name '*.h' -o -name Makefile \) \
			-newer "$DRV_KO" 2>/dev/null | head -1)" ]; then
		echo "# building the development driver $DRV_KO"
		if [ -n "${SUDO_USER:-}" ]; then
			runuser -u "$SUDO_USER" -- make -C "$DRV_DIR" >/dev/null
		else
			make -C "$DRV_DIR" >/dev/null
		fi || { echo "ERROR: build failed"; exit 1; }
	fi
	modprobe -a videodev videobuf2-v4l2 videobuf2-dma-sg snd snd-pcm || exit 1
	mxo2_insmod "$DRV_KO" "$@" || { echo "ERROR: insmod $DRV_KO failed"; exit 1; }
	sleep 1.5
}
