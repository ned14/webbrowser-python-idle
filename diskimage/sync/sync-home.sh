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
	*)
		echo "usage: sync-home.sh {pull|daemon}" >&2
		exit 1
		;;
esac
