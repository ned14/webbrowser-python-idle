#!/usr/bin/env python3
"""Unit tests for the small guest desktop scripts: the shared pidfile
liveness guard (webvm-pidfile.sh), the single-instance explorer launcher
(open-file-explorer.sh), the IDLE launcher (idle3.14-launcher), and the
screen-resize cadence (99-screen-resize.sh). Sandboxed copies with fake
binaries — the same pattern as test_keepalive.py."""

import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PIDFILE_LIB = ROOT / "diskimage" / "rootfs" / "usr" / "local" / "lib" / "webvm-pidfile.sh"
OPEN_EXPLORER = ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "open-file-explorer.sh"
IDLE_LAUNCHER = ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "idle3.14-launcher"
SCREEN_RESIZE = ROOT / "diskimage" / "scripts" / "99-screen-resize.sh"

BUSYBOX = shutil.which("busybox") or ""


def _live_pid():
    return subprocess.Popen(["sleep", "60"])


def _reaped_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _zombie_pid():
    """Spawn a child and SIGKILL it WITHOUT reaping: it stays a zombie
    until this process reaps it (works only where /proc exists)."""
    pid = os.fork()
    if pid == 0:
        os._exit(0)
    os.kill(pid, 9)
    # Give the kernel a moment to mark it Z (it must be a zombie BEFORE
    # pidfile_alive checks it).
    time.sleep(0.1)
    return pid


# --------------------------------------------------------------------------
# webvm-pidfile.sh (the shared liveness guard)
# --------------------------------------------------------------------------


class TestPidfileAlive:
    @pytest.fixture()
    def sandbox(self, tmp_path):
        lib = tmp_path / "webvm-pidfile.sh"
        lib.write_text(PIDFILE_LIB.read_text())
        return tmp_path

    def _alive(self, sandbox, pidfile):
        cmd = ([BUSYBOX, "sh"] if BUSYBOX else ["sh"]) + ["-c",
             f'. {sandbox}/webvm-pidfile.sh; if pidfile_alive {pidfile}; then echo YES; else echo NO; fi']
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
        return res.stdout.strip() == "YES"

    def test_missing_pidfile_is_dead(self, sandbox):
        assert self._alive(sandbox, sandbox / "nope.pid") is False

    def test_live_pid_is_alive(self, sandbox):
        sleeper = _live_pid()
        try:
            pidfile = sandbox / "live.pid"
            pidfile.write_text(str(sleeper.pid))
            assert self._alive(sandbox, pidfile) is True
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_reaped_pid_is_dead(self, sandbox):
        pidfile = sandbox / "dead.pid"
        pidfile.write_text(str(_reaped_pid()))
        assert self._alive(sandbox, pidfile) is False

    @pytest.mark.skipif(not Path("/proc").exists(), reason="needs /proc (Linux)")
    def test_zombie_pid_is_dead(self, sandbox):
        """The keep-alive regression (2026-08-30): a SIGKILLed direct child
        stays an un-reaped zombie and kill -0 succeeds on it — the guard
        must treat state Z as dead or the desktop never relaunches."""
        zombie = _zombie_pid()
        try:
            pidfile = sandbox / "zombie.pid"
            pidfile.write_text(str(zombie))
            assert self._alive(sandbox, pidfile) is False
        finally:
            os.waitpid(zombie, 0)  # reap so the harness has no zombie

    @pytest.mark.skipif(not Path("/proc").exists(), reason="needs /proc (Linux)")
    def test_recycled_pid_with_starttime_mismatch_is_dead(self, sandbox):
        """The keep-alive regression (2026-08-30): a pid REUSED by an
        unrelated process passes kill -0 AND the zombie check, so the
        pidfile's recorded starttime (/proc/<pid>/stat field 22) is the only
        way to tell the record apart from the impostor — a mismatch must
        read as dead, or a killed explorer never relaunches."""
        sleeper = _live_pid()
        try:
            pidfile = sandbox / "recycled.pid"
            # A WRONG starttime for the live pid: the guard must reject it.
            pidfile.write_text(f"{sleeper.pid} 999999999999")
            assert self._alive(sandbox, pidfile) is False
            # The CORRECT starttime: alive.
            with open(f"/proc/{sleeper.pid}/stat") as f:
                starttime = f.read().split()[21]
            pidfile.write_text(f"{sleeper.pid} {starttime}")
            assert self._alive(sandbox, pidfile) is True
        finally:
            sleeper.kill()
            sleeper.wait()

    @pytest.mark.skipif(not Path("/proc").exists(), reason="needs /proc (Linux)")
    def test_live_pid_under_proc_is_alive(self, sandbox):
        sleeper = _live_pid()
        try:
            pidfile = sandbox / "live2.pid"
            pidfile.write_text(str(sleeper.pid))
            assert self._alive(sandbox, pidfile) is True
        finally:
            sleeper.kill()
            sleeper.wait()


# --------------------------------------------------------------------------
# open-file-explorer.sh (single-instance guard)
# --------------------------------------------------------------------------


class TestOpenFileExplorer:
    @pytest.fixture()
    def sandbox(self, tmp_path):
        text = OPEN_EXPLORER.read_text()
        text = text.replace("/usr/local/lib/webvm-pidfile.sh", str(tmp_path / "webvm-pidfile.sh"))
        text = text.replace("/tmp/explorer.pid", str(tmp_path / "explorer.pid"))
        text = text.replace("python3 /usr/local/bin/file-explorer.py",
                            "sh " + str(tmp_path / "fake-explorer.sh"))
        script = tmp_path / "open-file-explorer.sh"
        script.write_text(text)
        script.chmod(0o755)
        (tmp_path / "webvm-pidfile.sh").write_text(PIDFILE_LIB.read_text())
        (tmp_path / "fake-explorer.sh").write_text(
            f"#!/bin/sh\necho launched >> {tmp_path}/launch.log\n")
        (tmp_path / "fake-explorer.sh").chmod(0o755)
        return tmp_path

    def _run(self, sandbox):
        subprocess.run(["sh", str(sandbox / "open-file-explorer.sh")], check=True)
        log = sandbox / "launch.log"
        return log.read_text().splitlines() if log.exists() else []

    def test_launches_when_no_explorer_running(self, sandbox):
        assert self._run(sandbox) == ["launched"]

    def test_skips_launch_when_explorer_alive(self, sandbox):
        sleeper = _live_pid()
        try:
            (sandbox / "explorer.pid").write_text(str(sleeper.pid))
            assert self._run(sandbox) == [], "a live explorer must suppress the launch"
        finally:
            sleeper.kill()
            sleeper.wait()

    def test_launches_when_pidfile_stale(self, sandbox):
        (sandbox / "explorer.pid").write_text(str(_reaped_pid()))
        assert self._run(sandbox) == ["launched"]


# --------------------------------------------------------------------------
# idle3.14-launcher (loopback verdict -> -n vs plain idle)
# --------------------------------------------------------------------------


class TestIdleLauncher:
    @pytest.fixture()
    def sandbox(self, tmp_path):
        text = IDLE_LAUNCHER.read_text()
        text = text.replace("/usr/local/bin/idle-loopback-cache", str(tmp_path / "idle-loopback-cache"))
        text = text.replace("/tmp/idle.pid", str(tmp_path / "idle.pid"))
        text = text.replace("exec idle3.14", "exec " + str(tmp_path / "fake-idle.sh"))
        text = text.replace("exec idle3.14 -n", "exec " + str(tmp_path / "fake-idle.sh") + " -n")
        script = tmp_path / "idle3.14-launcher"
        script.write_text(text)
        script.chmod(0o755)
        (tmp_path / "fake-idle.sh").write_text(
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {tmp_path}/idle.args\n")
        (tmp_path / "fake-idle.sh").chmod(0o755)
        return tmp_path

    def _run(self, sandbox, verdict):
        cache = sandbox / "idle-loopback-cache"
        cache.write_text("#!/bin/sh\nexit %d\n" % verdict)
        cache.chmod(0o755)
        subprocess.run(
            ["sh", str(sandbox / "idle3.14-launcher"), "hello.py"],
            check=True, env={**os.environ, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
        args = sandbox / "idle.args"
        return args.read_text().strip() if args.exists() else ""

    def test_loopback_ok_launches_plain_idle(self, sandbox):
        assert self._run(sandbox, 0) == "hello.py"

    def test_loopback_dead_launches_inprocess_idle(self, sandbox):
        # -n is the documented fallback when the runtime's accept path is
        # dead (see idle-loopback-cache) — it must come BEFORE the file args.
        assert self._run(sandbox, 1) == "-n hello.py"


# --------------------------------------------------------------------------
# 99-screen-resize.sh (adaptive cadence)
# --------------------------------------------------------------------------


class TestScreenResize:
    @pytest.fixture()
    def sandbox(self, tmp_path):
        text = SCREEN_RESIZE.read_text()
        text = text.replace("xrandr", str(tmp_path / "xrandr"))
        text = text.replace("sleep", str(tmp_path / "sleep"))
        script = tmp_path / "99-screen-resize.sh"
        script.write_text(text)
        script.chmod(0o755)
        return tmp_path

    def _run(self, sandbox, initial_geometry, auto_geometry, ticks):
        """Run the resizer with scripted `xrandr`/`sleep`. `xrandr --current`
        answers `initial_geometry` until the first `--auto` re-application,
        which flips the geometry to `auto_geometry` (identical in the steady
        case — the hash filter must not see a change where none happened).
        The fake sleep records its durations and kills the loop once `ticks`
        sleeps have run; the test polls the log (the resizer backgrounds its
        loop, so the runner script exits immediately)."""
        log = sandbox / "calls.log"
        sleep_sh = sandbox / "sleep"
        sleep_sh.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            echo "$1" >> {log}
            n=$(grep -cE '^[0-9]+$' {log} 2>/dev/null || echo 0)
            [ "$n" -ge {ticks} ] && kill $PPID 2>/dev/null
            """))
        sleep_sh.chmod(0o755)
        xrandr = sandbox / "xrandr"
        xrandr.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            echo "$*" >> {log}
            if [ "$1" = "--current" ]; then
                cat {sandbox}/geometry.txt
            else
                printf '%s\\n' "{auto_geometry}" > {sandbox}/geometry.txt
            fi
            """))
        xrandr.chmod(0o755)
        (sandbox / "geometry.txt").write_text(initial_geometry)
        subprocess.run(["sh", str(sandbox / "99-screen-resize.sh")],
                       capture_output=True, text=True)
        # Poll until the loop dies (the script itself exits immediately; the
        # loop runs backgrounded).
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            lines = log.read_text().splitlines() if log.exists() else []
            if len([l for l in lines if l.isdigit()]) >= ticks:
                break
            time.sleep(0.05)
        return lines

    def test_stable_geometry_slows_to_30s_cadence(self, sandbox):
        geo = "Screen 0: connected 1024x768+0+0 current 1024 x 768"
        lines = self._run(sandbox, geo, geo, ticks=7)
        # Steady geometry: --auto runs once (first tick), then 2s ticks hold
        # for 4 steady cycles, then the 30s cadence (the safety-net --auto
        # lands on the even steady ticks in slow mode).
        sleeps = [int(l) for l in lines if l.isdigit()]
        assert sleeps[:4] == [2, 2, 2, 2], sleeps
        assert all(s == 30 for s in sleeps[4:]), sleeps
        autos = [l for l in lines if "--auto" in l]
        assert autos, "the first tick must apply the preferred mode"

    def test_geometry_change_reapplies_and_rearms_fast(self, sandbox):
        before = "Screen 0: connected 800x600+0+0 current 800 x 600"
        after = "Screen 0: connected 1024x768+0+0 current 1024 x 768"
        lines = self._run(sandbox, before, after, ticks=7)
        # The changed geometry is re-applied (--auto), and the cadence stays
        # fast while the output is changing.
        sleeps = [int(l) for l in lines if l.isdigit()]
        assert sleeps[0] == 2, sleeps
        autos = [l for l in lines if "--auto" in l]
        assert autos, "a geometry change must re-apply the preferred mode"
