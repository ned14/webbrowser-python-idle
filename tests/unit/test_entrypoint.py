#!/usr/bin/env python3
"""Execute server/entrypoint.sh in a chroot sandbox with stub binaries,
asserting the fail-closed per-mode secret checks and the WEBVM_TAILNET=off
rendering behave as documented.

The sandbox is a minimal rootfs: busybox + the applets the entrypoints use,
the entrypoint + shared lib, and stub binaries for envsubst/htpasswd/
headscale/nginx/wsgidav/python3. Runs only as root (the test-unit compose
container runs as root; skipped elsewhere).
"""

import os
import shutil
import signal
import socket
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER_ENTRYPOINT = ROOT / "server" / "entrypoint.sh"
GATEWAY_ENTRYPOINT = ROOT / "gateway" / "entrypoint.sh"
SHARED_LIB = ROOT / "scripts" / "lib" / "webvm-common.sh"

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0, reason="entrypoint execution tests need root (chroot sandbox)"
)

BUSYBOX = shutil.which("busybox")
CHROOT = shutil.which("chroot")
if not BUSYBOX or not CHROOT:
    pytest.skip("busybox/chroot not available", allow_module_level=True)

APPLETS = (
    "sh", "echo", "printf", "mkdir", "cat", "sleep", "seq", "sed", "grep",
    "awk", "sort", "kill", "rm", "tail", "head", "cut", "tr", "true", "false",
    "touch",
)

STUBS = {
    # envsubst: no substitution needed for the assertions here (cat is fine)
    "envsubst": "#!/bin/sh\ncat\n",
    # htpasswd: succeed and create the target file (the happy-path test
    # asserts the htpasswd artifact exists)
    "htpasswd": "#!/bin/sh\ntouch \"$2\"\nexit 0\n",
    # headscale: never reached by the fail-closed paths under test
    "headscale": "#!/bin/sh\nexit 0\n",
    # nginx: -t succeeds; the daemon form sleeps (the supervisor watches it)
    "nginx": "#!/bin/sh\ncase \"$1\" in\n\t-t) exit 0 ;;\n\t*) sleep 60 ;;\nesac\n",
    "wsgidav": "#!/bin/sh\nsleep 60\n",
    "python3": "#!/bin/sh\necho 'window.__webvmConfig = {};'\n",
    "tailscaled": "#!/bin/sh\nsleep 60\n",
    "tailscale": "#!/bin/sh\nexit 0\n",
    "socat": "#!/bin/sh\nsleep 60\n",
}


def build_sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "local" / "bin").mkdir(parents=True)
    (root / "etc" / "webvm" / "lib").mkdir(parents=True)
    (root / "dev").mkdir()
    (root / "dev" / "null").write_text("")  # a plain file: redirects work
    # The supervised-service wrappers (webvm_supervise_start) keep their
    # pidfiles + status markers in /tmp.
    (root / "tmp").mkdir()
    # Service logs (webvm_supervise_start redirects the service's fds there).
    (root / "var" / "log").mkdir(parents=True)

    # busybox is dynamically linked against musl: the loader must be inside
    # the chroot or execve fails with ENOENT (the interpreter lookup fails).
    musl = Path("/lib/ld-musl-aarch64.so.1")
    if not musl.exists():
        musl = Path("/lib/ld-musl-x86_64.so.1")
    if musl.exists():
        (root / "lib").mkdir(exist_ok=True)
        shutil.copy2(musl, root / "lib" / musl.name)

    shutil.copy2(BUSYBOX, root / "bin" / "busybox")
    for applet in APPLETS:
        (root / "bin" / applet).symlink_to("busybox")

    shutil.copy2(SERVER_ENTRYPOINT, root / "usr" / "local" / "bin" / "entrypoint.sh")
    shutil.copy2(GATEWAY_ENTRYPOINT, root / "usr" / "local" / "bin" / "gateway-entrypoint.sh")
    shutil.copy2(SHARED_LIB, root / "etc" / "webvm" / "lib" / "webvm-common.sh")

    # Templates the entrypoint renders (the envsubst stub is `cat`, so the
    # rendered output is the raw template — only existence is asserted).
    # (The CSP header has no template: the entrypoint renders it from
    # render-webvm-config.py --render-csp, whose sandbox python3 stub just
    # echoes — only csp.conf's existence is asserted.)
    (root / "etc" / "webvm" / "headscale").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "server" / "nginx.conf.template", root / "etc" / "webvm" / "nginx.conf.template")
    shutil.copy2(ROOT / "server" / "headscale" / "config.yaml.template",
                 root / "etc" / "webvm" / "headscale" / "config.yaml.template")
    shutil.copy2(ROOT / "server" / "wsgidav.yaml.template", root / "etc" / "webvm" / "wsgidav.yaml.template")
    shutil.copy2(ROOT / "server" / "render-webvm-config.py",
                 root / "etc" / "webvm" / "render-webvm-config.py")

    for name, body in STUBS.items():
        stub = root / "usr" / "bin" / name
        stub.write_text(body)
        stub.chmod(0o755)
    return root


def run_entrypoint(root: Path, script: str, env: dict, timeout: float = 30):
    full_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/root",
    }
    full_env.update(env)
    return subprocess.run(
        [CHROOT, str(root), script],
        env=full_env, capture_output=True, text=True, timeout=timeout,
    )


def test_webdav_without_preauthkey_fails_closed(tmp_path):
    root = build_sandbox(tmp_path)
    res = run_entrypoint(root, "/usr/local/bin/entrypoint.sh", {
        "STORAGE_BACKEND": "webdav",
    })
    assert res.returncode == 1
    assert "HEADSCALE_PREAUTHKEY" in res.stderr


def test_webdav_without_gateway_authkey_fails_closed(tmp_path):
    root = build_sandbox(tmp_path)
    res = run_entrypoint(root, "/usr/local/bin/entrypoint.sh", {
        "STORAGE_BACKEND": "webdav",
        "HEADSCALE_PREAUTHKEY": "hskey-auth-test",
    })
    assert res.returncode == 1
    assert "GATEWAY_AUTHKEY" in res.stderr


def test_webdav_without_webdav_credentials_fails_closed(tmp_path):
    root = build_sandbox(tmp_path)
    res = run_entrypoint(root, "/usr/local/bin/entrypoint.sh", {
        "STORAGE_BACKEND": "webdav",
        "HEADSCALE_PREAUTHKEY": "hskey-auth-test",
        "GATEWAY_AUTHKEY": "hskey-auth-gateway",
    })
    assert res.returncode == 1
    assert "WEBDAV_USER" in res.stderr


def test_webdav_without_gateway_tailnet_ip_fails_closed(tmp_path):
    root = build_sandbox(tmp_path)
    res = run_entrypoint(root, "/usr/local/bin/entrypoint.sh", {
        "STORAGE_BACKEND": "webdav",
        "HEADSCALE_PREAUTHKEY": "hskey-auth-test",
        "GATEWAY_AUTHKEY": "hskey-auth-gateway",
        "WEBDAV_USER": "webdav",
        "WEBDAV_PASS": "secret",
    })
    assert res.returncode == 1
    assert "GATEWAY_TAILNET_IP" in res.stderr


def test_browser_with_headscale_enabled_requires_both_keys(tmp_path):
    root = build_sandbox(tmp_path)
    res = run_entrypoint(root, "/usr/local/bin/entrypoint.sh", {
        "STORAGE_BACKEND": "browser",
        "HEADSCALE_ENABLED": "1",
        "HEADSCALE_PREAUTHKEY": "hskey-auth-test",
    })
    assert res.returncode == 1
    assert "GATEWAY_AUTHKEY" in res.stderr


def test_samba_without_preauthkey_fails_closed(tmp_path):
    """samba mode requires the control plane (the sync agent talks to the
    Samba server through the gateway) — a missing preauth key must fail
    closed exactly like webdav."""
    root = build_sandbox(tmp_path)
    res = run_entrypoint(root, "/usr/local/bin/entrypoint.sh", {
        "STORAGE_BACKEND": "samba",
    })
    assert res.returncode == 1
    assert "HEADSCALE_PREAUTHKEY" in res.stderr


def test_nginx_config_invalid_fails_closed(tmp_path):
    """A template/envsubst mistake must surface as a clear container
    failure (nginx -t rejects the rendered config), not as a silent
    half-broken boot — and the supervised services must be cleaned up."""
    root = build_sandbox(tmp_path)
    (root / "usr" / "bin" / "nginx").write_text(
        "#!/bin/sh\ncase \"$1\" in\n\t-t) exit 1 ;;\n\t*) sleep 60 ;;\nesac\n"
    )
    (root / "usr" / "bin" / "nginx").chmod(0o755)
    res = run_entrypoint(root, "/usr/local/bin/entrypoint.sh", {
        "STORAGE_BACKEND": "browser",
        "WEBVM_TAILNET": "off",
    })
    assert res.returncode == 1
    assert "nginx config invalid" in res.stderr


def test_webvm_tailnet_off_renders_empty_config(tmp_path):
    """`make up` is a HARD-NETWORKLESS launch: even with webdav env + no
    secrets, the entrypoint must render an EMPTY baked page config and start
    nginx (never fail on the tailnet secrets it must not need)."""
    root = build_sandbox(tmp_path)
    proc = subprocess.Popen(
        [CHROOT, str(root), "/usr/local/bin/entrypoint.sh"],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/root",
            "STORAGE_BACKEND": "webdav",
            "WEBVM_TAILNET": "off",
        },
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 20
        config = root / "etc" / "webvm" / "webvm-config.js"
        while time.monotonic() < deadline:
            if config.exists():
                break
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(
                    f"entrypoint exited early (rc={proc.returncode}): {err or out}"
                )
            time.sleep(0.2)
        assert config.read_text().strip() == "window.__webvmConfig = {};"
        assert (root / "etc" / "nginx" / "nginx.conf").exists()
        assert (root / "etc" / "nginx" / "csp.conf").exists()
        # The webdav-only renderings must NOT exist in a non-webdav launch
        # (the empty config is the whole point of the hard-networkless mode).
        assert not (root / "etc" / "webvm" / "wsgidav.yaml").exists()
        assert not (root / "etc" / "webvm" / "webdav.htpasswd").exists()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def test_gateway_requires_authkey(tmp_path):
    root = build_sandbox(tmp_path)
    res = run_entrypoint(root, "/usr/local/bin/gateway-entrypoint.sh", {
        "STORAGE_BACKEND": "browser",
    })
    assert res.returncode == 1
    assert "GATEWAY_AUTHKEY" in res.stderr


def test_gateway_samba_requires_samba_lan_ip(tmp_path):
    """samba mode: the 445 relay target must exist — a missing SAMBA_LAN_IP
    fails closed BEFORE any tailscaled work."""
    root = build_sandbox(tmp_path)
    res = run_entrypoint(root, "/usr/local/bin/gateway-entrypoint.sh", {
        "STORAGE_BACKEND": "samba",
        "GATEWAY_AUTHKEY": "hskey-auth-gateway",
    })
    assert res.returncode == 1
    assert "SAMBA_LAN_IP" in res.stderr


def test_supervisor_exits_container_when_nginx_dies(tmp_path):
    """A supervised service that dies MUST stop the container so compose's
    restart policy re-creates it. The supervisor watches STATUS MARKERS
    (webvm_supervise_start), not kill -0: the entrypoint is PID 1 and never
    reaps, so the dead service stays a zombie and kill -0 would keep
    succeeding — the container would read healthy forever with nginx down."""
    root = build_sandbox(tmp_path)
    proc = subprocess.Popen(
        [CHROOT, str(root), "/usr/local/bin/entrypoint.sh"],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/root",
            # HARD-NETWORKLESS launch: nginx is the only supervised service.
            "STORAGE_BACKEND": "browser",
            "WEBVM_TAILNET": "off",
        },
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Wait for the wrapper to record the nginx stub's pid.
        pidfile = root / "tmp" / "webvm-nginx.pid"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if pidfile.exists():
                break
            if proc.poll() is not None:
                out, err = proc.communicate()
                raise AssertionError(
                    f"entrypoint exited early (rc={proc.returncode}): {err or out}"
                )
            time.sleep(0.2)
        assert pidfile.exists(), "nginx wrapper pidfile never appeared"

        # Crash the supervised nginx (the chroot shares the host pid
        # namespace, so the pid in the pidfile is killable from here).
        os.kill(int(pidfile.read_text().strip()), signal.SIGKILL)

        out, err = proc.communicate(timeout=20)
        assert proc.returncode != 0, (
            "entrypoint must exit when a supervised service dies "
            "(marker mechanism): got rc=0"
        )
        assert "FATAL" in err, "the supervisor must report the dead service"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# --------------------------------------------------------------------------
# Happy-path flows (stub services, real entrypoint logic)
# --------------------------------------------------------------------------


def _run_supervised(script_path: Path, root: Path, env: dict, timeout: float = 30):
    """Start an entrypoint that ends in the (infinite) supervisor loop and
    return the Popen; the caller asserts then terminates it."""
    proc = subprocess.Popen(
        [CHROOT, str(root), script_path],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root", **env},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    return proc


def _wait_file(root: Path, rel: str, proc, what: str, timeout: float = 20) -> Path:
    deadline = time.monotonic() + timeout
    path = root / rel
    while time.monotonic() < deadline:
        if path.exists():
            return path
        if proc.poll() is not None:
            out, err = proc.communicate()
            raise AssertionError(
                f"entrypoint exited early (rc={proc.returncode}) waiting for "
                f"{what}: {err or out}"
            )
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for: {what}")


def _terminate(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


SERVER_HAPPY_ENV = {
    "STORAGE_BACKEND": "webdav",
    "HEADSCALE_PREAUTHKEY": "hskey-auth-test",
    "GATEWAY_AUTHKEY": "hskey-auth-gateway",
    "WEBDAV_USER": "webdav",
    "WEBDAV_PASS": "secret",
    "GATEWAY_TAILNET_IP": "100.64.0.1",
    "CONTROL_HOST": "127.0.0.1",
    "CONTROL_PORT": "8443",
    "SITE_PORT": "8081",
    "WEBDAV_PORT": "8082",
    "WEBDAV_BASE_PATH": "/webdav/",
    "ALPINE_PAGE": "alpine.html",
    "STUN_PORT": "3478",
    "CONTROL_WSS_PORT": "443",
    "HEADSCALE_BOOTSTRAP": "0",
}


def _bind_socket(root: Path, rel: str) -> socket.socket:
    """Pre-bind a REAL AF_UNIX socket inside the chroot rootfs (the
    entrypoints wait for `[ -S ... ]` on these paths). The chroot shares the
    host fs, so binding at the chroot path makes the socket visible to the
    sandboxed services. The caller must keep the returned socket alive and
    close it afterwards (a closed socket leaves a stale file that still
    answers `[ -S ]` — fine for these tests, but be tidy)."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(path))
    return sock


def test_server_headscale_happy_path_starts_all_services(tmp_path):
    """The full tailnet launch (webdav + all secrets): headscale starts
    (socket wait + users-create retry + the masked preauth-key check ALL
    PASS against the stub), wsgidav + nginx come up, the baked config is
    rendered, and the supervisor keeps the container alive. This is the
    failure half of the fail-closed matrix — the failure cases never reach
    this flow."""
    root = build_sandbox(tmp_path)
    hs_sock = _bind_socket(root, "var/run/headscale/headscale.sock")
    try:
        (root / "usr" / "bin" / "headscale").write_text(textwrap.dedent("""\
            #!/bin/sh
            case "$1" in
                serve)
                    # The socket itself is pre-bound by the test harness
                    # (the entrypoint's [ -S ] wait needs a real one).
                    sleep 60 ;;
                users)
                    if [ "$2" = "create" ]; then exit 0; fi
                    echo "ID | Name | Namespace"
                    echo "1 | headscale | headscale" ;;
                preauthkeys)
                    # The pinned headscale MASKS keys with *** in non-TTY
                    # output; the entrypoint matches the prefix.
                    echo "hskey-auth-test***"
                    echo "hskey-auth-gateway***" ;;
                *) exit 0 ;;
            esac
            """))
        (root / "usr" / "bin" / "headscale").chmod(0o755)

        proc = _run_supervised("/usr/local/bin/entrypoint.sh", root, SERVER_HAPPY_ENV)
        try:
            _wait_file(root, "etc/nginx/nginx.conf", proc, "rendered nginx.conf")
            _wait_file(root, "etc/nginx/csp.conf", proc, "rendered csp.conf")
            _wait_file(root, "etc/headscale/config.yaml", proc, "rendered headscale config")
            _wait_file(root, "etc/webvm/wsgidav.yaml", proc, "rendered wsgidav config")
            _wait_file(root, "etc/webvm/webdav.htpasswd", proc, "generated htpasswd")
            _wait_file(root, "etc/webvm/webvm-config.js", proc, "rendered baked config")
            # All supervised services (headscale, wsgidav, nginx) started.
            _wait_file(root, "tmp/webvm-headscale.pid", proc, "headscale wrapper pidfile")
            _wait_file(root, "tmp/webvm-wsgidav.pid", proc, "wsgidav wrapper pidfile")
            _wait_file(root, "tmp/webvm-nginx.pid", proc, "nginx wrapper pidfile")
            # No fail-closed trip: the key check PASSED against the masked
            # listing, and the entrypoint is alive in the supervisor loop.
            assert proc.poll() is None, "the entrypoint must stay in the supervisor loop"
        finally:
            _terminate(proc)
            out, err = proc.communicate()
            assert "FATAL" not in err, f"no fatal error expected: {err}"
    finally:
        hs_sock.close()


def test_gateway_relay_matrix(tmp_path):
    """The gateway's socat relay matrix: the webdav relay (WEBDAV_PORT ->
    GATEWAY_CONTROL_IP), the scheme-default WSS relay (0.0.0.0:443 -> the
    server's CONTROL_WSS_PORT), the loopback DERP relay (CONTROL_PORT ->
    GATEWAY_CONTROL_IP), the git relays gated on *_LAN_IP — and the
    tailscale up args (path-less login server, authkey)."""
    root = build_sandbox(tmp_path)
    ts_sock = _bind_socket(root, "var/run/tailscale/tailscaled.sock")
    try:
        def stub(name, body):
            (root / "usr" / "bin" / name).write_text(body)
            (root / "usr" / "bin" / name).chmod(0o755)

        stub("tailscaled", """#!/bin/sh
            printf '%s\\n' "$*" > /tmp/tsd.args
            sleep 60
            """)
        stub("tailscale", """#!/bin/sh
            case "$1" in
                up) printf '%s\\n' "$*" > /tmp/tsup.args; exit 0 ;;
                ip) echo "100.64.0.1"; exit 0 ;;
                *) exit 0 ;;
            esac
            """)
        stub("socat", "#!/bin/sh\nprintf '%s\\n' \"$*\" >> /tmp/socat.args\nsleep 60\n")

        env = {
            "STORAGE_BACKEND": "webdav",
            "GATEWAY_AUTHKEY": "hskey-auth-gateway",
            "GATEWAY_CONTROL_IP": "172.28.0.10",
            "CONTROL_PORT": "8443",
            "CONTROL_WSS_PORT": "443",
            "WEBDAV_PORT": "8082",
            "GIT_SSH_LAN_IP": "",
            "GIT_HTTP_LAN_IP": "",
        }
        proc = _run_supervised("/usr/local/bin/gateway-entrypoint.sh", root, env)
        try:
            socat_args = _wait_file(root, "tmp/socat.args", proc, "first socat relay")
            tsup = _wait_file(root, "tmp/tsup.args", proc, "tailscale up args")
            _wait_file(root, "tmp/webvm-tailscaled.pid", proc, "tailscaled wrapper pidfile")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                lines = socat_args.read_text().splitlines()
                if len(lines) >= 3:
                    break
                time.sleep(0.2)
            lines = socat_args.read_text().splitlines()
            assert "TCP-LISTEN:8082,fork,reuseaddr,bind=127.0.0.1 TCP:172.28.0.10:8082" in lines, lines
            assert "TCP-LISTEN:443,fork,reuseaddr,bind=0.0.0.0 TCP:172.28.0.10:443" in lines, lines
            assert "TCP-LISTEN:8443,fork,reuseaddr,bind=127.0.0.1 TCP:172.28.0.10:8443" in lines, lines
            assert not any("2222" in l or "8083" in l for l in lines), \
                "git relays must NOT start without their *_LAN_IP"
            up = tsup.read_text()
            assert "--login-server=https://172.28.0.10:8443" in up, up
            assert "--authkey=hskey-auth-gateway" in up, up
            assert proc.poll() is None, "the gateway must stay in the supervisor loop"
        finally:
            _terminate(proc)
    finally:
        ts_sock.close()


def test_gateway_samba_and_git_relays(tmp_path):
    """samba mode: the 445 relay targets SAMBA_LAN_IP, and the git SSH/HTTP
    relays appear exactly when their *_LAN_IP vars are set."""
    root = build_sandbox(tmp_path)
    ts_sock = _bind_socket(root, "var/run/tailscale/tailscaled.sock")
    try:
        def stub(name, body):
            (root / "usr" / "bin" / name).write_text(body)
            (root / "usr" / "bin" / name).chmod(0o755)

        stub("tailscaled", "#!/bin/sh\nsleep 60\n")
        stub("tailscale", """#!/bin/sh
            case "$1" in
                up) exit 0 ;;
                ip) echo "100.64.0.1"; exit 0 ;;
                *) exit 0 ;;
            esac
            """)
        stub("socat", "#!/bin/sh\nprintf '%s\\n' \"$*\" >> /tmp/socat.args\nsleep 60\n")

        env = {
            "STORAGE_BACKEND": "samba",
            "GATEWAY_AUTHKEY": "hskey-auth-gateway",
            "GATEWAY_CONTROL_IP": "172.28.0.10",
            "CONTROL_PORT": "8443",
            "CONTROL_WSS_PORT": "443",
            "SAMBA_LAN_IP": "192.168.1.50",
            "GIT_SSH_LAN_IP": "192.168.1.60",
            "GIT_HTTP_LAN_IP": "192.168.1.61",
            "GIT_SSH_PORT": "2222",
            "GIT_HTTP_PORT": "8083",
        }
        proc = _run_supervised("/usr/local/bin/gateway-entrypoint.sh", root, env)
        try:
            socat_args = _wait_file(root, "tmp/socat.args", proc, "first socat relay")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                lines = socat_args.read_text().splitlines()
                if len(lines) >= 5:
                    break
                time.sleep(0.2)
            lines = socat_args.read_text().splitlines()
            assert "TCP-LISTEN:445,fork,reuseaddr,bind=127.0.0.1 TCP:192.168.1.50:445" in lines, lines
            assert "TCP-LISTEN:2222,fork,reuseaddr,bind=127.0.0.1 TCP:192.168.1.60:22" in lines, lines
            assert "TCP-LISTEN:8083,fork,reuseaddr,bind=127.0.0.1 TCP:192.168.1.61:8083" in lines, lines
            # The webdav relay must NOT be present in samba mode.
            assert not any(":8082" in l for l in lines), lines
        finally:
            _terminate(proc)
    finally:
        ts_sock.close()
