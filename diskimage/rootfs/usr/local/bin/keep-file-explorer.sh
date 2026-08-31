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

# Shared pidfile-liveness guard (same helper open-file-explorer.sh uses, so
# the single-instance contract lives in one file). Overridable for tests.
WEBVM_PIDFILE_SH="${WEBVM_PIDFILE_SH:-/usr/local/lib/webvm-pidfile.sh}"
# shellcheck disable=SC1090
. "$WEBVM_PIDFILE_SH"

STUCK_SECONDS=30
SESSION_SECONDS=60
POLL_SECONDS=2
BACKOFF_SECONDS=2
STARTUP_GRACE_SECONDS=3
EXPLORER_PIDFILE=/tmp/explorer.pid
IDLE_PIDFILE=/tmp/idle.pid
VIEWER_PIDFILE=/tmp/viewer.pid
COUNT_FILE=/tmp/.keep-alive-count
# X session liveness: if the X server socket disappears (Xorg died mid-
# session), the desktop is gone and relaunching the explorer would just
# churn (it cannot open a dead display — every launch fails). The daemon
# then makes NO decisions until X returns. The socket is derived from
# $DISPLAY (":99" -> /tmp/.X11-unix/X99) — the production desktop runs :0,
# but the rootfs smoke drives the daemon on :99 (and any non-default
# display must not be misread as a dead X). Overridable for tests.
X_DISPLAY_NUM=$(printf '%s' "${DISPLAY:-:0}" | sed 's/^://; s/\..*$//')
X_SOCKET="${X_SOCKET:-/tmp/.X11-unix/X${X_DISPLAY_NUM:-0}}"
X_DOWN_REPORTED=0
# The hex-window-id counting contract lives in wm-clients.sh --count-line
# (the spy pipes each xprop line into it — no per-line execve chain beyond
# the one wm-clients invocation). Overridable for tests.
WM_CLIENTS_BIN="${WM_CLIENTS_BIN:-/usr/local/bin/wm-clients.sh}"

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
	# X gone: the desktop session ended — no relaunch, no force-kill (every
	# launch would fail against the dead display). Report once, then pause
	# decisions until the socket is back.
	if [ -n "$X_SOCKET" ] && [ ! -S "$X_SOCKET" ]; then
		if [ "$X_DOWN_REPORTED" != "1" ]; then
			echo "keep-alive: X socket missing ($X_SOCKET) — desktop session ended; pausing keep-alive" >&2
			X_DOWN_REPORTED=1
		fi
		return
	fi
	X_DOWN_REPORTED=0
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
				# The pidfile holds "pid starttime" (the recycled-pid guard);
				# only the first field is the pid.
				kill -9 "$(awk '{print $1}' "$EXPLORER_PIDFILE" 2>/dev/null)" 2>/dev/null
				sleep 1
				# The SIGKILLed explorer is (or will be) an un-reaped zombie
				# child of this shell — kill -0 keeps succeeding on it, so
				# the stale pidfile must be removed BEFORE the relaunch
				# guard is consulted again (open-file-explorer.sh would
				# otherwise refuse to start a second explorer forever).
				rm -f "$EXPLORER_PIDFILE"
				launch
				WINDOWLESS_SINCE=$NOW
			elif [ "$WINDOWLESS_SINCE" = "0" ]; then
				WINDOWLESS_SINCE=$NOW
			fi
		else
			# No explorer at all (closed or crashed): relaunch now. The
			# stale pidfile (a crashed explorer that never removed it) is
			# removed first — the single-instance guard would refuse.
			rm -f "$EXPLORER_PIDFILE"
			launch
			WINDOWLESS_SINCE=$NOW
		fi
	else
		WINDOWLESS_SINCE=0
	fi
}

rm -f "$COUNT_FILE"

# Parse-and-publish loop for ONE spy session (runs as its own shell via sh -c
# below, so it cannot use the parent's functions): pipe each xprop line into
# wm-clients.sh --count-line (the ONE place the hex-id counting lives) and
# write ONLY the resulting client-window count to COUNT_FILE. Unreadable
# lines exit non-zero there and are skipped entirely — never published as
# zero.
SPY_READER='
	xprop -spy -root _NET_CLIENT_LIST 2>/dev/null |
	while IFS= read -r LINE; do
		printf "%s\n" "$LINE" | "$WM_CLIENTS_BIN" --count-line > "$COUNT_FILE" 2>/dev/null || true
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
	COUNT_FILE="$COUNT_FILE" WM_CLIENTS_BIN="$WM_CLIENTS_BIN" \
		timeout "$SESSION_SECONDS" sh -c "$SPY_READER" &
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
