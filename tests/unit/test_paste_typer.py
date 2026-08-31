#!/usr/bin/env python3
"""Unit tests for the guest paste typer
(diskimage/rootfs/usr/local/bin/paste-typer.sh).

The script is run as a subprocess with a FAKE xsendkeys backend: the script
spawns `XSENDKEYS_BIN` reading its command FIFO and the fake backend logs
each command line to `XSENDKEYS_LOG`. Frames are fed on the script's stdin;
assertions cover the CXACK/CXFAIL protocol, the typability gate, and the
exact xsendkeys command stream (keysyms + US-layout Shift_L handling).
No X server needed — the real XTEST path is exercised by the rootfs smoke
suite under Xvfb.
"""

import base64
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TYPER_SH = ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "paste-typer.sh"

FAKE_XSK = """#!/bin/sh
while IFS= read -r LINE; do
	echo "$LINE" >> "$XSENDKEYS_LOG"
done
"""

# Fake backend that CRASHES after XSK_MAX_LINES commands (a crashed
# xsendkeys — a C binary can die on an X error): write the line, then a
# short settle so the reaper can reap it, then exit.
FAKE_XSK_CRASHY = """#!/bin/sh
n=0
while IFS= read -r LINE; do
	echo "$LINE" >> "$XSENDKEYS_LOG"
	n=$((n + 1))
	if [ "$n" -ge "${XSK_MAX_LINES:-9999}" ]; then
		sleep 0.5
		exit 0
	fi
done
"""


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "fake-xsendkeys"
    fake.write_text(FAKE_XSK)
    fake.chmod(0o755)
    return {
        "PATH": "/usr/bin:/bin",
        # The typer sources the SHARED lib (webvm_supervise_start is the
        # backend lifecycle); point it at the repo copy and sandbox the
        # supervisor's pidfile/marker paths like every other path.
        "WEBVM_COMMON": str(ROOT / "scripts" / "lib" / "webvm-common.sh"),
        "WEBVM_SUPERVISE_DIR": str(tmp_path),
        "XSENDKEYS_BIN": str(fake),
        "XSENDKEYS_FIFO": str(tmp_path / "xsk.fifo"),
        "XSENDKEYS_LOG": str(tmp_path / "xsk.log"),
        "DISPLAY": ":99",
    }


def clip_frame(text):
    payload = text.encode("utf-8")
    return b"CXCLIP %d %s\n" % (len(payload), base64.b64encode(payload))


def run_typer(frames, env):
    return subprocess.run(
        ["/bin/sh", str(TYPER_SH)],
        input=frames,
        capture_output=True,
        env=env,
        timeout=30,
    )


# --------------------------------------------------------------------------
# Protocol: ACK/FAIL and refusals
# --------------------------------------------------------------------------

class TestProtocol:
    def test_good_frame_acks(self, env):
        res = run_typer(clip_frame("hello, world!"), env)
        assert res.returncode == 0, res.stderr
        assert res.stdout == b"CXACK 13\n"

    def test_empty_text_acks_zero(self, env):
        res = run_typer(clip_frame(""), env)
        assert res.stdout == b"CXACK 0\n"

    def test_multiple_frames(self, env):
        res = run_typer(clip_frame("one") + clip_frame("two"), env)
        assert res.stdout == b"CXACK 3\nCXACK 3\n"

    def test_malformed_lines_ignored(self, env):
        # Non-frames and non-numeric lengths are silently ignored; a frame
        # whose DECLARED length does not match its payload is CORRUPT and
        # answered CXFAIL (the page surfaces it immediately instead of
        # waiting out its ack timeout).
        res = run_typer(b"not a frame\nCXCLIP oops\nCXCLIP 3 aGk=\n", env)
        assert res.returncode == 0
        assert res.stdout == b"CXFAIL corrupt\n"

    def test_oversize_refused(self, env):
        big = "x" * 1048577
        res = run_typer(clip_frame(big), env)
        assert res.stdout == b"CXFAIL toolarge\n"

    def test_max_payload_boundary(self, env):
        # The exact MAX_PAYLOAD boundary, without the 1 MiB cost: the script
        # accepts a PASTE_MAX_PAYLOAD override (same pattern as the
        # XSENDKEYS_* overrides) — the boundary logic is identical.
        env["PASTE_MAX_PAYLOAD"] = "4"
        res = run_typer(clip_frame("abcd"), env)
        assert res.stdout == b"CXACK 4\n"
        res = run_typer(clip_frame("abcde"), env)
        assert res.stdout == b"CXFAIL toolarge\n"

    def test_length_mismatch_fails_corrupt(self, env):
        # Declared length != decoded length: a CORRUPT frame — the typer
        # answers CXFAIL so the page surfaces the failure immediately
        # instead of waiting out its ack timeout.
        res = run_typer(b"CXCLIP 99 " + base64.b64encode(b"abc") + b"\n", env)
        assert res.stdout == b"CXFAIL corrupt\n"
        assert Path(env["XSENDKEYS_LOG"]).exists() is False

    def test_invalid_base64_fails_corrupt(self, env):
        # Undecodable base64: same corrupt-frame handling.
        res = run_typer(b"CXCLIP 3 !!!\n", env)
        assert res.stdout == b"CXFAIL corrupt\n"

    def test_untypable_refused_with_reason(self, env):
        res = run_typer(clip_frame("café"), env)
        assert b"CXFAIL untypable char U+00E9 at index 3" in res.stdout

    def test_control_char_refused(self, env):
        res = run_typer(clip_frame("ab\x01cd"), env)
        assert b"CXFAIL untypable char U+0001 at index 2" in res.stdout

    def test_nul_byte_refused(self, env):
        res = run_typer(clip_frame("a\x00b"), env)
        assert b"CXFAIL untypable char U+0000 at index 1" in res.stdout

    def test_trailing_newline_preserved(self, env):
        # Trailing newline is legal paste content — must not be stripped.
        res = run_typer(clip_frame("hi\n"), env)
        assert res.stdout == b"CXACK 3\n"


# --------------------------------------------------------------------------
# Command stream (fake backend log): keysyms + Shift_L, pacing
# --------------------------------------------------------------------------

class TestCommandStream:
    def commands(self, env):
        log = Path(env["XSENDKEYS_LOG"])
        if not log.exists():
            return []  # a refused frame never writes anything
        return log.read_text().splitlines()

    def test_lowercase_and_digits(self, env):
        run_typer(clip_frame("ab 12"), env)
        assert self.commands(env) == [
            "usleep 5000", "key a",
            "usleep 5000", "key b",
            "usleep 5000", "key space",
            "usleep 5000", "key 1",
            "usleep 5000", "key 2",
        ]

    def test_uppercase_uses_shift_l(self, env):
        run_typer(clip_frame("Ab"), env)
        assert self.commands(env) == [
            "usleep 5000", "down Shift_L", "key a", "up Shift_L",
            "usleep 5000", "key b",
        ]

    def test_symbols_shift_table(self, env):
        run_typer(clip_frame("!{<~"), env)
        assert self.commands(env) == [
            "usleep 5000", "down Shift_L", "key 1", "up Shift_L",
            "usleep 5000", "down Shift_L", "key bracketleft", "up Shift_L",
            "usleep 5000", "down Shift_L", "key comma", "up Shift_L",
            "usleep 5000", "down Shift_L", "key grave", "up Shift_L",
        ]

    def test_unshifted_punctuation(self, env):
        run_typer(clip_frame(".,;'[]\\/=`-"), env)
        names = [c for c in self.commands(env) if c.startswith("key ")]
        assert names == [
            "key period", "key comma", "key semicolon", "key apostrophe",
            "key bracketleft", "key bracketright", "key backslash",
            "key slash", "key equal", "key grave", "key minus",
        ]

    def test_escapes_map_to_keysyms(self, env):
        run_typer(clip_frame("a\nb\tc\bd"), env)
        keys = [c for c in self.commands(env) if c.startswith("key ")]
        assert keys == ["key a", "key Return", "key b", "key Tab",
                        "key c", "key BackSpace", "key d"]

    def test_refused_frame_writes_nothing(self, env):
        run_typer(clip_frame("café"), env)
        assert self.commands(env) == []


# --------------------------------------------------------------------------
# Full printable-ASCII round trip (all 95 chars must translate)
# --------------------------------------------------------------------------

class TestAllPrintableAscii:
    def test_all_printable_ascii_acks_and_translates(self, env):
        text = "".join(chr(c) for c in range(0x20, 0x7F))
        res = run_typer(clip_frame(text), env)
        assert res.stdout == b"CXACK 95\n"
        keys = Path(env["XSENDKEYS_LOG"]).read_text().splitlines()
        # One `key` per character (the shift pairs wrap each char's key).
        key_lines = [k for k in keys if k.startswith("key ")]
        assert len(key_lines) == 95


# --------------------------------------------------------------------------
# Backend crash recovery: a dead xsendkeys must be respawned before the next
# frame (a direct child would linger as an un-reaped zombie, kill -0 would
# succeed on it, and every later paste would be written into an unread FIFO
# and silently lost — the daemon double-forks so the real backend is
# reparented and reaped, making kill -0 truthful).
# --------------------------------------------------------------------------

class TestBackendRespawn:
    def test_respawns_dead_backend_before_next_frame(self, env, tmp_path):
        crashy = tmp_path / "fake-xsendkeys-crashy"
        crashy.write_text(FAKE_XSK_CRASHY)
        crashy.chmod(0o755)
        env["XSENDKEYS_BIN"] = str(crashy)
        env["XSK_MAX_LINES"] = "4"  # one frame ("ab" = usleep,key,usleep,key)

        proc = subprocess.Popen(
            ["/bin/sh", str(TYPER_SH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            proc.stdin.write(clip_frame("ab"))
            proc.stdin.flush()
            assert proc.stdout.readline() == b"CXACK 2\n"
            # Let the fake consume its 4 commands, exit and get reaped.
            time.sleep(2)
            proc.stdin.write(clip_frame("cd"))
            proc.stdin.flush()
            assert proc.stdout.readline() == b"CXACK 2\n"
        finally:
            proc.stdin.close()
            proc.wait(timeout=10)

        lines = Path(env["XSENDKEYS_LOG"]).read_text().splitlines()
        # Both frames' command streams must reach a (re)spawned backend:
        # 4 lines for "ab" + 4 for "cd". Without the respawn the second
        # frame would be written into an unread FIFO and vanish.
        assert len(lines) == 8, f"expected 8 typed commands, got: {lines}"
