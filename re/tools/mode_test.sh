#!/bin/sh
# All composite sizes through the installed driver (runs as a user in the video group, no sudo):
# per size: format, fps over 120 frames, one frame -> $OUT/mode-WxH.png; then v4l2-compliance and VLC (default input).
# Usage: sh mode_test.sh OUTDIR [/dev/videoN, default: the "Matrox MXO2 Mini" device, Input set to Composite]
OUT=${1:?outdir}; V=${2:-}
if [ -z "$V" ]; then
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini" "$n/name" 2>/dev/null && V=/dev/$(basename "$n")
	done
	# one device for all inputs: the Input control (0 Auto, 1 HDMI, 2 Composite, 3 S-Video, 4 Component) picks Composite
	[ -n "$V" ] && v4l2-ctl -d "$V" -c input=2
fi
T="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"
v4l2-ctl -d "$V" --get-input --list-framesizes=YUYV
for S in $(v4l2-ctl -d "$V" --list-framesizes=YUYV | sed -n 's/.*Discrete \([0-9]*x[0-9]*\).*/\1/p'); do
	W=${S%x*}; H=${S#*x}
	echo "### $S"
	v4l2-ctl -d "$V" --set-fmt-video=width=$W,height=$H,pixelformat=YUYV
	v4l2-ctl -d "$V" --get-fmt-video --get-parm | grep -E 'Field|Frames per'
	v4l2-ctl -d "$V" --list-frameintervals=width=$W,height=$H,pixelformat=YUYV | grep Interval
	timeout 20 v4l2-ctl -d "$V" --stream-mmap=4 --stream-count=120 2>&1 | tr -d '<>' | grep fps | tail -1
	timeout 10 v4l2-ctl -d "$V" --stream-mmap=4 --stream-skip=5 --stream-count=1 --stream-to="$OUT/mode-$S.raw" >/dev/null 2>&1
	python3 -I "$T/raw2png.py" "$OUT/mode-$S.raw" "$OUT/mode-$S.png" $W $H 1 plain
done
echo "### v4l2-compliance"
v4l2-compliance -d "$V" -s 2>&1 | grep -E 'fail|Total'
echo "### VLC on $V, default options, 15 s"
rm -f "$OUT/vlc.mkv"
timeout 15 cvlc -q "v4l2://$V" --sout "#transcode{vcodec=h264,venc=x264{preset=ultrafast},acodec=none}:std{access=file,mux=mkv,dst=$OUT/vlc.mkv}" >/dev/null 2>&1
ffprobe -v error -count_frames -show_entries stream=width,height,nb_read_frames:format=duration -of compact "$OUT/vlc.mkv"
ffmpeg -v error -y -ss 7 -i "$OUT/vlc.mkv" -frames:v 1 "$OUT/vlc.png"
