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
	[ -x /usr/bin/idle3.14 ] || { echo "FAIL: /usr/bin/idle3.14 missing" >&2; exit 1; }

	# Tk file viewer deps: Pillow (with the Tk bridge _imagingtk) + mistune
	python3 -c "import PIL, PIL.ImageTk, mistune" || { echo "FAIL: PIL.ImageTk/mistune import failed" >&2; exit 1; }

	# Core binaries (nc comes from the base busybox)
	for cmd in xterm openbox xprop git ssh nc pip3; do
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
	grep -q "dbus-run-session -- openbox-session" /home/user/.xinitrc || { echo "FAIL: .xinitrc does not exec openbox-session under a session bus" >&2; exit 1; }
	grep -q "open-file-explorer.sh" /home/user/.config/openbox/autostart || { echo "FAIL: openbox autostart does not run the file explorer" >&2; exit 1; }
	# Keep-alive: the explorer is relaunched when the last window closes
	[ -x /usr/local/bin/keep-file-explorer.sh ] || { echo "FAIL: keep-file-explorer.sh missing" >&2; exit 1; }
	grep -q "keep-file-explorer.sh" /home/user/.config/openbox/autostart || { echo "FAIL: openbox autostart does not run the keep-alive daemon" >&2; exit 1; }
	# The openbox <mouse> section WIPES the built-in default bindings
	# (config.c parse_mouse -> mouse_unbind_all), so the titlebar ✕ Close
	# button must be re-bound explicitly or it renders but does nothing on
	# click (2026-08-18 fix). Without this the E2E close path and the desktop
	# keep-alive close->relaunch contract both silently break.
	grep -q "<context name=\"Close\">" /home/user/.config/openbox/rc.xml || { echo "FAIL: openbox rc.xml missing the Close-context mousebind (titlebar ✕ would do nothing)" >&2; exit 1; }
	grep -q "<action name=\"Close\"/>" /home/user/.config/openbox/rc.xml || { echo "FAIL: openbox rc.xml Close context has no Close action" >&2; exit 1; }
	# File explorer -> IDLE integration: the explorer launches IDLE for .py
	# files and disables its UI until IDLE exits.
	[ -f /usr/local/bin/file-explorer.py ] || { echo "FAIL: file-explorer.py missing" >&2; exit 1; }
	[ -f /usr/local/bin/file-explorer-tests.py ] || { echo "FAIL: file-explorer-tests.py missing" >&2; exit 1; }
	[ -x /usr/local/bin/open-file-explorer.sh ] || { echo "FAIL: open-file-explorer.sh missing" >&2; exit 1; }
	grep -q "idle3.14-launcher" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not launch IDLE" >&2; exit 1; }
	grep -q "_set_ui_enabled(False)" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not disable its UI while IDLE runs" >&2; exit 1; }
	grep -q "_set_ui_enabled(True)" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not re-enable its UI after IDLE" >&2; exit 1; }
	grep -q "load_folder(self.current_path)" /usr/local/bin/file-explorer.py || { echo "FAIL: explorer does not refresh after IDLE" >&2; exit 1; }
	[ -x /usr/local/bin/idle3.14-launcher ] || { echo "FAIL: idle3.14-launcher missing" >&2; exit 1; }
	# Tk file viewer integration: the explorer routes non-Python files to the
	# viewer and disables itself while it runs; the keep-alive daemon guards it.
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
	[ -d /home/user/examples ] || { echo "FAIL: ~/examples missing" >&2; exit 1; }
	[ -f /home/user/examples/snake-game.py ] || { echo "FAIL: ~/examples/snake-game.py missing" >&2; exit 1; }
	[ "$(stat -c %a /home/user/examples)" = "555" ] || { echo "FAIL: ~/examples not read-only (555)" >&2; exit 1; }
	[ "$(stat -c %a /home/user/examples/snake-game.py)" = "444" ] || { echo "FAIL: example file not read-only (444)" >&2; exit 1; }
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
	# Xvfb lifecycle helpers. Display numbers are reused across checks (the
	# IDLE and openbox checks both use :99), and a fresh server can collide
	# on a stale /tmp/.X99-lock left by the previous one still shutting
	# down — that made openbox fail with "Failed to open the display" on CI.
	# start_xvfb removes stale lock/socket and polls until the X socket
	# exists instead of a fixed sleep (Xvfb start is slow under qemu/CI
	# load); stop_xvfb waits for the server to fully exit so a reuse of the
	# same display number is always clean.
	_XVPID=""
	start_xvfb() {
		_disp="$1"
		rm -f "/tmp/.X${_disp}-lock" "/tmp/.X11-unix/X${_disp}"
		Xvfb ":$_disp" -screen 0 1280x800x24 -nolisten tcp >"/tmp/xvfb-$_disp.log" 2>&1 &
		_XVPID=$!
		for _i in 1 2 3 4 5 6 7 8 9 10; do
			if [ -e "/tmp/.X11-unix/X$_disp" ]; then
				break
			fi
			if ! kill -0 "$_XVPID" 2>/dev/null; then
				echo "FAIL: Xvfb :$_disp failed to start" >&2
				cat "/tmp/xvfb-$_disp.log" >&2
				return 1
			fi
			sleep 1
		done
		if ! kill -0 "$_XVPID" 2>/dev/null; then
			echo "FAIL: Xvfb :$_disp died before ready" >&2
			cat "/tmp/xvfb-$_disp.log" >&2
			return 1
		fi
	}
	stop_xvfb() {
		kill "$1" 2>/dev/null || true
		wait "$1" 2>/dev/null || true
	}
	# Run the full file-explorer test suite inside the guest, against an Xvfb
	# display (the same X/Tk stack the desktop uses, minus the CheerpX canvas).
	start_xvfb 99
	XPID=$_XVPID
	RESULT=0
	timeout 600 env DISPLAY=:99 python3 /usr/local/bin/file-explorer-tests.py \
		>/tmp/fe-tests.log 2>&1 || RESULT=$?
	stop_xvfb "$XPID"
	cat /tmp/fe-tests.log
	[ "$RESULT" = "0" ] || { echo "FAIL: file-explorer tests exited $RESULT" >&2; exit 1; }
	grep -q "PASS ALL" /tmp/fe-tests.log || { echo "FAIL: file-explorer tests did not report PASS ALL" >&2; exit 1; }

	# Tk file viewer test suite (images via Pillow, text, Markdown via
	# mistune, Prev/Next navigation) under the same X/Tk stack.
	start_xvfb 98
	XPID4=$_XVPID
	RESULT=0
	timeout 600 env DISPLAY=:98 python3 /usr/local/bin/file-viewer-tests.py \
		>/tmp/fv-tests.log 2>&1 || RESULT=$?
	stop_xvfb "$XPID4"
	cat /tmp/fv-tests.log
	[ "$RESULT" = "0" ] || { echo "FAIL: file-viewer tests exited $RESULT" >&2; exit 1; }
	grep -q "PASS ALL" /tmp/fv-tests.log || { echo "FAIL: file-viewer tests did not report PASS ALL" >&2; exit 1; }

	# A REAL viewer launch works under X: generate a PNG with Pillow, open it
	# in the viewer, and verify the process stays running.
	start_xvfb 97
	XPID5=$_XVPID
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
	stop_xvfb "$XPID5"

	# A REAL IDLE launch (the explorer Popen target) works under X: start
	# the launcher on hello.py and verify the IDLE process stays running.
	start_xvfb 99
	XPID2=$_XVPID
	timeout 60 env DISPLAY=:99 /usr/local/bin/idle3.14-launcher /home/user/hello.py \
		>/tmp/idle.log 2>&1 &
	IDLEPID=$!
	sleep 6
	if kill -0 "$IDLEPID" 2>/dev/null; then
		echo "IDLE launched and stayed running"
		# Loopback TCP works on real Linux, so the launcher round-trip
		# probe must have succeeded and IDLE must run WITH its shell
		# subprocess (no -n). Assert the real idle3.14 process cmdline
		# carries no -n flag — a false -n here means the round-trip probe
		# regressed (the guest dead-accept workaround, plans/display-bug.md
		# §2.11). The pgrep pattern is escaped so it cannot match this
		# script body on pid 1 (which mentions idle3.14). The middle
		# /usr/bin/python3.14 arg is optional: macOS Docker qemu-i386
		# emulation doubles the interpreter argv (python3.14 python3.14
		# idle3.14 ...), real x86 CI does not.
		IDLE_MAIN=$(pgrep -f "python3\.14 (/usr/bin/python3\.14 )?/usr/bin/idle3\.14" | head -1)
		if [ -n "$IDLE_MAIN" ] && \
			tr "\0" " " < "/proc/$IDLE_MAIN/cmdline" 2>/dev/null | grep -q -- " -n "; then
			echo "FAIL: launcher applied -n although loopback works (round-trip probe false negative)" >&2
			pkill -f idle3.14 2>/dev/null || true
			kill "$XPID2" 2>/dev/null || true
			exit 1
		fi
		if [ -z "$IDLE_MAIN" ]; then
			echo "FAIL: could not find the launched idle3.14 process" >&2
			pkill -f idle3.14 2>/dev/null || true
			kill "$XPID2" 2>/dev/null || true
			exit 1
		fi
		pkill -f idle3.14 2>/dev/null || true
	else
		echo "FAIL: IDLE exited early (log below)" >&2
		cat /tmp/idle.log >&2 || true
		kill "$XPID2" 2>/dev/null || true
		exit 1
	fi
	stop_xvfb "$XPID2"

	# The keep-alive daemon relaunches the explorer when it dies: run
	# openbox-session with the real config + autostart (which starts the
	# explorer + keep-alive), kill the explorer process, and verify the
	# keep-alive brings it back. Reuses display :99 right after the IDLE
	# check above — the stale-lock cleanup + readiness poll in start_xvfb
	# are exactly what keep this from racing the previous server.
	start_xvfb 99
	XPID3=$_XVPID
	export DISPLAY=:99
	export HOME=/home/user
	openbox-session >/tmp/openbox.log 2>&1 &
	OBPID=$!
	sleep 4
	if ! pgrep -f "file-explorer.py" >/dev/null 2>&1; then
		echo "FAIL: explorer did not start under openbox (openbox log tail below)" >&2
		tail -n 20 /tmp/openbox.log >&2 || true
		kill "$OBPID" "$XPID3" 2>/dev/null || true
		exit 1
	fi
	# Openbox must be managing the explorer as a decorated client window: the
	# EWMH root _NET_CLIENT_LIST must list it (wm-clients.sh counts it). This
	# is the Openbox analog of the old i3 "decorated titlebar window" guard —
	# a WM that never managed the window leaves the list empty. Poll briefly
	# for the window to map, then refuse if it never appears.
	CLIENT_OK=0
	for _c in 1 2 3 4 5 6 7 8 9 10; do
		COUNT=$(/usr/local/bin/wm-clients.sh --count 2>/dev/null || true)
		[ "${COUNT:-0}" = "0" ] || [ -z "${COUNT:-}" ] || { CLIENT_OK=1; break; }
		sleep 1
	done
	if [ "$CLIENT_OK" != "1" ]; then
		echo "FAIL: no client window in the Openbox _NET_CLIENT_LIST (WM not managing the explorer?)" >&2
		kill "$OBPID" "$XPID3" 2>/dev/null || true
		exit 1
	fi
	echo "Openbox client list contains a managed (decorated) window"
	pkill -9 -f "file-explorer.py"
	ALIVE=0
	for _i in 1 2 3 4 5 6 7 8 9 10; do
		sleep 2
		if pgrep -f "file-explorer.py" >/dev/null 2>&1; then ALIVE=1; break; fi
	done
	[ "$ALIVE" = "1" ] || { echo "FAIL: keep-alive did not relaunch the explorer" >&2; exit 1; }
	echo "keep-alive relaunched the explorer"
	# SECOND-generation kill (2026-08-30 regression): the relaunched explorer
	# is a DIRECT child of the keep-alive shell, so a SIGKILL leaves an
	# UNREAPED ZOMBIE — kill -0 keeps succeeding on it and a naive pidfile
	# guard would never relaunch again (the pidfile is removed by the
	# force-kill path + the zombie-aware webvm-pidfile.sh guard). Kill it
	# again and verify the desktop heals a second time.
	pkill -9 -f "file-explorer.py"
	ALIVE=0
	for _i in 1 2 3 4 5 6 7 8 9 10; do
		sleep 2
		if pgrep -f "file-explorer.py" >/dev/null 2>&1; then ALIVE=1; break; fi
	done
	[ "$ALIVE" = "1" ] || { echo "FAIL: keep-alive did not relaunch the second-generation explorer (zombie pidfile regression)" >&2; exit 1; }
	echo "keep-alive relaunched the second-generation explorer (zombie-safe)"
	kill "$OBPID" 2>/dev/null || true
	stop_xvfb "$XPID3"
'

# Paste typer delivery (the "text definitely appears in a text entry box"
# check): a CXCLIP frame piped to paste-typer.sh must type the text into a
# FOCUSED Tk Entry via the XTEST extension (xsendkeys — the exact
# production lane, no xdotool) and ack it. The entry's text is read back
# in-guest. This is the deterministic rootfs analog of the E2E delivery
# test: same script, same XTEST path, under Xvfb.
docker run --rm --platform=linux/i386 --entrypoint /bin/sh "$IMAGE" -c '
	set -e
	command -v xsendkeys >/dev/null || { echo "FAIL: xsendkeys missing" >&2; exit 1; }
	rm -f /tmp/.X96-lock /tmp/.X11-unix/X96
	Xvfb ":96" -screen 0 1280x800x24 -nolisten tcp >/tmp/xvfb-96.log 2>&1 &
	XVPID=$!
	for _i in 1 2 3 4 5 6 7 8 9 10; do
		[ -e /tmp/.X11-unix/X96 ] && break
		kill -0 "$XVPID" 2>/dev/null || { echo "FAIL: Xvfb :96 failed to start" >&2; exit 1; }
		sleep 1
	done
	kill -0 "$XVPID" 2>/dev/null || { echo "FAIL: Xvfb :96 died before ready" >&2; exit 1; }
	export DISPLAY=:96
	rm -f /tmp/paste-target.log /tmp/paste-typer.log
	cat > /tmp/paste-target.py <<PYEOF
import select
import sys
import tkinter as tk

root = tk.Tk()
root.title("paste-target")
entry = tk.Entry(root, width=80)
entry.pack(padx=10, pady=10)
entry.focus_force()
root.update()

last_value = ""
stable = 0
while True:
    root.update()
    value = entry.get()
    if value:
        if value == last_value:
            stable += 1
            if stable >= 15:
                print("PASTED:" + value, flush=True)
                root.destroy()
                sys.exit(0)
        else:
            last_value = value
            stable = 0
    else:
        last_value = ""
        stable = 0
    select.select([], [], [], 0.05)
PYEOF
	python3 /tmp/paste-target.py >/tmp/paste-target.log 2>&1 &
	TKPID=$!
	sleep 3
	printf "CXCLIP 13 aGVsbG8sIHdvcmxkIQ==\n" | \
		timeout 60 /usr/local/bin/paste-typer.sh \
		>/tmp/paste-typer.log 2>&1 || true
	FOUND=""
	for _i in 1 2 3 4 5 6 7 8 9 10; do
		if grep -q "PASTED:" /tmp/paste-target.log 2>/dev/null; then
			FOUND=1
			break
		fi
		sleep 1
	done
	FAIL_MSG=""
	if [ -z "$FOUND" ]; then
		FAIL_MSG="paste never landed in the Tk Entry"
	elif ! grep -q "PASTED:hello, world!" /tmp/paste-target.log; then
		FAIL_MSG="pasted text mismatch ($(cat /tmp/paste-target.log))"
	elif ! grep -q "CXACK 13" /tmp/paste-typer.log; then
		FAIL_MSG="typer did not ack the paste frame ($(cat /tmp/paste-typer.log))"
	fi
	kill "$TKPID" 2>/dev/null || true
	if [ -n "$FAIL_MSG" ]; then
		echo "FAIL: $FAIL_MSG" >&2
		cat /tmp/paste-target.log /tmp/paste-typer.log >&2 || true
		kill "$XVPID" 2>/dev/null || true
		exit 1
	fi
	echo "paste-typer typed into the focused Tk Entry and acked (PASTED:hello, world! / CXACK 13)"
	kill "$XVPID" 2>/dev/null || true
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
