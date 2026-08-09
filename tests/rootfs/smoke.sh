#!/bin/sh
# Rootfs smoke tests for the built guest image.
# Usage: tests/rootfs/smoke.sh [browser|samba|webdav|none]
set -eu

BACKEND="${1:-browser}"
IMAGE="${IMAGE:-webvm-guest}"

echo "==> rootfs smoke ($BACKEND)"

docker run --rm --platform=linux/i386 --entrypoint /bin/sh -e BACKEND="$BACKEND" "$IMAGE" -c '
	set -e
	BACKEND="$BACKEND"

	# Python + IDLE (display-free checks only; E2E covers a real launch)
	python3 -c "import tkinter, idlelib"
	[ -x /usr/bin/idle3.10 ] || { echo "FAIL: /usr/bin/idle3.10 missing" >&2; exit 1; }

	# Core binaries (nc comes from the base busybox)
	for cmd in xterm pcmanfm i3 git ssh nc pip3; do
		command -v "$cmd" >/dev/null || { echo "FAIL: $cmd missing" >&2; exit 1; }
	done

	# Curriculum packages must be ABSENT (stdlib-only baseline)
	for pkg in numpy requests pytest; do
		if python3 -c "import $pkg" 2>/dev/null; then
			echo "FAIL: $pkg is installed (should not be)" >&2; exit 1
		fi
	done
	python3 -c "import pip" || { echo "FAIL: pip missing" >&2; exit 1; }

	# Desktop boot scaffolding
	[ -x /sbin/init ] || { echo "FAIL: /sbin/init missing" >&2; exit 1; }
	[ -x /etc/local.d/desktop.start ] || { echo "FAIL: desktop.start missing" >&2; exit 1; }
	[ -x /etc/X11/xinit/xinitrc.d/99-screen-resize.sh ] || { echo "FAIL: screen-resize missing" >&2; exit 1; }
	grep -q "dbus-run-session -- i3" /home/user/.xinitrc || { echo "FAIL: .xinitrc does not exec i3 under a session bus" >&2; exit 1; }
	grep -q "exec --no-startup-id idle3.10" /home/user/.config/i3/config || { echo "FAIL: i3 does not autostart idle" >&2; exit 1; }
	grep -q "Xorg :0" /etc/local.d/desktop.start || { echo "FAIL: desktop.start does not launch Xorg" >&2; exit 1; }
	grep -q "sh /home/user/.xinitrc" /etc/local.d/desktop.start || { echo "FAIL: desktop.start does not run the user session" >&2; exit 1; }
	[ -f /etc/runlevels/default/local ] || { echo "FAIL: openrc local service not enabled" >&2; exit 1; }
	# No gettys: the desktop must boot straight to X, never a console login
	grep -q "^tty[1-6]::respawn:/sbin/getty" /etc/inittab && { echo "FAIL: getty enabled (console login prompt)" >&2; exit 1; }

	# Git tooling baked; the SSH keypair is generated at first boot by
	# desktop.start (never baked into the served image)
	[ -f /home/user/.gitconfig ] || { echo "FAIL: .gitconfig missing" >&2; exit 1; }
	[ -f /home/user/.ssh/config ] || { echo "FAIL: ssh config missing" >&2; exit 1; }
	grep -q "ssh-keygen" /etc/local.d/desktop.start || { echo "FAIL: desktop.start lacks first-boot keygen" >&2; exit 1; }

	# Backend marker
	[ "$(cat /etc/webvm-backend)" = "$BACKEND" ] || { echo "FAIL: backend marker mismatch" >&2; exit 1; }
'

if [ "$BACKEND" = "samba" ] || [ "$BACKEND" = "webdav" ]; then
	docker run --rm --platform=linux/i386 --entrypoint /bin/sh -e BACKEND="$BACKEND" "$IMAGE" -c '
		set -e
		# Sync agent present + functional syncrc baked (user-readable)
		[ -x /usr/local/bin/sync-home.sh ] || { echo "FAIL: sync-home.sh missing" >&2; exit 1; }
		[ -f /usr/local/lib/webvm-sync/sync.py ] || { echo "FAIL: sync.py missing" >&2; exit 1; }
		[ -f /root/.syncrc ] || { echo "FAIL: /root/.syncrc missing" >&2; exit 1; }
		[ -f /home/user/.syncrc ] || { echo "FAIL: /home/user/.syncrc missing" >&2; exit 1; }
		grep -q "backend = $BACKEND" /home/user/.syncrc || { echo "FAIL: syncrc backend mismatch" >&2; exit 1; }
		# Boot pull runs before X
		grep -q "sync-home.sh pull" /etc/local.d/desktop.start || { echo "FAIL: desktop.start lacks boot pull" >&2; exit 1; }
		grep -q "sync-home.sh daemon" /etc/local.d/desktop.start || { echo "FAIL: desktop.start lacks push daemon" >&2; exit 1; }
		if [ "$BACKEND" = "samba" ]; then
			python3 -c "import smb" || { echo "FAIL: pysmb not importable" >&2; exit 1; }
		fi
	'
else
	docker run --rm --platform=linux/i386 --entrypoint /bin/sh -e BACKEND="$BACKEND" "$IMAGE" -c '
		set -e
		# No sync agent, no syncrc in browser/none modes
		[ ! -f /usr/local/bin/sync-home.sh ] || { echo "FAIL: sync-home.sh present in browser/none" >&2; exit 1; }
		[ ! -f /root/.syncrc ] || { echo "FAIL: /root/.syncrc present in browser/none" >&2; exit 1; }
	'
fi

echo "==> rootfs smoke PASS"
