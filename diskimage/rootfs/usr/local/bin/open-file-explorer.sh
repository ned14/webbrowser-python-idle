#!/bin/sh
# Launch the file explorer unless one is already running. Used by the Openbox
# autostart, the W+Shift+f binding, and the keep-alive daemon, so the launch
# command (and the single-instance guard) live in exactly one place.
#
# While IDLE is open the explorer is merely withdrawn (process alive, no
# window), so a second launch must not happen — the guard keeps the desktop to
# a single explorer at all times.
#
# The single-instance guard checks the PID file written by the explorer
# (CheerpX core defect: `pgrep -f` scans every /proc/<pid>/cmdline, and a read
# of a process still being set up traps the emulator — see
# diskimage/faccessat-fix.c; the guest shim now returns EOF for cmdline reads,
# so the full command line is never available and pgrep -f cannot be used).
if [ -f /tmp/explorer.pid ] && kill -0 "$(cat /tmp/explorer.pid 2>/dev/null)" 2>/dev/null; then
	exit 0
fi
exec python3 /usr/local/bin/file-explorer.py
