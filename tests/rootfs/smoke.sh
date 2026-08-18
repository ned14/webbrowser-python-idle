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

	# Tk file viewer deps: Pillow (with the Tk bridge _imagingtk) + mistune
	python3 -c "import PIL, PIL.ImageTk, mistune" || { echo "FAIL: PIL.ImageTk/mistune import failed" >&2; exit 1; }

	# Core binaries (nc comes from the base busybox)
	for cmd in xterm i3 git ssh nc pip3; do
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
	grep -q "open-file-explorer.sh" /home/user/.config/i3/config || { echo "FAIL: i3 does not autostart the file explorer" >&2; exit 1; }
	# Keep-alive: the explorer is relaunched when the last window closes
	[ -x /usr/local/bin/keep-file-explorer.sh ] || { echo "FAIL: keep-file-explorer.sh missing" >&2; exit 1; }
	grep -q "keep-file-explorer.sh" /home/user/.config/i3/config || { echo "FAIL: i3 does not autostart the keep-alive daemon" >&2; exit 1; }
	# File explorer -> IDLE integration: the explorer launches IDLE for .py
	# files and replaces itself on screen until IDLE exits.
	[ -f /usr/local/bin/file-explorer.py ] || { echo "FAIL: file-explorer.py missing" >&2; exit 1; }
	[ -f /usr/local/bin/file-explorer-tests.py ] || { echo "FAIL: file-explorer-tests.py missing" >&2; exit 1; }
	[ -x /usr/local/bin/open-file-explorer.sh ] || { echo "FAIL: open-file-explorer.sh missing" >&2; exit 1; }
	grep -q "idle3.10-launcher" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not launch IDLE" >&2; exit 1; }
	grep -q "root.withdraw()" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not yield the screen to IDLE" >&2; exit 1; }
	grep -q "root.deiconify()" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not reappear after IDLE" >&2; exit 1; }
	grep -q "load_folder(self.current_path)" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not refresh after IDLE" >&2; exit 1; }
	[ -x /usr/local/bin/idle3.10-launcher ] || { echo "FAIL: idle3.10-launcher missing" >&2; exit 1; }
	# Tk file viewer integration: the explorer routes non-Python files to the
	# viewer and swaps screens with it; the keep-alive daemon guards it.
	[ -f /usr/local/bin/file-viewer.py ] || { echo "FAIL: file-viewer.py missing" >&2; exit 1; }
	[ -x /usr/local/bin/file-viewer.py ] || { echo "FAIL: file-viewer.py not executable" >&2; exit 1; }
	[ -f /usr/local/bin/file-viewer-tests.py ] || { echo "FAIL: file-viewer-tests.py missing" >&2; exit 1; }
	grep -q "file-viewer.py" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not launch the viewer" >&2; exit 1; }
	grep -q "_open_in_viewer" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer lacks the viewer swap" >&2; exit 1; }
	grep -q "file-viewer.py" /usr/local/bin/keep-file-explorer.sh || { echo "FAIL: keep-alive does not guard the viewer" >&2; exit 1; }
	# The starter .py that doubles as the E2E IDLE-swap target
	[ -f /home/user/hello.py ] || { echo "FAIL: ~/hello.py missing" >&2; exit 1; }
	# The replaced GTK file managers must be GONE (deps no longer installed)
	command -v pcmanfm >/dev/null && { echo "FAIL: pcmanfm still installed" >&2; exit 1; }
	command -v spacefm >/dev/null && { echo "FAIL: spacefm still installed" >&2; exit 1; }
	# Curriculum examples baked into ~/ and read-only (dir 0555, files 0444).
	# Check the mode bits, not -w: the smoke run is root, and root access
	# bypasses write permission, so a -w test would falsely pass.
	[ -d /home/user/python-examples ] || { echo "FAIL: ~/python-examples missing" >&2; exit 1; }
	[ -f /home/user/python-examples/snake-game.py ] || { echo "FAIL: ~/python-examples/snake-game.py missing" >&2; exit 1; }
	[ "$(stat -c %a /home/user/python-examples)" = "555" ] || { echo "FAIL: ~/python-examples not read-only (555)" >&2; exit 1; }
	[ "$(stat -c %a /home/user/python-examples/snake-game.py)" = "444" ] || { echo "FAIL: example file not read-only (444)" >&2; exit 1; }
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

echo "==> rootfs GUI tests (file explorer, in-image Xvfb)"

docker run --rm --platform=linux/i386 --entrypoint /bin/sh "$IMAGE" -c '
	set -e
	# Run the full file-explorer test suite inside the guest, against an Xvfb
	# display (the same X/Tk stack the desktop uses, minus the CheerpX canvas).
	Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
	XPID=$!
	sleep 1
	RESULT=0
	timeout 600 env DISPLAY=:99 python3 /usr/local/bin/file-explorer-tests.py \
		>/tmp/fe-tests.log 2>&1 || RESULT=$?
	kill "$XPID" 2>/dev/null || true
	cat /tmp/fe-tests.log
	[ "$RESULT" = "0" ] || { echo "FAIL: file-explorer tests exited $RESULT" >&2; exit 1; }
	grep -q "PASS ALL" /tmp/fe-tests.log || { echo "FAIL: file-explorer tests did not report PASS ALL" >&2; exit 1; }

	# Tk file viewer test suite (images via Pillow, text, Markdown via
	# mistune, Prev/Next navigation) under the same X/Tk stack.
	Xvfb :98 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb4.log 2>&1 &
	XPID4=$!
	sleep 1
	RESULT=0
	timeout 600 env DISPLAY=:98 python3 /usr/local/bin/file-viewer-tests.py \
		>/tmp/fv-tests.log 2>&1 || RESULT=$?
	kill "$XPID4" 2>/dev/null || true
	cat /tmp/fv-tests.log
	[ "$RESULT" = "0" ] || { echo "FAIL: file-viewer tests exited $RESULT" >&2; exit 1; }
	grep -q "PASS ALL" /tmp/fv-tests.log || { echo "FAIL: file-viewer tests did not report PASS ALL" >&2; exit 1; }

	# A REAL viewer launch works under X: generate a PNG with Pillow, open it
	# in the viewer, and verify the process stays running.
	Xvfb :97 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb5.log 2>&1 &
	XPID5=$!
	sleep 1
	python3 - <<'EOF'
from PIL import Image
Image.new("RGB", (320, 200), (60, 120, 200)).save("/tmp/viewer-test.png")
EOF
	timeout 60 env DISPLAY=:97 /usr/local/bin/file-viewer.py /tmp/viewer-test.png \
		>/tmp/viewer.log 2>&1 &
	VIEWERPID=$!
	sleep 6
	if kill -0 "$VIEWERPID" 2>/dev/null; then
		echo "viewer launched and stayed running"
		pkill -f "file-viewer.py" 2>/dev/null || true
	else
		echo "FAIL: viewer exited early (log below)" >&2
		cat /tmp/viewer.log >&2 || true
		kill "$XPID5" 2>/dev/null || true
		exit 1
	fi
	kill "$XPID5" 2>/dev/null || true

	# A REAL IDLE launch (the explorer Popen target) works under X: start
	# the launcher on hello.py and verify the IDLE process stays running.
	Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb2.log 2>&1 &
	XPID2=$!
	sleep 1
	timeout 60 env DISPLAY=:99 /usr/local/bin/idle3.10-launcher /home/user/hello.py \
		>/tmp/idle.log 2>&1 &
	IDLEPID=$!
	sleep 6
	if kill -0 "$IDLEPID" 2>/dev/null; then
		echo "IDLE launched and stayed running"
		# Loopback TCP works on real Linux, so the launcher round-trip
		# probe must have succeeded and IDLE must run WITH its shell
		# subprocess (no -n). Assert the real idle3.10 process cmdline
		# carries no -n flag — a false -n here means the round-trip probe
		# regressed (the guest dead-accept workaround, plans/display-bug.md
		# §2.11). The pgrep pattern is escaped so it cannot match this
		# script body on pid 1 (which mentions idle3.10).
		IDLE_MAIN=$(pgrep -f "python3\.10 /usr/bin/idle3\.10" | head -1)
		if [ -n "$IDLE_MAIN" ] && \
			tr "\0" " " < "/proc/$IDLE_MAIN/cmdline" 2>/dev/null | grep -q -- " -n "; then
			echo "FAIL: launcher applied -n although loopback works (round-trip probe false negative)" >&2
			pkill -f idle3.10 2>/dev/null || true
			kill "$XPID2" 2>/dev/null || true
			exit 1
		fi
		if [ -z "$IDLE_MAIN" ]; then
			echo "FAIL: could not find the launched idle3.10 process" >&2
			pkill -f idle3.10 2>/dev/null || true
			kill "$XPID2" 2>/dev/null || true
			exit 1
		fi
		pkill -f idle3.10 2>/dev/null || true
	else
		echo "FAIL: IDLE exited early (log below)" >&2
		cat /tmp/idle.log >&2 || true
		kill "$XPID2" 2>/dev/null || true
		exit 1
	fi
	kill "$XPID2" 2>/dev/null || true

	# The keep-alive daemon relaunches the explorer when it dies: run i3 with
	# the real config (autostarts the explorer + keep-alive), kill the explorer
	# process, and verify the keep-alive brings it back.
	Xvfb :99 -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb3.log 2>&1 &
	XPID3=$!
	sleep 1
	export DISPLAY=:99
	i3 -c /home/user/.config/i3/config >/tmp/i3.log 2>&1 &
	I3PID=$!
	sleep 4
	if ! pgrep -f "file-explorer.py" >/dev/null 2>&1; then
		echo "FAIL: explorer did not start under i3 (i3 log tail below)" >&2
		tail -n 20 /tmp/i3.log >&2 || true
		kill "$I3PID" "$XPID3" 2>/dev/null || true
		exit 1
	fi
	pkill -9 -f "file-explorer.py"
	ALIVE=0
	for _i in 1 2 3 4 5 6 7 8 9 10; do
		sleep 2
		if pgrep -f "file-explorer.py" >/dev/null 2>&1; then ALIVE=1; break; fi
	done
	[ "$ALIVE" = "1" ] || { echo "FAIL: keep-alive did not relaunch the explorer" >&2; exit 1; }
	echo "keep-alive relaunched the explorer"
	kill "$I3PID" 2>/dev/null || true
	kill "$XPID3" 2>/dev/null || true
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
		# Boot pull + push daemon run as ONE process before X (networking-bug.md
		# §16.3: `sync-home.sh both` — pull, then the push loop in the same
		# process; never a second `daemon` invocation).
		grep -q "sync-home.sh both" /etc/local.d/desktop.start || { echo "FAIL: desktop.start lacks boot pull + push daemon (sync-home.sh both)" >&2; exit 1; }
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
