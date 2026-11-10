#!/bin/sh
# Video output from Linux: v4l2-ctl plays test patterns to the "Matrox MXO2 Mini Output" device, one format after
# the other (NTSC 720x480, PAL 720x576, 1080p30), each 10 s: colour bars moving sideways with a square. After each
# one it asks what HDMI OUT and the analog OUT showed. 1080p is tried with several GenlockControl values
# (module parameter play_genlock, set with sudo). Then, if ffmpeg is there, 10 s of ffmpeg's test picture.
# Run as your user (no sudo), the new driver installed, no app capturing (OBS / VLC closed - playback needs the
# card alone). TV on HDMI OUT and/or composite OUT.
# Usage: sh run-play-test.sh [ntsc] [pal] [1080p] [audio]   (default: ntsc pal; 1080p is an experiment, see play_hd;
#        audio: a tone through ALSA alone, then picture + beeps together)
cd "$(dirname "$0")" || exit 1
OUT=notes/play-test.txt
[ $# -gt 0 ] || set -- ntsc pal
ask() {
	printf '    What did HDMI OUT and composite OUT show? > ' >/dev/tty
	read -r note </dev/tty
	echo "NOTE $1: $note"
}
{
	trap 'pkill -u "$(id -u)" -f "v4l2-ctl -d $ODEV --stream-out|ffmpeg .*-f v4l2 $ODEV" 2>/dev/null' EXIT
	trap 'exit 130' INT TERM
	ODEV=
	for n in /sys/class/video4linux/video*; do
		grep -qx "Matrox MXO2 Mini Output" "$n/name" 2>/dev/null && ODEV=/dev/$(basename "$n")
	done
	echo "# output device: ${ODEV:-none}"
	[ -n "$ODEV" ] || { echo "no \"Matrox MXO2 Mini Output\" device - install the new driver: sudo sh driver/install.sh"; exit 1; }
	v4l2-ctl -d "$ODEV" --info --list-formats-out-ext 2>&1 | grep -E "Card|Size|Interval"
	GL=/sys/module/mxo2/parameters/play_genlock
	play() {  # $1 = name, $2 = seconds
		echo "### $1: ${W}x${H}, $2 s"
		v4l2-ctl -d "$ODEV" --set-fmt-video-out=width=$W,height=$H,pixelformat=YUYV
		v4l2-ctl -d "$ODEV" --get-fmt-video-out | grep -E "Width|Field"
		T0=$(date +%s.%N)
		timeout 30 v4l2-ctl -d "$ODEV" --stream-out-mmap=3 --stream-out-pattern=0 --stream-out-square \
			--stream-out-hor-speed=2 --stream-count=$(awk "BEGIN { printf \"%d\", $FPS * $2 }") 2>&1 |
			tr -d '>' | grep -v '^$' | tail -2
		echo "  took $(awk "BEGIN { printf \"%.1f\", $(date +%s.%N) - $T0 }") s (expected ~$2 s)"
		ask "$1"
	}
	for want in "$@"; do
		case $want in
		ntsc) W=720; H=480; FPS=29.97; play ntsc 10 ;;
		pal) W=720; H=576; FPS=25; play pal 10 ;;
		1080p)
			W=1920; H=1080; FPS=30
			echo Y | sudo tee /sys/module/mxo2/parameters/play_hd >/dev/null   # 1080p is hidden by default
			for g in 0 0x1000 0x400 0x1; do
				echo "$g" | sudo tee $GL >/dev/null || { echo "could not set play_genlock"; break; }
				play "1080p genlock $g" 6
			done
			echo 0 | sudo tee $GL >/dev/null
			echo N | sudo tee /sys/module/mxo2/parameters/play_hd >/dev/null ;;
		audio)
			# ALSA playback to the card ("MXO2 Mini", hw device), 48 kHz stereo
			echo "### audio only: 440 Hz tone, 6 s (left louder than right)"
			timeout 20 ffmpeg -nostdin -hide_banner -loglevel error -re -f lavfi -i \
				"sine=frequency=440:sample_rate=48000,pan=stereo|c0=c0|c1=0.3*c0" -t 6 \
				-ac 2 -ar 48000 -sample_fmt s16 -f alsa hw:CARD=Mini 2>&1 | tail -3
			ask "audio only"
			echo "### video + audio: ffmpeg testsrc2 with a beep every second, 12 s (beep with the flash?)"
			timeout 30 ffmpeg -nostdin -hide_banner -loglevel error -re \
				-f lavfi -i testsrc2=size=720x480:rate=30000/1001 \
				-f lavfi -i "sine=frequency=1000:sample_rate=48000:beep_factor=4" -t 12 \
				-map 0:v -pix_fmt yuyv422 -f v4l2 "$ODEV" \
				-map 1:a -ac 2 -ar 48000 -sample_fmt s16 -f alsa hw:CARD=Mini 2>&1 | tail -3
			ask "video + audio" ;;
		*) echo "unknown format $want" ;;
		esac
	done
	if command -v ffmpeg >/dev/null; then
		echo "### ffmpeg testsrc2 720x480 29.97, 10 s"
		timeout 30 ffmpeg -nostdin -hide_banner -loglevel error -re -f lavfi -i testsrc2=size=720x480:rate=30000/1001 \
			-t 10 -pix_fmt yuyv422 -f v4l2 "$ODEV" 2>&1 | tail -3
		ask ffmpeg
	fi
	echo "# driver log"
	journalctl -k --since -5min --no-pager 2>/dev/null | grep mxo2 | tail -15
} 2>&1 | tee "$OUT"
echo "-> $OUT"
