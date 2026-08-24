#!/usr/bin/env python3
"""Behavioral unit tests for the keep-alive daemon
(diskimage/rootfs/usr/local/bin/keep-file-explorer.sh).

The daemon is a POSIX shell script whose observable contract is:

  1. CLOSING the explorer (zero client windows + dead explorer pid) MUST
     relaunch it. This is the regression guard for the xprop -spy rewrite:
     an earlier revision performed launch() inside the spy pipeline's
     subshell and could drop the close event entirely.
  2. Unparsable client-list lines (xprop's missing-atom error — emitted in
     BOTH lower-case "no such atom" and title-case "No such atom" spellings
     across xprop builds — and "not found") are SKIPPED: never treated as a
     zero-window desktop, which would cause spurious launches.
  3. A windowless-but-alive explorer behind a live IDLE/viewer process is
     the intentional IDLE-swap withdraw and must NEVER be force-killed.
  4. A windowless-alive explorer with no IDLE/viewer is STUCK after
     STUCK_SECONDS: force-killed AND relaunched (a killed windowless
     explorer emits no property update, so without an explicit launch the
     desktop would stay empty until the next spy session).

The tests sandbox the production script: paths (/tmp pid files, count file,
launcher) are rewritten into a tmp dir and the timing constants are shrunk,
then the daemon runs under the host busybox sh with a scripted fake `xprop`
on PATH. The fake publishes scenario lines gated on go-files so tests sync
on the daemon's own count file instead of sleeping blindly.

CheerpX-specific runtime behavior (real Xorg/Openbox under emulation, real
launcher/python startup) is covered by tests/rootfs/smoke.sh; these tests
lock the decision logic itself.
"""

import os
import pathlib
import subprocess
import textwrap
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
DAEMON = ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "keep-file-explorer.sh"

ONE_WINDOW = "_NET_CLIENT_LIST(WINDOW): window id # 0x1a00003"
ZERO_WINDOWS = "_NET_CLIENT_LIST(WINDOW): window id #"
NO_SUCH_ATOM_LOWER = "_NET_CLIENT_LIST:  no such atom on any window."
NOT_FOUND = "_NET_CLIENT_LIST: not found."


def _sandbox(script_tmp: pathlib.Path) -> dict[str, str]:
    """Materialize a sandboxed copy of the daemon + fake launcher + fake
    xprop, and return the env the daemon must run with."""
    replacement = {
        "/usr/local/bin/open-file-explorer.sh": str(script_tmp / "launcher.sh"),
        "/tmp/explorer.pid": str(script_tmp / "explorer.pid"),
        "/tmp/idle.pid": str(script_tmp / "idle.pid"),
        "/tmp/viewer.pid": str(script_tmp / "viewer.pid"),
        "/tmp/.keep-alive-count": str(script_tmp / "count"),
        # Shrink timings: stuck after 2s, spy session 4s, 0.5s ticks/grace.
        "STUCK_SECONDS=30": "STUCK_SECONDS=2",
        "SESSION_SECONDS=60": "SESSION_SECONDS=4",
        "POLL_SECONDS=2": "POLL_SECONDS=1",
        "BACKOFF_SECONDS=2": "BACKOFF_SECONDS=1",
        "STARTUP_GRACE_SECONDS=3": "STARTUP_GRACE_SECONDS=1",
    }
    text = DAEMON.read_text()
    for old, new in replacement.items():
        assert text.count(old) >= 1, f"pattern missing from daemon: {old}"
        text = text.replace(old, new)
    daemon_copy = script_tmp / "keep-file-explorer.sh"
    daemon_copy.write_text(text)
    daemon_copy.chmod(0o755)

    launcher = script_tmp / "launcher.sh"
    launcher.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        # Fake open-file-explorer.sh: record the launch, then pose as the
        # explorer by writing OUR pid to the pidfile and staying alive (the
        # daemon's liveness guard reads exactly this file).
        echo $$ > {script_tmp}/explorer.pid
        echo launch >> {script_tmp}/launch.log
        sleep 60
        """))
    launcher.chmod(0o755)

    xprop = script_tmp / "bin" / "xprop"
    xprop.parent.mkdir(exist_ok=True)
    xprop.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        # Fake xprop -spy: print scenario line 1 immediately, then each next
        # line only once its gate file appears (set by the test), then linger
        # until `timeout` ends the session. On a session restart the scenario
        # replays from the top — gates already touched flow through at once.
        first=1
        while IFS= read -r LINE; do
            if [ "$first" != "1" ]; then
                while [ ! -f "{script_tmp}/go" ]; do sleep 0.05; done
                sleep "${{XPROP_STEP:-0.2}}"
            fi
            first=0
            printf '%s\\n' "$LINE"
        done < "$XPROP_SCENARIO"
        sleep 300
        """))
    xprop.chmod(0o755)
    return {"PATH": f"{xprop.parent}:{os.environ['PATH']}"}


def _write_scenario(script_tmp: pathlib.Path, lines: list[str]) -> pathlib.Path:
    scenario = script_tmp / "scenario.txt"
    scenario.write_text("".join(line + "\n" for line in lines))
    return scenario


def _live_pid() -> subprocess.Popen:
    """A real alive process for kill -0 / kill -9 checks."""
    return subprocess.Popen(["sleep", "60"])


def _dead_pid() -> int:
    """A pid that is guaranteed NOT alive (started, reaped, gone)."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _wait_for(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    raise AssertionError(f"timed out after {timeout}s waiting for: {what}")


@pytest.fixture()
def vm(tmp_path: pathlib.Path):
    class VM:
        def __init__(self):
            self.dir = tmp_path
            self.env = _sandbox(self.dir)
            self.daemon = None

        def start(self, scenario_lines: list[str]):
            self.scenario = _write_scenario(self.dir, scenario_lines)
            env = dict(os.environ)
            env.update(self.env)
            env["XPROP_SCENARIO"] = str(self.scenario)
            self.daemon = subprocess.Popen(
                ["sh", str(self.dir / "keep-file-explorer.sh")],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        def release(self):
            (self.dir / "go").write_text("go\n")

        def set_explorer_pid(self, pid):
            (self.dir / "explorer.pid").write_text(str(pid))

        def set_idle_pid(self, pid):
            (self.dir / "idle.pid").write_text(str(pid))

        @property
        def launches(self) -> list[str]:
            log = self.dir / "launch.log"
            return log.read_text().splitlines() if log.exists() else []

        def read_count(self) -> str:
            count = self.dir / "count"
            return count.read_text().strip() if count.exists() else ""

        def stop(self):
            if self.daemon and self.daemon.poll() is None:
                self.daemon.kill()
                self.daemon.wait()

    vm = VM()
    yield vm
    vm.stop()


def test_closing_the_explorer_relaunches_it(vm):
    """THE regression guard: a zero-window event while the explorer process
    is dead MUST trigger a relaunch."""
    vm.start([ONE_WINDOW, ZERO_WINDOWS])
    vm.set_explorer_pid(_dead_pid())
    vm.release()  # publish ONE_WINDOW

    def one_window_applied():
        return vm.read_count() == "1"

    _wait_for(one_window_applied, 6, "first (healthy) count to be applied")
    assert vm.launches == [], "healthy desktop must not be relaunched"

    vm.release()  # publish ZERO_WINDOWS

    def relaunched():
        return len(vm.launches) >= 1

    _wait_for(relaunched, 8, "relaunch after the explorer was closed")


def test_unreadable_client_list_lines_are_skipped(vm):
    """xprop's missing-atom error arrives in two case spellings across builds;
    neither may ever be parsed as 'zero windows' (spurious relaunch)."""
    vm.start([NO_SUCH_ATOM_LOWER, NOT_FOUND])
    vm.set_explorer_pid(_dead_pid())
    vm.release()

    time.sleep(4)  # > several poll ticks + grace
    assert vm.launches == [], (
        "unreadable client-list lines must never trigger a relaunch "
        "(lower-case 'no such atom' regression)"
    )
    assert vm.read_count() == "", "failure lines must not be published as counts"


def test_withdrawn_explorer_behind_idle_is_not_killed(vm):
    """Zero windows + live explorer + live IDLE = the IDLE swap: the explorer
    is intentionally withdrawn and must survive past STUCK_SECONDS untouched."""
    sleeper = _live_pid()
    try:
        vm.start([ZERO_WINDOWS])
        vm.set_explorer_pid(sleeper.pid)
        vm.set_idle_pid(sleeper.pid)
        vm.release()

        # Stay observed well past STUCK_SECONDS (2s in the sandbox).
        time.sleep(6)
        assert sleeper.poll() is None, (
            "withdrawn explorer behind IDLE must not be force-killed"
        )
        assert len(vm.launches) == 0, (
            "withdrawn explorer behind IDLE must not be relaunched"
        )
    finally:
        sleeper.kill()
        sleeper.wait()


def test_stuck_windowless_explorer_is_force_killed_and_relaunched(vm):
    """Zero windows + live explorer + NO idle/viewer, held past STUCK_SECONDS:
    the daemon kills the stuck process AND relaunches immediately (the kill
    produces no property update, so the explicit launch is mandatory)."""
    stuck = _live_pid()
    try:
        vm.start([ZERO_WINDOWS])
        vm.set_explorer_pid(stuck.pid)
        vm.release()

        def healed():
            return stuck.poll() is not None and len(vm.launches) >= 1

        _wait_for(healed, 10, "stuck explorer force-kill + relaunch")
        assert stuck.returncode == -9, "stuck explorer must be SIGKILLed"
    finally:
        if stuck.poll() is None:
            stuck.kill()
            stuck.wait()


def test_healthy_desktop_is_left_alone(vm):
    """A mapped explorer window is health: no launches, no kills, even past
    STUCK_SECONDS."""
    sleeper = _live_pid()
    try:
        vm.start([ONE_WINDOW])
        vm.set_explorer_pid(sleeper.pid)
        vm.release()

        time.sleep(6)
        assert len(vm.launches) == 0, "healthy desktop must not be relaunched"
        assert sleeper.poll() is None, "healthy explorer must not be killed"
    finally:
        sleeper.kill()
        sleeper.wait()
