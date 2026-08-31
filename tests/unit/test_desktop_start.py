#!/usr/bin/env python3
"""Execute /etc/local.d/desktop.start (the guest's X-desktop boot script) in a
chroot sandbox with stub binaries, asserting the untested boot logic:

  1. browser/none backends NEVER start the sync agent; the eth0-wait loop
     with no NIC never calls udhcpc (a raw socket on a missing interface
     crashes the CheerpX core), and idle-loopback-cache is consulted once.
  2. When eth0 appears, udhcpc is actually driven (the tailnet data path).
  3. samba/webdav backends start `sync-home.sh both` with HOME=/home/user
     (the pull+daemon single-process contract).
  4. The first-boot SSH keypair is generated only when it does not exist.

The sandbox mirrors test_entrypoint.py: busybox + stubs, with `sleep`
replaced by an instant-exit stub so the bounded retry loops complete in
milliseconds instead of minutes. Xorg is a record-and-exit stub, so the
script's X wait fails fast and the test observes everything that ran BEFORE
the X branch (fonts, eth0, sync, keygen). Runs only as root (the test-unit
compose container runs as root; skipped elsewhere).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DESKTOP_START = ROOT / "diskimage" / "rootfs" / "etc" / "local.d" / "desktop.start"

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0, reason="chroot sandbox tests need root"
)

BUSYBOX = shutil.which("busybox")
CHROOT = shutil.which("chroot")
if not BUSYBOX or not CHROOT:
    pytest.skip("busybox/chroot not available", allow_module_level=True)

APPLETS = (
    "sh", "echo", "printf", "mkdir", "cat", "sleep", "rm", "kill",
    "tail", "chmod", "chown", "touch",
)


def build_sandbox(tmp_path: Path, backend: str, key_exists: bool) -> Path:
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "local" / "bin").mkdir(parents=True)
    (root / "usr" / "local" / "sbin").mkdir(parents=True)
    (root / "etc" / "local.d").mkdir(parents=True)
    (root / "home" / "user" / ".ssh").mkdir(parents=True)
    (root / "run" / "user").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "dev").mkdir()
    (root / "dev" / "null").write_text("")
    (root / "dev" / "console").write_text("")

    # busybox is dynamically linked against musl: the loader must be inside
    # the chroot or execve fails with ENOENT.
    musl = Path("/lib/ld-musl-aarch64.so.1")
    if not musl.exists():
        musl = Path("/lib/ld-musl-x86_64.so.1")
    if musl.exists():
        (root / "lib").mkdir(exist_ok=True)
        shutil.copy2(musl, root / "lib" / musl.name)

    shutil.copy2(BUSYBOX, root / "bin" / "busybox")
    for applet in APPLETS:
        (root / "bin" / applet).symlink_to("busybox")

    # /etc/passwd so `chown user:user` resolves (busybox chown needs it).
    (root / "etc" / "passwd").write_text(
        "root:x:0:0:root:/root:/bin/sh\n"
        "user:x:1000:1000:user:/home/user:/bin/ash\n"
    )

    shutil.copy2(DESKTOP_START, root / "etc" / "local.d" / "desktop.start")
    (root / "etc" / "webvm-backend").write_text(backend + "\n")
    if key_exists:
        (root / "home" / "user" / ".ssh" / "id_ed25519").write_text("existing")

    def stub(path: Path, body: str):
        path.write_text(body)
        path.chmod(0o755)

    # Instant sleep: the bounded retry loops (60x2s eth0 wait, 240x0.25s X
    # wait, 5x5s udhcpc retry) must complete in milliseconds, not minutes.
    # /usr/bin (ahead of /bin in PATH) so the stub never writes THROUGH the
    # /bin/sleep -> busybox applet symlink (which would clobber busybox).
    stub(root / "usr" / "bin" / "sleep", "#!/bin/sh\nexit 0\n")

    # ip: `link show eth0` succeeds only when the marker file exists.
    stub(root / "usr" / "bin" / "ip", """#!/bin/sh
        case "$1 $2" in
            "link show") if [ -f /eth0-ready ]; then exit 0; else exit 1; fi ;;
            *) exit 0 ;;
        esac
        """)
    stub(root / "usr" / "bin" / "udhcpc",
         "#!/bin/sh\necho \"$*\" >> /tmp/udhcpc.args\nexit 0\n")
    stub(root / "usr" / "bin" / "su",
         "#!/bin/sh\necho \"$*\" >> /tmp/su.args\nexit 0\n")
    stub(root / "usr" / "bin" / "ssh-keygen",
         "#!/bin/sh\necho \"$*\" >> /tmp/ssh-keygen.args\nexit 0\n")
    # Xorg: record and exit — the X wait then fails fast and the test
    # observes everything that ran before the X branch.
    stub(root / "usr" / "bin" / "Xorg",
         "#!/bin/sh\necho \"$*\" >> /tmp/xorg.args\nexit 0\n")
    # Absolute-path guest helpers.
    stub(root / "usr" / "local" / "bin" / "idle-loopback-cache",
         "#!/bin/sh\necho \"$*\" >> /tmp/idlecache.args\nexit 0\n")
    stub(root / "usr" / "local" / "bin" / "sync-home.sh",
         "#!/bin/sh\necho \"HOME=$HOME|$*\" >> /tmp/sync.args\nexit 0\n")
    stub(root / "usr" / "local" / "bin" / "paste-typer.sh",
         "#!/bin/sh\nexit 0\n")
    return root


def run_desktop_start(root: Path, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        [CHROOT, str(root), "/bin/sh", "/etc/local.d/desktop.start"],
        capture_output=True, text=True, timeout=timeout,
    )


def _args(root: Path, name: str) -> list[str]:
    path = root / "tmp" / name
    if not path.exists():
        return []
    return path.read_text().splitlines()


def test_browser_backend_eth0_missing_no_udhcpc_no_sync(tmp_path):
    """`make up`-style boot with no NIC: the eth0 retry loop must give up
    without ever calling udhcpc (a raw socket on a missing interface crashes
    the CheerpX core), the loopback verdict is cached once, and browser mode
    never starts the sync agent."""
    root = build_sandbox(tmp_path, backend="browser", key_exists=False)
    res = run_desktop_start(root)
    # The X wait fails fast (Xorg stub never creates the socket).
    assert res.returncode == 1
    assert _args(root, "udhcpc.args") == [], "udhcpc must never run without eth0"
    assert _args(root, "sync.args") == [], "browser mode must not start the sync agent"
    assert len(_args(root, "idlecache.args")) == 1, "idle-loopback-cache must run once"
    # First-boot keygen runs (no key yet).
    assert _args(root, "su.args"), "first-boot ssh-keygen must run when no key exists"
    assert "ssh-keygen" in _args(root, "su.args")[0]


def test_eth0_present_drives_udhcpc(tmp_path):
    """When the emulated NIC appears (the browser-side Tailscale client
    connected), the data-path config must run udhcpc."""
    root = build_sandbox(tmp_path, backend="browser", key_exists=True)
    (root / "eth0-ready").write_text("")
    res = run_desktop_start(root)
    assert res.returncode == 1
    assert _args(root, "udhcpc.args"), "udhcpc must run when eth0 exists"
    assert "eth0" in _args(root, "udhcpc.args")[0]


def test_webdav_backend_starts_sync_home_both(tmp_path):
    """samba/webdav: the boot pull + push daemon run as ONE backgrounded
    process (sync-home.sh both) with HOME=/home/user — the CheerpX process-
    spawning workaround (never a second daemon invocation)."""
    root = build_sandbox(tmp_path, backend="webdav", key_exists=True)
    res = run_desktop_start(root)
    assert res.returncode == 1
    lines = _args(root, "sync.args")
    assert lines, "webdav mode must start sync-home.sh"
    assert all("HOME=/home/user" in l and "both" in l for l in lines), lines


def test_keygen_skipped_when_key_exists(tmp_path):
    """The SSH keypair is generated at FIRST boot only (never baked into the
    served image, and never regenerated across boots)."""
    root = build_sandbox(tmp_path, backend="browser", key_exists=True)
    res = run_desktop_start(root)
    assert res.returncode == 1
    assert _args(root, "su.args") == [], "keygen must be skipped when the key exists"


def test_samba_backend_starts_sync_home_both(tmp_path):
    """samba mode: same single-process sync contract as webdav."""
    root = build_sandbox(tmp_path, backend="samba", key_exists=True)
    res = run_desktop_start(root)
    assert res.returncode == 1
    lines = _args(root, "sync.args")
    assert lines and all("both" in l for l in lines), lines
