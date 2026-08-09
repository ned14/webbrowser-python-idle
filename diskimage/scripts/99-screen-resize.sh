#!/bin/sh
# Keep the X resolution matched to the KMS canvas size.
# Runs from /etc/X11/xinit/xinitrc.d (sourced by xinit at X startup).
# Polls xrandr for the detected output size and re-applies --auto whenever the
# mode changes (the canvas resize races the first poll, so re-run until stable).
while true; do
	prev=""
	stable=0
	while [ "$stable" -lt 2 ]; do
		cur=$(xrandr 2>/dev/null | sed -n 's/.* connected .* \([0-9]*x[0-9]*\).*/\1/p' | head -1)
		if [ -n "$cur" ] && [ "$cur" != "$prev" ]; then
			prev=$cur
			stable=0
			xrandr --output None-0 --off 2>/dev/null
			xrandr --auto 2>/dev/null
		else
			stable=$((stable + 1))
		fi
		sleep 1
	done
	sleep 2
done &
