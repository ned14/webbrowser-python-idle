#!/bin/sh
# Shared pidfile-liveness guard for the desktop helper scripts
# (open-file-explorer.sh, keep-file-explorer.sh).
#
# Process liveness uses the PID files written by the explorer / viewer /
# IDLE-launcher (NOT pgrep -f): the CheerpX core's /proc/<pid>/cmdline read
# traps the emulator for processes still being set up, so the fix shim
# (diskimage/faccessat-fix.c) returns EOF for cmdline reads and pgrep -f
# can no longer see command lines. See diskimage/faccessat-fix.c.

# True if the pid recorded in $1 is a live process. The file may hold
# "pid" alone (the old format) or "pid starttime" (the format the explorer/
# viewer/launcher write now): the starttime is /proc/<pid>/stat field 22,
# recorded at write time and re-read at check time. A pid REUSED by an
# unrelated process (the kernel recycled the number after the original died)
# passes kill -0 and the zombie check but has a DIFFERENT starttime — the
# mismatch proves the recorded process is gone (2026-08-30: a recycled pid
# false-alived the guard, the keep-alive never relaunched the killed
# explorer, and the force-kill path waited out STUCK_SECONDS).
pidfile_alive() {
	[ -f "$1" ] || return 1
	# read without a trailing newline (plain `echo $! > pid` files) still
	# populates the vars — only its EXIT status is nonzero, so no `||`.
	read -r _pid _start < "$1" 2>/dev/null || true
	[ -n "$_pid" ] || return 1
	case "$_pid" in ''|*[!0-9]*) return 1 ;; esac
	kill -0 "$_pid" 2>/dev/null || return 1
	# A SIGKILLed child of the caller stays an UNREAPED ZOMBIE (the keep-
	# alive daemon never waits on its children), and kill -0 succeeds on
	# zombies — the pidfile would otherwise pin the guard forever (a killed
	# explorer never relaunches; the force-kill path never heals). The
	# reliable signal is /proc/<pid>/stat state "Z". The guest mounts proc;
	# hosts without proc (the macOS test sandbox) fall back to plain
	# kill -0.
	if [ -r "/proc/$_pid/stat" ]; then
		_state=$(awk '{print $3}' "/proc/$_pid/stat" 2>/dev/null) || return 1
		[ "$_state" != "Z" ] || return 1
		if [ -n "$_start" ]; then
			_startnow=$(awk '{print $22}' "/proc/$_pid/stat" 2>/dev/null) || return 1
			[ "$_startnow" = "$_start" ] || return 1
		fi
		return 0
	else
		return 0
	fi
}
