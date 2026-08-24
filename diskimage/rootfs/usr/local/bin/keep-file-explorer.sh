#!/bin/sh
# Keep the file explorer (/usr/local/bin/file-explorer.py) running, and
# SELF-HEAL a stuck launch.
#
# The explorer is the desktop's only permanent window. When IDLE is opened
# from it ("Open with IDLE"), the explorer WITHDRAWS itself — the process stays
# alive but windowless, and re-shows itself once IDLE exits. This daemon:
#   1. Polls the window manager's client list for the number of real windows
#      (via wm-clients.py, which reads the EWMH _NET_CLIENT_LIST root property
#      Openbox maintains — withdrawn windows drop out of it, exactly as they
#      used to disappear from i3's tree).
#   2. Relaunches the explorer only when zero windows exist AND no explorer
#      process is running. The process guard is what keeps the desktop safe
#      around the IDLE swap: while IDLE is up the explorer is alive-but-
#      withdrawn, and a relaunch must not stack a second explorer on top of
#      IDLE. When the user closes the explorer its process exits, so zero
#      windows + no process = relaunch immediately.
#   3. If the desktop stays windowless for STUCK_SECONDS while an explorer
#      process exists, force-kills it and relaunches — a deadlocked Tk startup
#      cannot hold the desktop empty forever.
#
# Process liveness uses the PID files written by the explorer / viewer /
# IDLE-launcher (NOT pgrep -f): the CheerpX core's /proc/<pid>/cmdline read
# traps the emulator for processes still being set up, so the fix shim
# (diskimage/faccessat-fix.c) returns EOF for cmdline reads and pgrep -f can
# no longer see command lines. See diskimage/faccessat-fix.c for the defect.
#
# wm-clients.py errors (empty count output) must NOT trigger a relaunch —
# hence the strict `= "0"` comparison.

set -u

STUCK_SECONDS=30
POLL_SECONDS=3
EXPLORER_PIDFILE=/tmp/explorer.pid
IDLE_PIDFILE=/tmp/idle.pid
VIEWER_PIDFILE=/tmp/viewer.pid

# True if the pid recorded in $1 is a live process.
pidfile_alive() {
	[ -f "$1" ] || return 1
	kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null
}

count_windows() {
	# Returns the number of open program windows on stdout, or nothing (and a
	# non-zero exit) if the WM client list cannot be read.
	/usr/local/bin/wm-clients.py --count 2>/dev/null
}

explorer_running() {
	pidfile_alive "$EXPLORER_PIDFILE"
}

idle_running() {
	# IDLE launched from the explorer: while it runs, the explorer is
	# intentionally windowless (withdrawn), so it must NOT be treated as stuck.
	pidfile_alive "$IDLE_PIDFILE"
}

viewer_running() {
	# The Tk file viewer (file-viewer.py) launched from the explorer: same
	# model as idle_running — the explorer is withdrawn while the viewer is
	# up, and a slow viewer startup must not read as a stuck desktop.
	pidfile_alive "$VIEWER_PIDFILE"
}

launch() {
	# Backgrounded (not exec) so the keep-alive keeps polling. The launcher
	# guards against a second instance itself, but a fresh process means a
	# fresh chance at mapping a window.
	/usr/local/bin/open-file-explorer.sh >/dev/null 2>&1 &
}

# Timestamp (seconds) of the last time a window was observed.
WINDOWLESS_SINCE=0

while :; do
	sleep "$POLL_SECONDS"
	NOW=$(date +%s 2>/dev/null || echo 0)
	# Clock glitch (date failed -> NOW=0): never let a stale WINDOWLESS_SINCE
	# count deliver an immediate force-kill once the clock recovers — restart
	# the windowless window instead.
	if [ "$NOW" = "0" ]; then
		WINDOWLESS_SINCE=0
	fi
	# A WM-list failure ("") is treated as "no windows known": a wedged
	# explorer keeps its window unmapped, so _NET_CLIENT_LIST can legitimately
	# be unreadable. The idle/viewer process guards below still protect the
	# IDLE/viewer swap (while they run, the force-kill never fires), so the
	# stuck-explorer self-heal must NOT be disabled by a wm-clients failure.
	WINS=$(count_windows 2>/dev/null)
	if [ "$WINS" = "0" ] || [ -z "$WINS" ]; then
		if explorer_running; then
		# Windowless but alive: either still mapping its window, or
		# withdrawn while IDLE / the viewer is being shown. The force-kill
		# applies only when neither is running (a withdrawn explorer behind
		# a running app is healthy). A stuck explorer gets STUCK_SECONDS,
		# then is force-killed so the desktop can start fresh.
		if ! idle_running && ! viewer_running && [ "$WINDOWLESS_SINCE" != "0" ] && \
				 [ "$NOW" -gt "$WINDOWLESS_SINCE" ] && \
				 [ $((NOW - WINDOWLESS_SINCE)) -ge "$STUCK_SECONDS" ]; then
				kill -9 "$(cat "$EXPLORER_PIDFILE" 2>/dev/null)" 2>/dev/null
				sleep 1
				WINDOWLESS_SINCE=$NOW
			elif [ "$WINDOWLESS_SINCE" = "0" ]; then
				WINDOWLESS_SINCE=$NOW
			fi
		else
			# No explorer at all (closed or crashed): relaunch now.
			launch
			WINDOWLESS_SINCE=$NOW
		fi
	else
		WINDOWLESS_SINCE=0
	fi
done
