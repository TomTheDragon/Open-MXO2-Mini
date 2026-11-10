#!/bin/sh
# The video device through PipeWire, as a desktop app sees it (run as your user, no sudo; driver installed and
# loaded). Per input: set the Input control, then PipeWire (GStreamer pipewiresrc) streams 60 frames and saves one
# as notes/pw/<input>.png. Also prints the PipeWire node and its formats, and WirePlumber's log about cameras.
# Usage: sh run-pipewire-test.sh [auto|hdmi|composite|svideo|component ...]   (default: auto)
# Needs: v4l-utils, gstreamer1-plugins-good (pngenc), pipewire-gstreamer.
cd "$(dirname "$0")" || exit 1
OUT=notes/pipewire-test.txt
mkdir -p notes/pw
[ $# -gt 0 ] || set -- auto
{
	VDEV=
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini" "$n/name" 2>/dev/null && VDEV=/dev/$(basename "$n")
	done
	echo "# V4L2 device: ${VDEV:-none}"
	ls /sys/class/video4linux/ | while read -r d; do echo "  $d: $(cat /sys/class/video4linux/$d/name)"; done
	[ -n "$VDEV" ] || { echo "no \"Matrox MXO2 Mini\" device (old driver still loaded?)"; exit 1; }
	v4l2-ctl -d "$VDEV" --list-inputs --get-input -C input 2>&1
	echo "# WirePlumber rule"
	cat /etc/wireplumber/wireplumber.conf.d/51-mxo2.conf /usr/share/wireplumber/wireplumber.conf.d/51-mxo2.conf \
		2>/dev/null | grep -E 'cap.card|node.name' || echo "  not installed"
	echo "# PipeWire video nodes"
	wpctl status 2>&1 | sed -n '/^Video/,/^Settings/p'
	node() {
		pw-dump 2>/dev/null | python3 -I -c '
import json, sys
for o in json.load(sys.stdin):
    p = (o.get("info") or {}).get("props") or {}
    if o.get("type", "").endswith(":Node") and (p.get("api.v4l2.cap.driver") == "mxo2" or
                                              "mxo2" in str(p.get("node.name", ""))):
        if sys.argv[1] == "name":
            print(p.get("node.name")); break
        print("  id %s node.name %s, description %s, path %s, state %s" % (o["id"], p.get("node.name"),
              p.get("node.description"), p.get("api.v4l2.path"), o["info"].get("state")))
        for f in (o["info"].get("params") or {}).get("EnumFormat", []):
            print("    format %s %s %s" % (f.get("format"), f.get("size"), f.get("framerate")))
' "$1"
	}
	node info
	NODE=$(node name)
	echo "# node: ${NODE:-none}"
	[ -n "$NODE" ] || { echo "PipeWire has no node for the MXO2 (WirePlumber log below)"; }
	for want in "$@"; do
		case $want in
		auto) C=0 ;; hdmi) C=1 ;; composite) C=2 ;; svideo) C=3 ;; component) C=4 ;;
		*) echo "unknown input $want"; continue ;;
		esac
		echo "### $want: Input control = $C"
		v4l2-ctl -d "$VDEV" -c input=$C
		sleep 3
		v4l2-ctl -d "$VDEV" --get-input --get-fmt-video --list-framesizes=YUYV 2>&1 | grep -E 'Input|Width|Size'
		[ -n "$NODE" ] || continue
		NODE=$(node name)
		node info | grep format
		rm -f notes/pw/"$want"-*.png
		T0=$(date +%s.%N)
		timeout 20 gst-launch-1.0 -v pipewiresrc target-object="$NODE" num-buffers=60 ! video/x-raw ! fakesink sync=false 2>&1 |
			grep -E 'fakesink0.sink: caps = |ERROR|WARN|error' | sed 's/^/  /' | tail -4
		echo "  60 frames took $(awk "BEGIN { printf \"%.1f\", $(date +%s.%N) - $T0 }") s (timeout 20 s = no frames)"
		timeout 20 gst-launch-1.0 -q pipewiresrc target-object="$NODE" num-buffers=20 ! videoconvert ! pngenc ! \
			multifilesink location=notes/pw/"$want"-%02d.png 2>&1 | sed 's/^/  /' | tail -4
		LAST=$(ls notes/pw/"$want"-*.png 2>/dev/null | tail -1)
		if [ -n "$LAST" ]; then
			mv "$LAST" notes/pw/"$want".png
			rm -f notes/pw/"$want"-*.png
			echo "  picture: notes/pw/$want.png"
		else
			echo "  NO PICTURE (no frame from PipeWire)"
		fi
	done
	v4l2-ctl -d "$VDEV" -c input=0
	echo "# WirePlumber / PipeWire log (last 5 min, v4l2 / camera)"
	journalctl --user -u wireplumber -u pipewire --since -5min --no-pager 2>/dev/null |
		grep -E 'spa\.v4l2|s-monitors|wireplumber\[' | grep -i -E 'v4l2|camera|mxo2|video' | tail -20
} 2>&1 | tee "$OUT"
echo "-> $OUT"
