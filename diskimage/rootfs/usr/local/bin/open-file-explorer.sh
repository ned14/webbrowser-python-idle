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
exec python3 /usr/local/bin/file-explorer.py
