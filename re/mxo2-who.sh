#!/bin/sh
# Which programs use the MXO2 (its video device, ALSA card, debugfs files)? Lists them; with --close ends them
# (SIGTERM; PipeWire / WirePlumber restart by themselves and are only listed).
# Run with: sudo sh mxo2-who.sh [--close]      (without sudo only your own programs are visible)
cd "$(dirname "$0")" || exit 1
. ../driver/mxo2-unload.sh
grep -q '^mxo2 ' /proc/modules || { echo "mxo2 is not loaded (sudo modprobe mxo2)"; exit 0; }
echo "mxo2 loaded, use count $(cat /sys/module/mxo2/refcnt 2>/dev/null); device files: $(mxo2_nodes | tr '\n' ' ')"
pids=$(mxo2_holders)
if [ -z "$pids" ]; then
	echo "no program uses the MXO2"
	exit 0
fi
echo "used by:"
mxo2_show $pids
[ "$1" = --close ] || exit 0
for p in $pids; do
	case "$(cat "/proc/$p/comm" 2>/dev/null)" in
	pipewire*|wireplumber) ;;
	*) kill -TERM "$p" 2>/dev/null && echo "closed pid $p" ;;
	esac
done
