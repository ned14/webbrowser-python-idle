#!/bin/sh
# Keep the file explorer (/usr/local/bin/file-explorer.py) running, and
# SELF-HEAL a stuck launch.
#
# The explorer is the desktop's only permanent window. When IDLE is opened
# from it ("Open with IDLE"), the explorer DISABLES its UI — the process and
# its window stay up, inert, under the maximized launched app — and re-enables
# itself once IDLE exits. This daemon:
#   1. Watches the window manager's client list for the number of real
#      windows: a long-lived `xprop -spy -root _NET_CLIENT_LIST` session,
#      which prints the CURRENT value as soon as it attaches and a fresh line
#      every time Openbox updates the EWMH root property. Earlier generations
#      polled (i3-msg, then wm-clients.py, then wm-clients.sh --count three
#      times a minute): each poll was a chain of ~6 execves (xprop + grep + wc
#      + tr + date ...) — expensive under CheerpX emulation. Here the spy
#      session parses its lines and writes ONLY the resulting count to
#      COUNT_FILE; the main shell picks changes up within POLL_SECONDS using
#      builtin-only reads (no per-tick forks).
#   2. Relaunches the explorer only when zero windows exist AND no explorer
#      process is running. The process guard keeps the desktop safe around
#      the IDLE swap: while IDLE is up the explorer is alive-but-inert, and a
#      relaunch must not stack a second explorer on top of IDLE. When the
#      user closes the explorer its process exits, so zero windows + no
#      process = relaunch.
#   3. If the desktop stays windowless for STUCK_SECONDS while an explorer
#      process exists, force-kills it and relaunches — a deadlocked Tk
#      startup cannot hold the desktop empty forever.
#
# ARCHITECTURE NOTE (why decisions live in the MAIN shell): every earlier
# revision of the spy design performed launch()/kill() from inside the spy
# pipeline's subshell. Besides dying with each session, a process spawned
# from a subshell parked in a pipe read() is exactly the pattern the sync
# agent notes flag as unreliable under CheerpX (desktop.start: backgrounded
# children of blocked shells never execute). So the subshell is now dumb —
# it only writes parsed counts to COUNT_FILE — and the main shell, a stable
# long-lived process, owns every action. The spy session is bounded with
# busybox `timeout` (SESSION_SECONDS) purely so a quiet desktop cannot pin
# the process forever; the main shell restarts it and the attach line
# re-syncs the current count immediately.
#
# A client-list line that cannot be parsed is SKIPPED — never evaluated as a
# zero-window desktop. xprop reports a missing/unreadable atom as
# "_NET_CLIENT_LIST:  no such atom on any window." (all lowercase — the
# title-case "No such atom" variant some builds print is matched too), so
# the filter below accepts both cases plus "not found".
#
# Process liveness uses the PID files written by the explorer / viewer /
# IDLE-launcher (NOT pgrep -f): the CheerpX core's /proc/<pid>/cmdline read
# traps the emulator for processes still being set up, so the fix shim
# (diskimage/faccessat-fix.c) returns EOF for cmdline reads and pgrep -f
# can no longer see command lines. See diskimage/faccessat-fix.c.
#
# Elapsed time uses date +%s wall-clock seconds. The tick loop runs ONLY in
# the main shell, so timestamps share one epoch (the earlier revision tried
# $SECONDS, but busybox builds disagree about auto-incrementing it — one of
# our two environments treats it as a plain variable, which silently froze
# every timer). A failed `date` yields NOW=0; apply_window_count skips that
# tick entirely so a recovered clock can never deliver a stale force-kill.

set -u

STUCK_SECONDS=30
SESSION_SECONDS=60
POLL_SECONDS=2
BACKOFF_SECONDS=2
STARTUP_GRACE_SECONDS=3
EXPLORER_PIDFILE=/tmp/explorer.pid
IDLE_PIDFILE=/tmp/idle.pid
VIEWER_PIDFILE=/tmp/viewer.pid
COUNT_FILE=/tmp/.keep-alive-count

# True if the pid recorded in $1 is a live process.
pidfile_alive() {
	[ -f "$1" ] || return 1
	kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null
}

explorer_running() {
	pidfile_alive "$EXPLORER_PIDFILE"
}

idle_running() {
	# IDLE launched from the explorer: while it runs, the explorer keeps its
	# window but stays inert (disabled UI), so it must NOT be treated as stuck.
	pidfile_alive "$IDLE_PIDFILE"
}

viewer_running() {
	# The Tk file viewer (file-viewer.py) launched from the explorer: same
	# model as idle_running — the explorer is disabled while the viewer is
	# up, and a slow viewer startup must not read as a stuck desktop.
	pidfile_alive "$VIEWER_PIDFILE"
}

launch() {
	# Backgrounded (not exec) so the keep-alive keeps watching. The launcher
	# guards against a second instance itself, but a fresh process means a
	# fresh chance at mapping a window. Runs in the MAIN shell.
	/usr/local/bin/open-file-explorer.sh >/dev/null 2>&1 &
}

# Apply the keep-alive decision for a client-window count observed at wall-
# clock second $2. Runs ONLY in the main shell.
apply_window_count() {
	WINS=$1
	NOW=$2
	# Clock glitch (date failed -> NOW=0): skip the tick entirely — a stale
	# timestamp must never arm a false force-kill, and the next good tick
	# re-evaluates reality anyway.
	if [ "$NOW" = "0" ]; then
		return
	fi
	if [ "$WINS" = "0" ]; then
		if explorer_running; then
			# Windowless but alive: either still mapping its window, or
			# inert (disabled UI) while IDLE / the viewer is being shown. The
			# force-kill applies only when neither is running (an inert
			# explorer behind a running app is healthy). A
			# stuck explorer gets STUCK_SECONDS, then is force-killed so
			# the desktop can start fresh.
			if ! idle_running && ! viewer_running && \
			   [ "$WINDOWLESS_SINCE" != "0" ] && \
			   [ "$NOW" -gt "$WINDOWLESS_SINCE" ] && \
			   [ $((NOW - WINDOWLESS_SINCE)) -ge "$STUCK_SECONDS" ]; then
				kill -9 "$(cat "$EXPLORER_PIDFILE" 2>/dev/null)" 2>/dev/null
				sleep 1
				launch
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
}

rm -f "$COUNT_FILE"

# Parse-and-publish loop for ONE spy session (runs as its own shell via sh -c
# below, so it cannot use the parent's functions): parse each xprop line and
# write ONLY the resulting client-window count to COUNT_FILE. Unreadable
# lines are skipped entirely — never published as zero.
SPY_READER='
	xprop -spy -root _NET_CLIENT_LIST 2>/dev/null |
	while IFS= read -r LINE; do
		case "$LINE" in
			""|*"not found"*|*[Nn]o\ [Ss]uch\ [Aa]tom*) continue ;;
		esac
		printf "%s\n" "$LINE" \
			| grep -o "0x[0-9a-fA-F]\+" | wc -l | tr -d " " > "$COUNT_FILE"
	done
'

SPY_PID=""
WINDOWLESS_SINCE=0

while :; do
	# One bounded spy session (see SPY_READER). `timeout` bounds the session
	# so a quiet desktop cannot pin the process (and cannot strand it: the
	# hard kill fires even if no further property update would ever trigger
	# a SIGPIPE); the session also ends the moment xprop itself dies (X
	# restarted / connection dropped) — the reader then sees EOF.
	COUNT_FILE="$COUNT_FILE" timeout "$SESSION_SECONDS" sh -c "$SPY_READER" &
	SPY_PID=$!
	GRACE_END=$(($(date +%s 2>/dev/null || echo 0) + STARTUP_GRACE_SECONDS))

	while kill -0 "$SPY_PID" 2>/dev/null; do
		sleep "$POLL_SECONDS"
		NOW=$(date +%s 2>/dev/null || echo 0)
		# Startup grace: the explorer process may not have written its pid
		# file yet and its window is not mapped yet, so an instant
		# zero-window evaluation would stack a second explorer on top of
		# it.
		if [ "$NOW" = "0" ] || [ "$NOW" -lt "$GRACE_END" ]; then
			continue
		fi
		WINS=""
		read -r WINS < "$COUNT_FILE" 2>/dev/null || WINS=""
		case "$WINS" in
			''|*[!0-9]*)
				# No count yet (or garbage): nothing decided this tick.
				continue ;;
		esac
		apply_window_count "$WINS" "$NOW"
	done

	# Session over (timeout or X death): reap, brief backoff, re-attach.
	# The next session's attach line republishes the current count within
	# POLL_SECONDS, so decisions simply continue.
	kill "$SPY_PID" 2>/dev/null
	wait "$SPY_PID" 2>/dev/null
	SPY_PID=""
	sleep "$BACKOFF_SECONDS"
done
