#!/usr/bin/env python3
"""Script hygiene: sh -n on all shell scripts, py_compile on the sync agent."""

import py_compile
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SHELL_SCRIPTS = [
    ROOT / "build.sh",
    ROOT / "scripts" / "gen-certs.sh",
    ROOT / "scripts" / "print-url.sh",
    ROOT / "scripts" / "acceptance.sh",
    ROOT / "scripts" / "fetch-cheerpx-runtime.sh",
    ROOT / "server" / "entrypoint.sh",
    ROOT / "gateway" / "entrypoint.sh",
    ROOT / "diskimage" / "sync" / "sync-home.sh",
    ROOT / "diskimage" / "rootfs" / "etc" / "local.d" / "desktop.start",
    ROOT / "diskimage" / "scripts" / "99-screen-resize.sh",
    ROOT / "tests" / "rootfs" / "smoke.sh",
    ROOT / "tests" / "server" / "integration.sh",
    ROOT / "tests" / "server" / "join-test-client.sh",
]

PYTHON_SOURCES = [
    ROOT / "diskimage" / "sync" / "sync.py",
    ROOT / "tests" / "fixtures" / "fake_webdav.py",
]


@pytest.mark.parametrize("script", SHELL_SCRIPTS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_shell_scripts_parse(script):
    if not script.exists():
        pytest.skip("script not present")
    subprocess.run(["sh", "-n", str(script)], check=True)


@pytest.mark.parametrize("source", PYTHON_SOURCES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_python_sources_compile(source):
    if not source.exists():
        pytest.skip("source not present")
    py_compile.compile(str(source), doraise=True)
