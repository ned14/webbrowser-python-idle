#!/usr/bin/env python3
"""Unit tests for the guest sitecustomize patch
(diskimage/rootfs/usr/lib/python3.14/site-packages/sitecustomize.py).

The patch replaces time.sleep in EVERY guest interpreter with a select()-based
wait (select's timeout arm is the one CheerpX timer proven to fire — see the
file header). These tests verify the wrapper's semantics by exec()ing the file
against a FAKE time module, so the real (pytest) interpreter is never patched:

  * installing replaces module-level time.sleep with _cheerpx_sleep
  * a positive duration actually consumes wall time via the select path
  * zero / negative / None delegate to the original sleep unchanged

sync._sleep must delegate to time.sleep so it inherits the patch.
"""

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITECUSTOMIZE = (
    ROOT / "diskimage" / "rootfs" / "usr" / "lib" /
    "python3.14" / "site-packages" / "sitecustomize.py"
)
SYNC_PY = ROOT / "diskimage" / "sync" / "sync.py"
sys.path.insert(0, str(SYNC_PY.parent))

import sync  # noqa: E402


def _install_against_fake_time():
    """Exec sitecustomize with fake `time` and `select` modules in
    sys.modules; return (fake_time, patched_sleep, original_calls,
    FakeSelect). Restores sys.modules afterwards so the pytest interpreter's
    real modules are never touched."""
    saved_time = sys.modules.get("time")
    saved_select = sys.modules.get("select")
    fake = types.ModuleType("time")
    fake.monotonic = time.monotonic
    fake.perf_counter = time.perf_counter
    original_calls = []
    fake.sleep = lambda secs: original_calls.append(secs) or None

    class FakeSelectModule(types.ModuleType):
        """Stands in for the real select module; records timeouts and lets
        tests force the fallback branch."""
        active = True
        calls = []

        @classmethod
        def select(cls, r, w, x, timeout):
            cls.calls.append(timeout)
            if not cls.active:
                raise OSError("select unavailable")
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                time.sleep(0.005)
            return ([], [], [])

    fake_select = types.ModuleType("select")
    fake_select.select = FakeSelectModule.select

    ns = {"__name__": "sitecustomize"}
    code = SITECUSTOMIZE.read_text()
    sys.modules["time"] = fake
    sys.modules["select"] = fake_select
    try:
        exec(compile(code, str(SITECUSTOMIZE), "exec"), ns)
    finally:
        if saved_time is not None:
            sys.modules["time"] = saved_time
        else:
            del sys.modules["time"]
        if saved_select is not None:
            sys.modules["select"] = saved_select
        else:
            del sys.modules["select"]
    patched = fake.sleep
    assert patched.__name__ == "_cheerpx_sleep", "patch did not install"
    return fake, patched, original_calls, FakeSelectModule


def test_patch_replaces_time_sleep():
    fake, patched, _, _ = _install_against_fake_time()
    assert patched is fake.sleep
    assert fake.sleep.__name__ == "_cheerpx_sleep"


def test_positive_duration_consumes_wall_time_via_select():
    fake, patched, original_calls, spy = _install_against_fake_time()
    start = time.monotonic()
    patched(0.25)
    elapsed = time.monotonic() - start
    assert 0.2 <= elapsed <= 1.5, f"sleep(0.25) took {elapsed:.3f}s"
    assert spy.calls == [0.25], "must park via select, not busy-wait"
    assert original_calls == [], "working select path must not fall back"


def test_select_failure_falls_back_to_original():
    fake, patched, original_calls, spy = _install_against_fake_time()
    spy.active = False
    patched(0.1)
    assert original_calls == [0.1], "fallback must call the original sleep"
    assert spy.calls == [0.1], "fallback attempted select first"


def test_zero_negative_none_delegate_to_original():
    fake, patched, original_calls, spy = _install_against_fake_time()
    patched(0)
    patched(-5)
    patched(None)
    assert original_calls == [0, -5, None]
    assert spy.calls == [], "non-positive durations never touch select"


def test_sync_sleep_delegates_to_time_sleep(monkeypatch):
    """sync._sleep is now a thin shim: whatever time.sleep resolves to at
    call time (the patched one in the guest) does the waiting."""
    called = []
    monkeypatch.setattr(sync.time, "sleep", lambda s: called.append(s))
    sync._sleep(3)
    sync._sleep(0.5)
    assert called == [3, 0.5]
