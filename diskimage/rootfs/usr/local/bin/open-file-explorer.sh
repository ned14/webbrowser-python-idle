#!/bin/sh
# Launch the file explorer unless one is already running. Used by the Openbox
# autostart, the W+Shift+f binding, and the keep-alive daemon, so the launch
# command (and the single-instance guard) live in exactly one place.
#
# While IDLE is open the explorer is merely withdrawn (process alive, no
# window), so a second launch must not happen — the guard keeps the desktop to
# a single explorer at all times.
if pgrep -f "file-explorer.py" >/dev/null 2>&1; then
	exit 0
fi
exec python3 /usr/local/bin/file-explorer.py
