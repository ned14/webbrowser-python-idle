#!/bin/sh
# wm-clients.sh — print the number of Openbox client windows.
#
# Openbox is an EWMH-compliant window manager but (unlike i3) has no
# `i3-msg -t get_tree`-style IPC to enumerate windows, so the keep-alive
# daemon counts the EWMH `_NET_CLIENT_LIST` root property instead: the window
# IDs of every client window the WM manages. The file explorer's window stays
# in the list while IDLE/the viewer is shown (it disables its UI rather than
# unmapping), so the count is the number of windows ON the desktop — the
# keep-alive relaunches only when that number is zero.
#
# This replaces the old wm-clients.py. That helper started a fresh Python
# interpreter on every poll just to run one xprop read; the count now lives in
# plain shell. It shells out to `xprop` (from the xorg-xprop / xprop package)
# because that is the smallest available X tool on this minimal guest. The
# file explorer does NOT use this helper: being a permanent Python process
# already, it reads the same property itself, in-process
# (file-explorer.py `_wm_client_windows`), so its 0.5–3 s window polls spawn
# no interpreter either.
#
# The keep-alive daemon's xprop -spy session feeds its parsed lines into
# `wm-clients.sh --count-line` (stdin), so the hex-window-id counting contract
# lives HERE exactly once (an earlier duplicate in the spy could drift).
#
# Usage:
#     wm-clients.sh --count       # read _NET_CLIENT_LIST via xprop, print the count
#     wm-clients.sh --count-line  # count the hex ids on a client-list line from stdin
#
# On any X/`xprop` failure this prints nothing and exits non-zero (for
# --count it prints nothing, mirroring the old `i3-msg`/wm-clients.py failure
# contract, which the keep-alive treats as "do not relaunch"). A window list
# that cannot be read is reported as an error, never as an empty desktop.
set -u

# Count the 0x… window ids in a _NET_CLIENT_LIST line (stdin). Unreadable
# lines (xprop's missing-atom errors come in BOTH case spellings across
# builds, plus "not found") exit 1 with no output — never "0", which would be
# read as an empty desktop. tr strips the space padding busybox wc adds, so a
# zero-count desktop prints exactly "0" (never "      0").
count_line() {
	LINE=$(cat)
	case "$LINE" in
		""|*"not found"*|*[Nn]o\ [Ss]uch\ [Aa]tom*) exit 1 ;;
	esac
	printf '%s\n' "$LINE" | grep -o '0x[0-9a-fA-F]\+' | wc -l | tr -d ' '
}

case "${1:-}" in
	--count-line)
		count_line
		;;
	--count)
		LIST=$(xprop -root _NET_CLIENT_LIST 2>/dev/null)
		printf '%s\n' "$LIST" | count_line
		;;
	*)
		echo "usage: wm-clients.sh --count|--count-line" >&2
		exit 1
		;;
esac
