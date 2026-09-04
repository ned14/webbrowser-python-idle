#!/bin/sh
# Launch the file explorer unless one is already running. Used by the Openbox
# autostart, the W+Shift+f binding, and the keep-alive daemon, so the launch
# command (and the single-instance guard) live in exactly one place.
#
# While IDLE is open the explorer stays alive with its window up (UI
# disabled, covered by the maximized app), so a second launch must not
# happen — the guard keeps the desktop to a single explorer at all times.
#
# The single-instance guard checks the PID file written by the explorer
# (CheerpX core defect: `pgrep -f` scans every /proc/<pid>/cmdline, and a read
# of a process still being set up traps the emulator — see
# diskimage/faccessat-fix.c; the guest shim now returns EOF for cmdline reads,
# so the full command line is never available and pgrep -f cannot be used).
# The guard itself is the shared pidfile_alive helper (webvm-pidfile.sh),
# the same one the keep-alive daemon uses.
# shellcheck disable=SC1090
. /usr/local/lib/webvm-pidfile.sh

if pidfile_alive /tmp/explorer.pid; then
	exit 0
fi
# Write OUR pid to the pidfile BEFORE exec'ing python3 (exec keeps the same
# pid, so this is the explorer's own record): the keep-alive daemon's "no
# explorer process" detection reads this file, and on a slow machine the
# explorer's own early write (after the interpreter starts) can take seconds
# — without this, the daemon could see "no explorer" and stack a second
# launch (the 2026-09-04 slow-Chromebook hang). The full "pid starttime"
# record is written here (field 22 of /proc/self/stat — the recycled-pid
# guard in webvm-pidfile.sh); the explorer rewrites the same record as soon
# as it starts.
_STARTTIME=$(awk '{print $22}' /proc/self/stat 2>/dev/null || true)
echo "$$ $_STARTTIME" > /tmp/explorer.pid 2>/dev/null
exec python3 /usr/local/bin/file-explorer.py
