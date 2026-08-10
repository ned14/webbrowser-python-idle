#!/bin/sh
# Keep the X resolution matched to the KMS canvas size. NON-DESTRUCTIVE: never
# `xrandr --output ... --off` (on the mis-enumerated CheerpX "None-0" connector
# that blanks the display); just re-apply the preferred mode on a poll.
while true; do
	xrandr --auto 2>/dev/null
	sleep 3
done &
