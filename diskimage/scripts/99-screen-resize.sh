#!/bin/sh
# Keep the X resolution matched to the KMS canvas size. NON-DESTRUCTIVE: never
# `xrandr --output ... --off` (on the mis-enumerated CheerpX "None-0" connector
# that blanks the display); just re-apply the preferred mode on a poll.
#
# Adaptive cadence: every `xrandr --auto` is a full mode re-application +
# DRM re-enumeration through the emulator, so it taxes the single guest vCPU
# for the whole desktop lifetime. `--auto` therefore runs ONLY when the
# geometry actually changed (the `xrandr --current` signature differs — a
# canvas resize re-arms the fast cadence on the next slow tick; the E2E
# resize spec allows a 45 s window, a 30 s worst case is well inside it),
# plus a slow 1/min safety net for a silent mode regression that never
# changes the geometry signature. Poll fast (2 s) while the output is
# changing (boot, window resizes); once stable, slow to 30 s (was 10 s —
# every tick is an emulated execve chain of xrandr + md5sum, so the steady
# cadence is pure vCPU cost on a quiet desktop). The geometry signature
# comes from `xrandr --current` (a cheap query, unlike --auto); a 32-char
# cut of the geometry/connected lines replaces the full-output md5sum (the
# hash cost is dominated by the execve, but the md5 of ~40 lines adds real
# emulated work per tick and the head of the output changes whenever the
# geometry does).
interval=2
steady=0
last=""
while true; do
	cur=$(xrandr --current 2>/dev/null)
	hash=$(printf '%s\n' "$cur" | grep -E 'connected|current' | head -n 2 | cut -c1-64 | tr -d ' \t')
	if [ -z "$hash" ] || [ "$hash" != "$last" ]; then
		# Geometry changed (or first tick): apply the preferred mode, then
		# re-read so the next tick compares against the APPLIED geometry.
		xrandr --auto 2>/dev/null
		cur=$(xrandr --current 2>/dev/null)
		hash=$(printf '%s\n' "$cur" | grep -E 'connected|current' | head -n 2 | cut -c1-64 | tr -d ' \t')
		last=$hash
		steady=0
		interval=2
	else
		steady=$((steady + 1))
		if [ "$steady" -ge 4 ]; then
			interval=30
		fi
		# Safety net: once per minute in slow mode, re-apply unconditionally
		# (a regression that never changes the geometry signature still heals).
		if [ "$interval" = "30" ] && [ $((steady % 2)) -eq 0 ]; then
			xrandr --auto 2>/dev/null
		fi
	fi
	sleep "$interval"
done &
