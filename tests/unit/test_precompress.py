#!/usr/bin/env python3
"""Unit tests for scripts/precompress-static.sh (the brotli sibling
generator run by every frontend build)."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "precompress-static.sh"


def _sandbox(tmp_path, brotli_mode="ok"):
    """A fake `brotli` on PATH + a fake build dir. Returns (env, build_dir)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if brotli_mode == "missing":
        pass  # no brotli at all
    else:
        brotli = bin_dir / "brotli"
        # Real-ish invocation: brotli -q 11 -c FILE > FILE.br. The fake
        # records the invoked file and writes a compressed sibling.
        brotli.write_text(
            "#!/bin/sh\n"
            'echo "$@" >> "%s/calls.log"\n'
            'input="$(echo "$@" | sed -n \'s/.*-c \\([^ ]*\\)$/\\1/p\')"\n'
            '[ -n "$input" ] && printf "br:%%s" "$(cat "$input")" > "$input.br"\n'
            % tmp_path
        )
        brotli.chmod(0o755)
    build = tmp_path / "build"
    build.mkdir()
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return env, build


def _run(env, build):
    return subprocess.run(
        ["sh", str(SCRIPT), str(build)],
        capture_output=True, text=True, env={**os.environ, **env},
    )


def test_compresses_expected_types_only(tmp_path):
    env, build = _sandbox(tmp_path)
    targets = {
        "app.js": "x" * 2000, "style.css": "y" * 2000, "page.html": "z" * 2000,
        "data.json": "{}" * 1000, "icon.svg": "<svg/>" + " " * 1100,
        "module.wasm": b"\0" * 2000,
    }
    for name, content in targets.items():
        (build / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    # Compressed formats / binary assets must NOT get siblings.
    (build / "photo.png").write_bytes(b"\x89PNG" * 500)
    (build / "font.woff2").write_bytes(b"wOF2" * 500)
    (build / "tower.ico").write_bytes(b"\0" * 500)

    res = _run(env, build)
    assert res.returncode == 0, res.stderr
    for name in targets:
        assert (build / (name + ".br")).exists(), f"{name} must get a .br sibling"
    for name in ("photo.png", "font.woff2", "tower.ico"):
        assert not (build / (name + ".br")).exists(), f"{name} must NOT get a sibling"

    # The 1 KiB threshold: small text files are skipped.
    (build / "tiny.js").write_text("var x=1;")
    res2 = _run(env, build)
    assert res2.returncode == 0
    assert not (build / "tiny.js.br").exists(), "sub-1KiB files must be skipped"


def test_never_emits_gzip_siblings(tmp_path):
    """The brotli_static-before-gzip_static precedence invariant: .gz
    siblings would shadow the smaller .br for every gzip-capable client."""
    env, build = _sandbox(tmp_path)
    (build / "app.js").write_text("x" * 2000)
    res = _run(env, build)
    assert res.returncode == 0, res.stderr
    assert (build / "app.js.br").exists()
    assert not (build / "app.js.gz").exists(), "no .gz siblings ever"


def test_missing_brotli_is_a_noop(tmp_path):
    env, build = _sandbox(tmp_path, brotli_mode="missing")
    (build / "app.js").write_text("x" * 2000)
    res = _run(env, build)
    assert res.returncode == 0, res.stderr
    assert "skipping precompression" in res.stderr
    assert not (build / "app.js.br").exists()


def test_missing_build_dir_fails(tmp_path):
    env, _ = _sandbox(tmp_path)
    res = _run(env, tmp_path / "no-such-build")
    assert res.returncode == 1
    assert "not found" in res.stderr
