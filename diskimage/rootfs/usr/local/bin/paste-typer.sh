#!/bin/sh
# WebVM guest paste typer — the SIMPLE paste lane, implemented in shell.
#
# Started once at boot by /etc/local.d/desktop.start with stdin/stdout on
# /dev/console (the page's console input/output channel). Reads `CXCLIP
# <len> <base64>` frames from stdin and types the payload into the
# X-input-focus window as XTEST fake input (the same key press/release
# pairs a human produces), printing `CXACK <len>` to stdout when done or
# `CXFAIL <reason>` when it could not.
#
# Architecture (why it is two pieces):
#   * xsendkeys (/usr/local/bin/xsendkeys, see diskimage/xsendkeys.c) is a
#     tiny C backend compiled in the Dockerfile build stage: it reads
#     `key <keysym>` / `down <keysym>` / `up <keysym>` / `usleep <us>`
#     command lines from its stdin and drives XTestFakeKeyEvent, XSync()ing
#     after EVERY command (without a round-trip the X server processes only
#     the first FakeInput and drops the rest — verified 2026-08-28).
#   * THIS script is the daemon: it owns the CXCLIP framing, the ASCII
#     typability gate, the char -> keysym-name translation (US keymap), and
#     the CXACK/CXFAIL protocol. It talks to xsendkeys through a FIFO it
#     holds open (exec 9<>), so there is NO process spawn per paste — the
#     only spawn is xsendkeys itself, once, at boot.
#
# Why this lane (verified 2026-08-27, CheerpX 1.3.8):
#   * the page cannot inject X keys — the runtime's X key path is driven by
#     the capture textarea's VALUE (real keystrokes only; synthetic events
#     yield zero EV_KEY);
#   * a guest X11 selection owner (xsel --input) traps the CheerpX core;
#   * the console tty input channel works (V1) and XTEST fake input works
#     cleanly when the target window holds the X input focus.
#
# CheerpX guest rules honored:
#   * The console tty is put in raw mode (stty raw -echo: ICANON/ECHO off,
#     VMIN=1) so a large frame is not line-canonicalized or echoed.
#   * No external spawns in the hot path: read/printf are ash builtins; the
#     only per-paste applets are base64/od/awk/wc (tiny busybox applets).
#   * The FIFO is opened O_RDWR (exec 9<>) so neither end blocks at boot.
#
# Typability rule (mirrors the page's check in WebVM.svelte): only
# printable ASCII plus \n \t \b can be typed out as keys. Everything else —
# control characters, all non-ASCII — is REFUSED with `CXFAIL untypable
# <detail>` and never typed wrong. The page refuses the same text before
# sending; this is the defense-in-depth second gate.
#
# Environment overrides (unit tests run without X):
#   WEBVM_COMMON      the shared lib (guest copy at /usr/local/lib/webvm-common.sh;
#                     tests point it at the repo copy)
#   XSENDKEYS_BIN     the backend binary (default xsendkeys)
#   XSENDKEYS_FIFO    the command FIFO path (default /tmp/xsendkeys.fifo)
#   XSENDKEYS_PIDFILE the backend pidfile (default: the shared supervisor's)
#   XSENDKEYS_STATUS  the backend status-marker path (default: the supervisor's)
#   DISPLAY           the X display (default :0)

MAX_PAYLOAD=${PASTE_MAX_PAYLOAD:-1048576}
# 5 ms per char (~200 chars/s) — halved from the original 10 ms contract for
# paste-heavy workloads (validated by the paste E2E: any dropped XTEST key
# shows up as missing characters there). XSync after every command is the
# real pacing mechanism (see xsendkeys.c), so the delay is belt-and-braces.
DELAY_US=${PASTE_DELAY_US:-5000}   # 5 ms per char — the ~200 chars/s typing contract
XSK_BIN=${XSENDKEYS_BIN:-xsendkeys}
XSK_FIFO=${XSENDKEYS_FIFO:-/tmp/xsendkeys.fifo}
# The backend lifecycle uses the SHARED supervisor contract (webvm_common
# webvm_supervise_start — the SAME marker mechanism the server/gateway
# entrypoints use): pidfile + status marker under $WEBVM_SUPERVISE_DIR.
WEBVM_SUPERVISE_DIR="${WEBVM_SUPERVISE_DIR:-/tmp}"
XSK_PIDFILE=${XSENDKEYS_PIDFILE:-$WEBVM_SUPERVISE_DIR/webvm-xsendkeys.pid}
XSK_STATUS=${XSENDKEYS_STATUS:-$WEBVM_SUPERVISE_DIR/webvm-xsendkeys.status}
DISPLAY=${DISPLAY:-:0}
export DISPLAY

# Shared defaults + helpers (the guest copy of scripts/lib/webvm-common.sh —
# the server/gateway entrypoints source the SAME file; the drift is pinned by
# tests/unit/test_scripts.py::test_guest_lib_copy_matches_shared_lib).
WEBVM_COMMON="${WEBVM_COMMON:-/usr/local/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"

emit() {
	printf '%s\n' "$1"
}

# --------------------------------------------------------------------------
# Backend lifecycle. The backend is spawned through the SHARED supervisor
# wrapper (webvm_supervise_start — the same wrapper the server/gateway
# entrypoints use for headscale/nginx/tailscaled):
#
#     webvm_supervise_start xsendkeys /dev/null "$XSK_BIN"
#
# runs the backend as its own child, records its real pid in
# $XSK_PIDFILE and writes the status marker $XSK_STATUS when it exits. The
# death check therefore reads the MARKER — not `kill -0`. A directly-
# backgrounded backend would linger as an UNREAPED ZOMBIE in the daemon's
# process table when it crashes (a C binary can die on an X error), and
# `kill -0` succeeds on zombies — the crash would never be seen and every
# later paste would be written into an unread FIFO and silently lost. The
# wrapper runs to completion (writing the marker) whenever the backend dies,
# regardless of who reaps what; the pidfile carries the BACKEND's real pid
# for the EXIT trap (killing the backend, not the wrapper). The wrapper
# subshell inherits this daemon's fds, so the backend's command FIFO (fd 9)
# passes through without any extra plumbing.
spawn_backend() {
	# stdout of the helper is the marker path; the console stream must not
	# see it (CXACK/CXFAIL frames live there). The backend's stdin must be
	# the command FIFO (fd 9): an asynchronous subshell's stdin is /dev/null,
	# so the shared wrapper duplicates fd 9 to the service's stdin via
	# WEBVM_SUPERVISE_STDIN_FD (the knob exists for exactly this — a
	# caller-held fd feeding a supervised service).
	WEBVM_SUPERVISE_STDIN_FD=9
	# PASTE_DEBUG=1 also routes the backend's stderr to the console so the
	# page can see xsendkeys' own diagnostics (XTEST presence, display
	# errors).
	if [ "${PASTE_DEBUG:-0}" = "1" ]; then
		PASTE_DEBUG=1 webvm_supervise_start xsendkeys /dev/console "$XSK_BIN" >/dev/null
	else
		webvm_supervise_start xsendkeys /dev/null "$XSK_BIN" >/dev/null
	fi
	# shellcheck disable=SC2034 # consumed by webvm_supervise_start (sourced lib)
	WEBVM_SUPERVISE_STDIN_FD=""
}

# One-time stdout/stderr flush helper for the awk failure reason file.
BADREASON=/tmp/paste-bad.$$

# --------------------------------------------------------------------------
# The translator: bytes (decimal, from od) -> xsendkeys command stream.
# Uses the US keymap: uppercase/symbols are Shift_L + the base key. On an
# untypable byte it decodes the UTF-8 code point for a meaningful
# diagnostic (matching the page's message), prints it to stderr and exits 1.
# --------------------------------------------------------------------------
translate() {
	awk -v delay="$DELAY_US" '
		{ for (i = 1; i <= NF; i++) a[++n] = $i + 0 }
		END {
			cidx = 0
			for (i = 1; i <= n; i++) {
				b = a[i]
				if (b == 10) { print "usleep " delay; print "key Return"; cidx++; continue }
				if (b == 9)  { print "usleep " delay; print "key Tab"; cidx++; continue }
				if (b == 8)  { print "usleep " delay; print "key BackSpace"; cidx++; continue }
				if (b < 32 || b > 126) {
					cp = b
					consumed = 1
					if (b >= 0xC2 && b <= 0xDF) consumed = 2
					else if (b >= 0xE0 && b <= 0xEF) consumed = 3
					else if (b >= 0xF0 && b <= 0xF4) consumed = 4
					if (consumed > 1) {
						ok = 1
						if (consumed == 2) cp = b % 32
						else if (consumed == 3) cp = b % 16
						else cp = b % 8
						for (j = 1; j < consumed; j++) {
							c2 = a[i + j]
							if (c2 < 0x80 || c2 > 0xBF) { ok = 0; break }
							cp = cp * 64 + (c2 % 64)
						}
						if (!ok) cp = b
					}
					printf "char U+%04X at index %d\n", cp, cidx > "/dev/stderr"
					exit 1
				}
				c = sprintf("%c", b)
				if (c ~ /^[a-z0-9]$/) { name = c; sh = 0 }
				else if (c ~ /^[A-Z]$/) { name = tolower(c); sh = 1 }
				else if (c == " ") { name = "space"; sh = 0 }
				else if (c == ".") { name = "period"; sh = 0 }
				else if (c == ",") { name = "comma"; sh = 0 }
				else if (c == ";") { name = "semicolon"; sh = 0 }
				else if (c == "\047") { name = "apostrophe"; sh = 0 }
				else if (c == "\"") { name = "apostrophe"; sh = 1 }
				else if (c == "[") { name = "bracketleft"; sh = 0 }
				else if (c == "]") { name = "bracketright"; sh = 0 }
				else if (c == "\\") { name = "backslash"; sh = 0 }
				else if (c == "/") { name = "slash"; sh = 0 }
				else if (c == "=") { name = "equal"; sh = 0 }
				else if (c == "-") { name = "minus"; sh = 0 }
				else if (c == "`") { name = "grave"; sh = 0 }
				else if (c == "!") { name = "1"; sh = 1 }
				else if (c == "@") { name = "2"; sh = 1 }
				else if (c == "#") { name = "3"; sh = 1 }
				else if (c == "$") { name = "4"; sh = 1 }
				else if (c == "%") { name = "5"; sh = 1 }
				else if (c == "^") { name = "6"; sh = 1 }
				else if (c == "&") { name = "7"; sh = 1 }
				else if (c == "*") { name = "8"; sh = 1 }
				else if (c == "(") { name = "9"; sh = 1 }
				else if (c == ")") { name = "0"; sh = 1 }
				else if (c == "_") { name = "minus"; sh = 1 }
				else if (c == "+") { name = "equal"; sh = 1 }
				else if (c == "{") { name = "bracketleft"; sh = 1 }
				else if (c == "}") { name = "bracketright"; sh = 1 }
				else if (c == "|") { name = "backslash"; sh = 1 }
				else if (c == ":") { name = "semicolon"; sh = 1 }
				else if (c == "<") { name = "comma"; sh = 1 }
				else if (c == ">") { name = "period"; sh = 1 }
				else if (c == "?") { name = "slash"; sh = 1 }
				else if (c == "~") { name = "grave"; sh = 1 }
				else {
					printf "char U+%04X at index %d\n", b, cidx > "/dev/stderr"
					exit 1
				}
				print "usleep " delay
				if (sh) print "down Shift_L"
				print "key " name
				if (sh) print "up Shift_L"
				cidx++
			}
		}
	'
}

# --------------------------------------------------------------------------
# One CXCLIP frame -> type it (or refuse it).
# --------------------------------------------------------------------------
handle() {
	LINE=$1
	case "$LINE" in
		CXCLIP*)
			REST=${LINE#CXCLIP }
			N=${REST%% *}
			B64=${REST#* }
			case "$N" in
				''|*[!0-9]*) return ;;
			esac
			[ "$N" -le "$MAX_PAYLOAD" ] || { emit "CXFAIL toolarge"; return; }
			# Decode and length-check as a PIPE (command substitution would
			# strip trailing newlines, which are legal paste content). A
			# length mismatch or undecodable base64 is a CORRUPT frame —
			# answered CXFAIL so the page surfaces the failure immediately
			# instead of waiting out its ack timeout.
			ACTUAL=$(printf '%s' "$B64" | base64 -d 2>/dev/null | wc -c | tr -d ' ')
			[ -n "$ACTUAL" ] && [ "$ACTUAL" = "$N" ] || { emit "CXFAIL corrupt"; return; }
			# Validate + translate (stderr carries the failure reason).
			CMDS=$(printf '%s' "$B64" | base64 -d 2>/dev/null | od -An -v -tu1 | translate 2>"$BADREASON") ||
			{
				REASON=$(cat "$BADREASON" 2>/dev/null)
				rm -f "$BADREASON"
				emit "CXFAIL untypable ${REASON:-unknown}"
				return
			}
			rm -f "$BADREASON"
			# The backend must be alive before writing (the FIFO write would
			# otherwise block forever with no reader). Death is signalled by
			# the wrapper's STATUS MARKER (see spawn_backend) — exact, and
			# immune to zombie/reaper quirks that make kill -0 lie.
			if [ -f "$XSK_STATUS" ]; then
				spawn_backend
			fi
			printf '%s\n' "$CMDS" >&9
			emit "CXACK $N"
			;;
	esac
}

# --------------------------------------------------------------------------
# Main: raw console, spawn the backend once, read frames.
# --------------------------------------------------------------------------
stty raw -echo 2>/dev/null || true
rm -f "$XSK_FIFO"
mkfifo "$XSK_FIFO" 2>/dev/null || true
# O_RDWR so neither the reader nor the writer blocks at boot; the script
# keeps fd 9 open for the lifetime of the daemon (the backend never sees
# EOF, so it never exits on its own).
exec 9<>"$XSK_FIFO"
spawn_backend

# Kill the backend on exit. The pidfile is read HERE, at trap time — never
# at spawn time: the wrapper writes it immediately at spawn, so by exit it
# is guaranteed present, while a spawn-time read can race a slow wrapper
# under load and come up empty — `kill 0` would then signal the whole
# process group instead of the backend, the backend would survive, the
# wrapper's `wait` would never return, and the wrapper (which inherits a
# copy of this daemon's stdout pipe) would hold that pipe open forever —
# a reader waiting for EOF hangs (observed 2026-08-30 under CI load).
# shellcheck disable=SC2154 # _xsk_pid IS assigned by the $(...) in this trap line
trap 'sleep 0.3; _xsk_pid=$(cat "$XSK_PIDFILE" 2>/dev/null || echo 0); [ "$_xsk_pid" != "0" ] && kill "$_xsk_pid" 2>/dev/null || true' EXIT

while IFS= read -r LINE; do
	handle "$LINE"
done
# Deterministic exit status for piped use (tests). In production stdin is
# /dev/console and never reaches EOF, so this is only reached on stdin EOF.
exit 0
