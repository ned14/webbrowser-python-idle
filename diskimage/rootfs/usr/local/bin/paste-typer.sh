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
#   XSENDKEYS_BIN   the backend binary (default xsendkeys)
#   XSENDKEYS_FIFO  the command FIFO path (default /tmp/xsendkeys.fifo)
#   DISPLAY         the X display (default :0)

MAX_PAYLOAD=1048576
DELAY_US=10000   # 10 ms per char — the ~100 chars/s typing contract
XSK_BIN=${XSENDKEYS_BIN:-xsendkeys}
XSK_FIFO=${XSENDKEYS_FIFO:-/tmp/xsendkeys.fifo}
DISPLAY=${DISPLAY:-:0}
export DISPLAY

emit() {
	printf '%s\n' "$1"
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
			# strip trailing newlines, which are legal paste content).
			ACTUAL=$(printf '%s' "$B64" | base64 -d 2>/dev/null | wc -c | tr -d ' ')
			[ -n "$ACTUAL" ] && [ "$ACTUAL" = "$N" ] || return
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
			# otherwise block forever with no reader).
			if ! kill -0 "$XSK_PID" 2>/dev/null; then
				( "$XSK_BIN" <&9 >/dev/null 2>&1 ) &
				XSK_PID=$!
				sleep 1
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
"$XSK_BIN" <&9 >/dev/null 2>&1 &
XSK_PID=$!
# Let the backend open the display before the first frame can arrive.
sleep 1

trap 'sleep 0.3; kill "$XSK_PID" 2>/dev/null || true' EXIT

while IFS= read -r LINE; do
	handle "$LINE"
done
# Deterministic exit status for piped use (tests). In production stdin is
# /dev/console and never reaches EOF, so this is only reached on stdin EOF.
exit 0
