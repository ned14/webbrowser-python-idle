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
from pathlib import Path

import pytest

TYPER_SH = Path(__file__).resolve().parents[2] / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "paste-typer.sh"

FAKE_XSK = """#!/bin/sh
while IFS= read -r LINE; do
	echo "$LINE" >> "$XSENDKEYS_LOG"
done
"""


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "fake-xsendkeys"
    fake.write_text(FAKE_XSK)
    fake.chmod(0o755)
    return {
        "PATH": "/usr/bin:/bin",
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
        [sys.executable and "/bin/sh", str(TYPER_SH)],
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
        res = run_typer(b"not a frame\nCXCLIP oops\nCXCLIP 3 aGk=\n", env)
        assert res.returncode == 0
        assert res.stdout == b""

    def test_oversize_refused(self, env):
        big = "x" * 1048577
        res = run_typer(clip_frame(big), env)
        assert res.stdout == b"CXFAIL toolarge\n"

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
            "usleep 10000", "key a",
            "usleep 10000", "key b",
            "usleep 10000", "key space",
            "usleep 10000", "key 1",
            "usleep 10000", "key 2",
        ]

    def test_uppercase_uses_shift_l(self, env):
        run_typer(clip_frame("Ab"), env)
        assert self.commands(env) == [
            "usleep 10000", "down Shift_L", "key a", "up Shift_L",
            "usleep 10000", "key b",
        ]

    def test_symbols_shift_table(self, env):
        run_typer(clip_frame("!{<~"), env)
        assert self.commands(env) == [
            "usleep 10000", "down Shift_L", "key 1", "up Shift_L",
            "usleep 10000", "down Shift_L", "key bracketleft", "up Shift_L",
            "usleep 10000", "down Shift_L", "key comma", "up Shift_L",
            "usleep 10000", "down Shift_L", "key grave", "up Shift_L",
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
