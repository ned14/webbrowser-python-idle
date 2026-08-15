#!/bin/sh
# Single sync-agent runner (started by /etc/local.d/desktop.start as the
# `user` account). The boot pull and the push loop are one invocation chain so
# they cannot race the backend lease or the manifest.
set -eu

SYNC_PY=/usr/local/lib/webvm-sync/sync.py
CMD="${1:-pull}"

case "$CMD" in
	pull)
		exec python3 "$SYNC_PY" pull
		;;
	daemon)
		exec python3 "$SYNC_PY" daemon
		;;
	both)
		# One process runs the boot pull and then becomes the push daemon:
		# spawning a second process after the pull is unreliable under
		# CheerpX (background `su` children never run; the pull's process
		# teardown can wedge the guest), so the push loop continues in the
		# same process.
		exec python3 "$SYNC_PY" both
		;;
	*)
		echo "usage: sync-home.sh {pull|daemon|both}" >&2
		exit 1
		;;
esac
