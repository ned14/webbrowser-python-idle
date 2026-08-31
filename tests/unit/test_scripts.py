#!/usr/bin/env python3
"""Script hygiene: sh -n on all shell scripts, py_compile on the sync agent,
and a cross-check that the explicit-hash URL (scripts/print-url.sh) and the
baked page config (server/render-webvm-config.py) derive the SAME params."""

import json
import os
import py_compile
import re
import shlex
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SHELL_SCRIPTS = [
    ROOT / "build.sh",
    ROOT / "scripts" / "lib" / "webvm-common.sh",
    ROOT / "scripts" / "gen-certs.sh",
    ROOT / "scripts" / "print-url.sh",
    ROOT / "scripts" / "acceptance.sh",
    ROOT / "scripts" / "fetch-cheerpx-runtime.sh",
    ROOT / "scripts" / "rebuild-tailscale-wasm.sh",
    ROOT / "scripts" / "precompress-static.sh",
    ROOT / "scripts" / "reset-cycle.sh",
    ROOT / "server" / "entrypoint.sh",
    ROOT / "gateway" / "entrypoint.sh",
    ROOT / "diskimage" / "sync" / "sync-home.sh",
    ROOT / "diskimage" / "rootfs" / "etc" / "local.d" / "desktop.start",
    ROOT / "diskimage" / "scripts" / "99-screen-resize.sh",
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "open-file-explorer.sh",
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "keep-file-explorer.sh",
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "lib" / "webvm-pidfile.sh",
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "idle3.14-launcher",
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "idle-loopback-cache",
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "paste-typer.sh",
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "sbin" / "rc-preload",
    ROOT / "diskimage" / "scripts" / "wm-clients.sh",
    ROOT / "tests" / "rootfs" / "smoke.sh",
    ROOT / "tests" / "server" / "integration.sh",
    ROOT / "tests" / "server" / "join-test-client.sh",
]

PYTHON_SOURCES = [
    ROOT / "diskimage" / "sync" / "sync.py",
    ROOT / "diskimage" / "scripts" / "file-explorer.py",
    ROOT / "diskimage" / "scripts" / "file-explorer-tests.py",
    ROOT / "diskimage" / "scripts" / "file_types.py",
    ROOT / "diskimage" / "scripts" / "file-viewer.py",
    ROOT / "diskimage" / "scripts" / "file-viewer-tests.py",
    ROOT / "server" / "render-webvm-config.py",
    ROOT / "tests" / "fixtures" / "fake_webdav.py",
    # The guest-wide time.sleep patch (loaded by CPython's site module).
    ROOT / "diskimage" / "rootfs" / "usr" / "lib" / "python3.14" /
    "site-packages" / "sitecustomize.py",
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


def test_openbox_rc_close_binding():
    """The openbox rc.xml <mouse> section must re-bind the titlebar ✕ Close
    button. openbox's config parser (config.c parse_mouse -> mouse_unbind_all)
    WIPES the compiled-in default bindings whenever a <mouse> section exists,
    including "Left click on Close -> Close" — so a <mouse> section without a
    Close context leaves the ✕ rendered but dead (fixed 2026-08-18)."""
    import xml.etree.ElementTree as ET

    rc = ROOT / "diskimage" / "config" / "openbox" / "rc.xml"
    root = ET.parse(rc).getroot()

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
    tag = lambda name: f"{ns}{name}"

    mouse = root.find(tag("mouse"))
    assert mouse is not None, "rc.xml must define a <mouse> section"

    close_context = None
    for ctx in mouse.findall(tag("context")):
        if ctx.get("name") == "Close":
            close_context = ctx
            break
    assert close_context is not None, (
        "rc.xml <mouse> section must define a <context name=\"Close\"> — "
        "without it openbox wipes its default Close-click binding and the "
        "titlebar ✕ does nothing"
    )

    click_binds_close = False
    for mb in close_context.findall(tag("mousebind")):
        if mb.get("button") == "Left" and mb.get("action") == "Click":
            for act in mb.findall(tag("action")):
                if act.get("name") == "Close":
                    click_binds_close = True
    assert click_binds_close, (
        "Close context must bind Left-click to the Close action"
    )


def _baked_config(env, backend="webdav"):
    """The /webvm-config.js rendering (server/render-webvm-config.py named
    args — the entrypoint calls it the same way). The deployment values whose
    single home is the shared lib are passed EXPLICITLY (the renderer has no
    defaults for them — a Python-side default would silently become a second
    home)."""
    run = subprocess.run(
        ["python3", str(ROOT / "server" / "render-webvm-config.py"),
         "--control-host", env["CONTROL_HOST"], "--control-port", env["CONTROL_PORT"],
         "--auth-key", env["HEADSCALE_PREAUTHKEY"], "--backend", backend,
         "--gateway-ip", env["GATEWAY_TAILNET_IP"], "--webdav-port", env["WEBDAV_PORT"],
         "--webdav-user", env["WEBDAV_USER"], "--webdav-pass", env["WEBDAV_PASS"],
         "--webdav-base-path", _lib_var("WEBDAV_BASE_PATH"),
         "--alpine-page", _lib_var("ALPINE_PAGE"),
         "--site-port", _lib_var("SITE_PORT")],
        capture_output=True, text=True, check=True,
    )
    body = run.stdout
    assert body.startswith("window.__webvmConfig = ") and body.rstrip().endswith(";")
    return json.loads(body[len("window.__webvmConfig = "):].rstrip()[:-1])


def _hash_params(env):
    """The explicit-hash URL params (scripts/print-url.sh)."""
    run = subprocess.run(
        [str(ROOT / "scripts" / "print-url.sh")],
        capture_output=True, text=True, check=True, env={**os.environ, **env},
    )
    url, _, fragment = run.stdout.strip().partition("#")
    assert fragment, f"expected a hash URL, got {run.stdout.strip()!r}"
    return {k: v[0] for k, v in urllib.parse.parse_qs(fragment).items()}


WEBDAV_ENV = {
    "STORAGE_BACKEND": "webdav",
    "CONTROL_HOST": "127.0.0.1",
    "CONTROL_PORT": "8443",
    "SITE_PORT": "8081",
    "LAN_IP": "127.0.0.1",
    "WEBDAV_PORT": "8082",
    "GATEWAY_TAILNET_IP": "100.64.0.1",
    "HEADSCALE_PREAUTHKEY": "hskey-auth-test",
    "WEBDAV_USER": "webdav",
    "WEBDAV_PASS": "pass word",
}


@pytest.mark.parametrize(
    "backend,env",
    [
        ("webdav", WEBDAV_ENV),
        ("samba", {**WEBDAV_ENV, "STORAGE_BACKEND": "samba"}),
        ("browser", {**WEBDAV_ENV, "STORAGE_BACKEND": "browser", "HEADSCALE_ENABLED": "1"}),
        ("browser", {**WEBDAV_ENV, "STORAGE_BACKEND": "browser", "HEADSCALE_ENABLED": "1", "HEADSCALE_PREAUTHKEY": 'k"e$y'}),
    ],
)
def test_baked_config_matches_print_url_hash(backend, env):
    """The baked /webvm-config.js and the `make url` hash must derive the same
    params — they are two renderings of one URL (drift = the page's networking
    silently differs depending on how it was opened)."""
    baked = _baked_config(env, backend=backend)
    url_params = _hash_params(env)

    for key in ("authKey", "controlUrl", "syncUrl", "syncUser", "syncPass"):
        expected = baked.get(key)
        got = url_params.get(key)
        if expected is None:
            assert got is None, f"hash URL unexpectedly carries {key}={got!r}"
        else:
            assert got == expected, f"{key}: baked {expected!r} != hash URL {got!r}"


def test_control_host_defaults_consistent():
    """CONTROL_HOST is BROWSER-facing; every default must agree. The
    127.0.0.1 default is the zero-config single machine; LAN deployments set
    it to a hardcoded LAN address. HOSTNAMES ARE BANNED: host.docker.internal
    /etc/hosts tricks must never reappear — the browser must reach the
    control plane over 127.0.0.1 / a LAN IP alone."""
    lib = (ROOT / "scripts" / "lib" / "webvm-common.sh").read_text()
    compose = (ROOT / "compose.yaml").read_text()
    env_example = (ROOT / ".env.example").read_text()

    # The defaults' SINGLE HOME is the shared lib; the consumers source it
    # (a re-added local default would drift — the whole point of the lib).
    assert "${CONTROL_HOST:-127.0.0.1}" in lib, \
        "webvm-common.sh CONTROL_HOST default must be 127.0.0.1"
    assert "${GATEWAY_CONTROL_IP:-172.28.0.10}" in lib, \
        "webvm-common.sh GATEWAY_CONTROL_IP default must be 172.28.0.10 (the cert SAN, compose and the gateway must agree)"
    assert "CONTROL_HOST=127.0.0.1" in env_example, \
        ".env.example CONTROL_HOST must document 127.0.0.1"
    assert "GATEWAY_CONTROL_IP=172.28.0.10" in env_example, \
        ".env.example must document GATEWAY_CONTROL_IP"

    for path in (
        "server/entrypoint.sh",
        "gateway/entrypoint.sh",
        "scripts/gen-certs.sh",
        "scripts/print-url.sh",
        "scripts/acceptance.sh",
        "build.sh",
        "tests/server/integration.sh",
        "tests/server/join-test-client.sh",
    ):
        text = (ROOT / path).read_text()
        assert "webvm-common.sh" in text, \
            f"{path} must source the shared lib (scripts/lib/webvm-common.sh) — defaults live there"

    # The banned hostname must appear NOWHERE in any runtime config, script,
    # test or CI file (comment lines are exempt — the ban is documented in
    # comments, but must never be USED). Plans may discuss it historically;
    # executable files never may.
    banned = [
        "scripts/lib/webvm-common.sh",
        "server/entrypoint.sh",
        "server/nginx.conf.template",
        "server/headscale/config.yaml.template",
        "server/render-webvm-config.py",
        "scripts/gen-certs.sh",
        "scripts/print-url.sh",
        "scripts/acceptance.sh",
        "gateway/entrypoint.sh",
        "compose.yaml",
        ".env.example",
        ".github/workflows/ci.yml",
        ".github/workflows/pages.yml",
        "Makefile",
        "webvm/src/app.html",
        "webvm/src/lib/network.js",
        "webvm/src/lib/sessionGuard.js",
        "webvm/src/lib/clipboard.js",
        "webvm/src/lib/cacheId.js",
        "webvm/src/lib/siteBase.js",
        "webvm/src/lib/WebVM.svelte",
        "webvm/src/lib/cheerpx.js",
        "webvm/src/routes/alpine/+page.svelte",
        "tests/server/integration.sh",
        "tests/server/join-test-client.sh",
        "tests/rootfs/smoke.sh",
        "tests/e2e/playwright.config.js",
        "tests/e2e/lib/desktop.js",
        "tests/e2e/lib/webdav-auth.js",
        "tests/e2e/tests/boot.spec.js",
        "tests/e2e/tests/desktop.spec.js",
        "tests/e2e/tests/error-overlay.spec.js",
        "tests/e2e/tests/idle-pointer.spec.js",
        "tests/e2e/tests/network.spec.js",
        "tests/e2e/tests/paste.spec.js",
        "tests/e2e/tests/persistence.spec.js",
        "tests/e2e/tests/resize.spec.js",
        "tests/e2e/tests/sync.spec.js",
        "tests/e2e/repro-tailnet.mjs",
        "tests/e2e/data-path-probe.mjs",
        "tests/e2e/big-put-probe.mjs",
        "tests/e2e/stream-put-probe.mjs",
        "tests/e2e/guest-socket-trace.mjs",
        "diskimage/rootfs/usr/local/bin/paste-typer.sh",
    ]
    offenders = []
    for path in banned:
        text = (ROOT / path).read_text()
        # Drop comment lines (# and //) — documentation of the ban is fine;
        # a USE of the hostname is not.
        code = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith(("#", "//", "*"))
        )
        if "host.docker.internal" in code:
            offenders.append(path)
    assert not offenders, \
        "host.docker.internal is BANNED (no /etc/hosts tricks): " + \
        ", ".join(offenders)


def test_tailscale_version_lockstep():
    """The tailscale pin must agree across the gateway image, the join-test
    client and the wasm rebuild — scripts/versions.env is the single source;
    bump it there first, then update the consumers."""
    versions = {}
    for line in (ROOT / "scripts" / "versions.env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        versions[key.strip()] = value.strip()
    assert versions.get("TAILSCALE_VERSION"), "versions.env missing TAILSCALE_VERSION"
    assert versions.get("GO_VERSION"), "versions.env missing GO_VERSION"

    gateway_dockerfile = (ROOT / "gateway" / "Dockerfile").read_text()
    assert f"tailscale/tailscale:v{versions['TAILSCALE_VERSION']}" in gateway_dockerfile, \
        "gateway/Dockerfile pin must match versions.env"
    join_test = (ROOT / "tests" / "server" / "join-test-client.sh").read_text()
    assert f"tailscale/tailscale:v{versions['TAILSCALE_VERSION']}" in join_test, \
        "join-test-client.sh pin must match versions.env"
    rebuild = (ROOT / "scripts" / "rebuild-tailscale-wasm.sh").read_text()
    assert "versions.env" in rebuild, "rebuild-tailscale-wasm.sh must source versions.env"
    assert "TAILSCALE_VERSION" in rebuild, "rebuild-tailscale-wasm.sh must use TAILSCALE_VERSION"
    assert "GO_VERSION" in rebuild, "rebuild-tailscale-wasm.sh must use GO_VERSION"
    assert f'GO_IMAGE="golang:${{GO_VERSION}}"' in rebuild, \
        "rebuild-tailscale-wasm.sh GO image must come from versions.env"


def test_app_html_seed_keys_match_renderer():
    """The credential keys the app.html inline seed script moves from the URL
    hash / baked config into sessionStorage must be EXACTLY the keys the
    server renderer (server/render-webvm-config.py) and the hash-URL printer
    (scripts/print-url.sh) emit — a key added on one side and forgotten on
    the other silently disables networking for sessions opened that way."""
    app_html = (ROOT / "webvm" / "src" / "app.html").read_text()
    m = re.search(r'\["authKey",\s*"controlUrl",\s*"syncUrl",\s*"syncUser",\s*"syncPass"\]', app_html)
    assert m, "app.html seed script must carry the five credential keys as a literal list"
    seed_keys = ["authKey", "controlUrl", "syncUrl", "syncUser", "syncPass"]

    baked = _baked_config(WEBDAV_ENV, backend="webdav")
    url_params = _hash_params(WEBDAV_ENV)
    assert sorted(seed_keys) == sorted(baked.keys()) == sorted(url_params.keys()), (
        "app.html seed keys, the baked config keys and the hash-URL keys must be identical"
    )


# --------------------------------------------------------------------------
# Shared-lib helpers (webvm-common.sh) — executed directly with sh.
# --------------------------------------------------------------------------

LIB = ROOT / "scripts" / "lib" / "webvm-common.sh"


def _sh(script, env=None, cwd=None):
    return subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True,
        env={**os.environ, **(env or {})}, cwd=str(cwd) if cwd else None,
    )


def test_webvm_key_is_listed():
    """The masked-preauthkey matching (headscale 0.29.x prints keys as a
    prefix + *** when not a TTY) must be exact — a wrong match would either
    fail a valid deployment or accept a wrong key."""
    lib_text = LIB.read_text()
    assert "webvm_key_is_listed" in lib_text, "helper must live in the shared lib"
    script = f'''
        . {shlex.quote(str(LIB))}
        check() {{ if webvm_key_is_listed "$1" "$2"; then echo YES; else echo NO; fi; }}
        check "hskey-auth-abc123" "hskey-auth-abc***"
        check "hskey-auth-abcdefg" "hskey-auth-abc***"
        check "hskey-auth-xyz" "hskey-auth-abc***"
        check "hskey-auth-xyz" ""
        check "hskey-auth-abc" "hskey-auth-xyz***"
        check "hskey-auth-A1b2-C3d4" "hskey-auth-A1b2-C3d4***"
        check "hskey-auth-second" "$(printf 'hskey-auth-first***\\nhskey-auth-second***')"
        check "hskey-auth-abc" "not-a-key ***"
    '''
    res = _sh(script)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["YES", "YES", "NO", "NO", "NO", "YES", "YES", "NO"]


def test_webvm_require_secret():
    """Fail-closed secret check: empty/unset exits 1 with the hint; a set
    value passes. (webvm_require_secret calls `exit 1`, so each case runs in
    a subshell — the rc is captured, not the shell.)"""
    script = f'''
        . {shlex.quote(str(LIB))}
        ( webvm_require_secret FOO "the hint" >/dev/null 2>&1 ); echo rc=$?
        ( FOO=bar webvm_require_secret FOO "the hint" >/dev/null 2>&1 ); echo rc=$?
    '''
    res = _sh(script)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["rc=1", "rc=0"]


def test_webvm_wait_until(tmp_path):
    """The wait loop succeeds when the condition turns true and fails after
    the given number of tries."""
    counter = tmp_path / "counter"
    script = f'''
        . {shlex.quote(str(LIB))}
        set +e
        webvm_wait_until 3 0.05 true; echo rc=$?
        webvm_wait_until 2 0.05 false; echo rc=$?
        webvm_wait_until 3 0.05 sh -c 'n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}; [ "$n" -ge 2 ]'; echo rc=$?
    '''
    res = _sh(script)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["rc=0", "rc=1", "rc=0"]


def test_webvm_load_dotenv_precedence_and_quote_stripping(tmp_path):
    """The .env loader must strip surrounding quotes (compose does too) and
    never override an explicit environment value."""
    (tmp_path / ".env").write_text(
        "# comment\n"
        "QUOTED=\"a b\"\n"
        "SINGLE='c d'\n"
        "PLAIN=bare\n"
        "EXISTING=from-dotenv\n"
        "not-valid key=x\n"
        "EMPTY=\n"
    )
    script = f'''
        . {shlex.quote(str(LIB))}
        webvm_load_dotenv
        printf '%s|%s|%s|%s|%s|%s' "$QUOTED" "$SINGLE" "$PLAIN" "$EXISTING" "$EMPTY" "${{not_valid_key:-unset}}"
    '''
    res = _sh(script, env={"EXISTING": "from-env"}, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert res.stdout == "a b|c d|bare|from-env||unset"


# --------------------------------------------------------------------------
# wm-clients.sh --count-line (the hex-id counting contract the keep-alive
# daemon's spy feeds; direct tests so the contract is pinned on its own).
# --------------------------------------------------------------------------

WM_CLIENTS = ROOT / "diskimage" / "scripts" / "wm-clients.sh"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("_NET_CLIENT_LIST(WINDOW): window id # 0x1a00003", "1"),
        ("_NET_CLIENT_LIST(WINDOW): window id # 0x1a00003, 0x2a00004, 0x3a00005", "3"),
        ("_NET_CLIENT_LIST(WINDOW): window id #", "0"),
        ("_NET_CLIENT_LIST:  no such atom on any window.", None),
        ("_NET_CLIENT_LIST:  No such atom on any window.", None),
        ("_NET_CLIENT_LIST: not found.", None),
        ("", None),
    ],
    ids=["one-window", "three-windows", "zero-windows", "atom-lower", "atom-title", "not-found", "empty"],
)
def test_wm_clients_count_line(line, expected):
    res = subprocess.run(
        ["sh", str(WM_CLIENTS), "--count-line"],
        input=line + "\n", capture_output=True, text=True,
    )
    if expected is None:
        assert res.returncode != 0, f"unreadable line must exit non-zero: {line!r}"
        assert res.stdout == "", "failure lines must print nothing (never '0')"
    else:
        assert res.returncode == 0, res.stderr
        assert res.stdout.strip() == expected


# --------------------------------------------------------------------------
# Per-mode secret matrix (webvm_require_mode_secrets — shared by the server
# entrypoint and print-url.sh; the two must enforce the SAME requirements).
# --------------------------------------------------------------------------

def test_webvm_backend_needs_headscale():
    """The backend->control-plane matrix (webvm_backend_needs_headscale) is
    the single home of `need_headscale`: samba/webdav always need the control
    plane, browser/none only with HEADSCALE_ENABLED=1. The server entrypoint
    and webvm_require_mode_secrets both consume it — a mode added on one side
    and forgotten on the other would silently start a tailnet-capable backend
    without its control plane."""
    script = f'''
        . {shlex.quote(str(LIB))}
        n() {{ if webvm_backend_needs_headscale "$1"; then echo yes; else echo no; fi; }}
        ( n browser )
        ( n none )
        ( export HEADSCALE_ENABLED=1; n browser )
        ( export HEADSCALE_ENABLED=1; n none )
        ( n samba )
        ( n webdav )
    '''
    res = _sh(script)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == ["no", "no", "yes", "yes", "yes", "yes"]


def test_lib_envsubst_vars_are_exported():
    """The server entrypoint renders nginx.conf through `envsubst`, which
    reads the ENVIRONMENT of its child process: a lib-defaulted var that is
    not exported renders EMPTY in the template. ALPINE_PAGE and
    WEBVM_IMAGE_DIR are the two envsubst'd values the lib defaults (compose
    provides CONTROL_HOST/SITE_PORT/…), so they must stay exported — the
    regression rendered `location //` and `return 302 ;` (ext2 404 + broken
    site redirect) whenever .env lacked them (observed 2026-08-30)."""
    lib_text = LIB.read_text()
    assert "export ALPINE_PAGE" in lib_text, (
        "ALPINE_PAGE must be exported for the entrypoint's envsubst "
        "(an unexported var renders empty in the nginx template)"
    )
    assert "export WEBVM_IMAGE_DIR" in lib_text, (
        "WEBVM_IMAGE_DIR must be exported for the entrypoint's envsubst "
        "(an unexported var renders empty in the nginx template)"
    )
    script = f'''
        . {shlex.quote(str(LIB))}
        env | grep -E '^(ALPINE_PAGE|WEBVM_IMAGE_DIR)=' | sort
    '''
    res = _sh(script)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "ALPINE_PAGE=alpine.html",
        "WEBVM_IMAGE_DIR=custom-disk-images",
    ]


def test_webvm_require_mode_secrets():
    """The per-mode fail-closed matrix must be exact: each backend's required
    secrets, the --bootstrap skip (the gateway has not joined yet), and the
    unknown-backend rejection."""
    script = f'''
        . {shlex.quote(str(LIB))}
        r() {{ ( webvm_require_mode_secrets "$1" ${{2:-}} >/dev/null 2>&1 ); echo "rc=$?"; }}
        # browser/none without HEADSCALE_ENABLED need nothing
        ( r browser )
        ( r none )
        # browser with HEADSCALE_ENABLED needs the preauth key
        ( export HEADSCALE_ENABLED=1; r browser )
        ( export HEADSCALE_ENABLED=1 HEADSCALE_PREAUTHKEY=k; r browser )
        # samba needs the preauth key
        ( r samba )
        ( export HEADSCALE_PREAUTHKEY=k; r samba )
        # webdav needs preauth key + WebDAV creds (+ tailnet IP unless bootstrap)
        ( export HEADSCALE_PREAUTHKEY=k; r webdav )
        ( export HEADSCALE_PREAUTHKEY=k WEBDAV_USER=u; r webdav )
        ( export HEADSCALE_PREAUTHKEY=k WEBDAV_USER=u WEBDAV_PASS=p; r webdav )
        ( export HEADSCALE_PREAUTHKEY=k WEBDAV_USER=u WEBDAV_PASS=p GATEWAY_TAILNET_IP=100.64.0.1; r webdav )
        ( export HEADSCALE_PREAUTHKEY=k WEBDAV_USER=u WEBDAV_PASS=p; r webdav --bootstrap )
        # --gateway-key (server entrypoint only) adds the gateway auth key
        ( export HEADSCALE_PREAUTHKEY=k; r samba --gateway-key )
        ( export HEADSCALE_PREAUTHKEY=k GATEWAY_AUTHKEY=g; r samba --gateway-key )
        # unknown backend
        ( r bogus )
    '''
    res = _sh(script)
    assert res.returncode == 0, res.stderr
    assert res.stdout.splitlines() == [
        "rc=0", "rc=0",              # browser/none no HS
        "rc=1", "rc=0",              # browser + HS_ENABLED
        "rc=1", "rc=0",              # samba
        "rc=1", "rc=1", "rc=1", "rc=0", "rc=0",  # webdav (+bootstrap)
        "rc=1", "rc=0",              # samba --gateway-key
        "rc=1",                      # bogus
    ]


# --------------------------------------------------------------------------
# Compose <-> shared-lib default drift (compose cannot source the lib, so
# the test is the enforcement — a remapped port/IP must never apply to only
# one of the two).
# --------------------------------------------------------------------------

def test_compose_defaults_match_lib():
    """Every ${VAR:-default} in compose.yaml must equal the shared lib's
    default for that var (the lib is the single home of the deployment
    defaults; the compose file repeats them only because it cannot source
    the lib)."""
    compose = "\n".join(
        line for line in (ROOT / "compose.yaml").read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    lib = LIB.read_text()
    found = set(re.findall(r"\$\{([A-Z_]+):-([^}]*)\}", compose))
    assert found, "no ${VAR:-default} found in compose.yaml"
    for var, default in sorted(found):
        assert f"${{{var}:-{default}}}" in lib, (
            f"compose.yaml default ${{{var}:-{default}}} is not the shared "
            f"lib's default for {var} — the lib is the single home"
        )

    # The server container's static compose-network IP must come from the
    # SAME variable the cert SAN and the gateway's --login-server use — a
    # bare literal here would silently orphan a remapped gateway (the
    # GATEWAY_CONTROL_IP override would apply everywhere except the
    # interface it must actually reach).
    assert "ipv4_address: ${GATEWAY_CONTROL_IP:-172.28.0.10}" in compose, (
        "compose.yaml must interpolate the server static IP from "
        "GATEWAY_CONTROL_IP (${GATEWAY_CONTROL_IP:-172.28.0.10}) — never a "
        "bare literal"
    )


def test_e2e_and_ci_literals_match_lib():
    """The E2E harness and CI bake URL literals (they cannot source the
    shell lib) — pin them against the lib so a port/page/WebDAV-base rename
    cannot silently desync the tests from the stack they exercise."""
    site_port = _lib_var("SITE_PORT")
    alpine_page = _lib_var("ALPINE_PAGE")
    webdav_port = _lib_var("WEBDAV_PORT")
    webdav_base = _lib_var("WEBDAV_BASE_PATH")

    pw_config = (ROOT / "tests" / "e2e" / "playwright.config.js").read_text()
    assert f"https://127.0.0.1:{site_port}" in pw_config

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert f"https://127.0.0.1:{site_port}/{alpine_page}" in ci, (
        "ci.yml E2E_SITE_URL must match the lib SITE_PORT/ALPINE_PAGE"
    )
    assert f"http://127.0.0.1:{webdav_port}{webdav_base}" in ci, (
        "ci.yml E2E_WEBDAV_BASE must match the lib WEBDAV_PORT/WEBDAV_BASE_PATH"
    )

    for spec in (
        "boot.spec.js", "desktop.spec.js", "error-overlay.spec.js",
        "idle-pointer.spec.js", "paste.spec.js", "persistence.spec.js",
        "resize.spec.js",
    ):
        text = (ROOT / "tests" / "e2e" / "tests" / spec).read_text()
        # The specs build SITE_URL from E2E_SITE_PORT with a plain fallback
        # (`…:${process.env.E2E_SITE_PORT || 8081}/alpine.html`) — accept
        # both the plain literal and the template fallback form.
        assert f":{site_port}/{alpine_page}" in text or \
            f"|| {site_port}}}/{alpine_page}" in text, (
                f"{spec} must reference the lib SITE_PORT/ALPINE_PAGE"
            )


def test_paste_contract_guest_page_lockstep():
    """The paste typing contract has two homes — the page (clipboard.js
    CX_TYPE_DELAY_MS / PASTE_MAX_CHARS) and the guest typer (paste-typer.sh
    DELAY_US / MAX_PAYLOAD). They must stay proportional (the page's ack
    timeout and the panel's typing estimate assume the guest's delay) and
    ordered (a page paste over the guest's payload cap is a guaranteed
    CXFAIL toolarge)."""
    clipboard = (ROOT / "webvm" / "src" / "lib" / "clipboard.js").read_text()
    typer = (ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "paste-typer.sh").read_text()

    m = re.search(r"export const CX_TYPE_DELAY_MS = (\d+);", clipboard)
    assert m, "clipboard.js must define CX_TYPE_DELAY_MS"
    delay_ms = int(m.group(1))
    m = re.search(r"DELAY_US=\$\{PASTE_DELAY_US:-(\d+)\}", typer)
    assert m, "paste-typer.sh must define DELAY_US"
    delay_us = int(m.group(1))
    assert delay_us == delay_ms * 1000, (
        f"paste-typer.sh DELAY_US ({delay_us}) must equal clipboard.js "
        f"CX_TYPE_DELAY_MS * 1000 ({delay_ms * 1000}) — the page's typing "
        f"estimate and ack timeout assume the guest's delay"
    )

    m = re.search(r"export const PASTE_MAX_CHARS = (\d+);", clipboard)
    assert m, "clipboard.js must define PASTE_MAX_CHARS"
    max_chars = int(m.group(1))
    m = re.search(r"MAX_PAYLOAD=\$\{PASTE_MAX_PAYLOAD:-(\d+)\}", typer)
    assert m, "paste-typer.sh must define MAX_PAYLOAD"
    max_payload = int(m.group(1))
    assert max_chars <= max_payload, (
        f"page PASTE_MAX_CHARS ({max_chars}) must be <= the guest's "
        f"MAX_PAYLOAD ({max_payload}) — a page paste over the guest cap is "
        "a guaranteed CXFAIL toolarge"
    )


def test_guest_baked_sync_url_matches_renderer(render_webvm_config):
    """The guest's BAKED syncrc URL (build.sh SYNC_URL_EFF default) and the
    served syncUrl (render-webvm-config.py build_config) are two renderings
    of one URL: http://<gateway-ip>:<webdav-port><webdav-base-path>. A base
    path or port change reaching only one side silently kills guest sync."""
    lib_text = LIB.read_text()
    assert 'GATEWAY_TAILNET_IP_DEFAULT="${GATEWAY_TAILNET_IP_DEFAULT:-100.64.0.1}"' in lib_text, (
        "the placeholder gateway tailnet IP must live in the shared lib "
        "(build.sh + the Dockerfile ARG defaults are pinned against it)"
    )
    gateway_ip = _lib_var("GATEWAY_TAILNET_IP_DEFAULT")
    webdav_port = _lib_var("WEBDAV_PORT")
    webdav_base = _lib_var("WEBDAV_BASE_PATH")

    build = (ROOT / "build.sh").read_text()
    assert f"http://${{GATEWAY_TAILNET_IP_DEFAULT}}:${{WEBDAV_PORT}}${{WEBDAV_BASE_PATH}}" in build, (
        "build.sh SYNC_URL_EFF must derive from the lib's "
        "GATEWAY_TAILNET_IP_DEFAULT/WEBDAV_PORT/WEBDAV_BASE_PATH"
    )

    sync_url = render_webvm_config.build_config(
        "127.0.0.1", "8443", "k", "webdav", gateway_ip,
        webdav_port, "u", "p", webdav_base_path=webdav_base,
    )["syncUrl"]
    assert sync_url == f"http://{gateway_ip}:{webdav_port}{webdav_base}", sync_url

    # The Dockerfile ARG defaults must agree with the lib-derived build.sh
    # defaults (a direct `docker build` with no args bakes the same guest).
    dockerfile = (ROOT / "diskimage" / "Dockerfile").read_text()
    assert f"ARG SYNC_URL=http://{gateway_ip}:{webdav_port}{webdav_base}" in dockerfile
    assert f"ARG SAMBA_HOST={gateway_ip}" in dockerfile


def test_vendored_cxcore_trap_patch_intact():
    """The vendored CheerpX runtime carries the swallowed-trap patch applied
    by scripts/fetch-cheerpx-runtime.sh (exactly three
    console.error('Unexpected exit' trampoline sites, no `debugger;`, no
    exception-object `e()` call). The committed files must keep the patch
    even when the fetch script is not re-run — error-overlay.spec.js depends
    on the trap reporting. Mirrors the fetch script's presence guards."""
    for name in ("cxcore.js", "cxcore-no-return-call.js"):
        path = ROOT / "webvm" / "cheerpx" / name
        if not path.exists():
            continue
        text = path.read_text()
        sites = len(re.findall(r"console\.error\('Unexpected exit'", text))
        assert sites == 3, (
            f"webvm/cheerpx/{name} must carry exactly 3 trap-reporting "
            f"sites (the vendored-runtime patch); found {sites}"
        )
        assert "debugger" not in text, (
            f"webvm/cheerpx/{name} must contain no debugger; statements "
            "(they froze the tab with DevTools open)"
        )
        assert "e.stack);e()" not in text, (
            f"webvm/cheerpx/{name} must not contain the exception-object "
            "call that crashed the core on a swallowed trap"
        )


def test_security_header_literals_agree():
    """The cross-origin-isolation header values exist in three files that
    cannot share code (the nginx template, the shared subresource-location
    include, and the GitHub Pages service worker). They must agree — a
    single wrong header anywhere silently breaks SharedArrayBuffer on that
    path. (sw.js injects only COOP/COEP — CORP is an nginx-side belt-and-
    braces addition; the other two files carry all three.)"""
    values = {
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Embedder-Policy": "require-corp",
        "Cross-Origin-Resource-Policy": "cross-origin",
    }
    nginx = (ROOT / "server" / "nginx.conf.template").read_text()
    sub = (ROOT / "server" / "site-subresource-headers.conf").read_text()
    sw = (ROOT / "webvm" / "static" / "sw.js").read_text()
    sw_headers = {"Cross-Origin-Opener-Policy", "Cross-Origin-Embedder-Policy"}
    for header, value in values.items():
        for path, text in (("nginx.conf.template", nginx),
                           ("site-subresource-headers.conf", sub)):
            assert f'{header} "{value}"' in text, (
                f"{path} must set {header}: {value}"
            )
        if header in sw_headers:
            assert f"set('{header}', '{value}')" in sw, (
                f"sw.js must inject {header}: {value}"
            )


# --------------------------------------------------------------------------
# CheerpX version lockstep (versions.env is the single source)
# --------------------------------------------------------------------------

def test_cheerpx_version_lockstep():
    """The CheerpX runtime pin must agree across scripts/versions.env (the
    single source), the fetch script, webvm/package.json and cheerpx.js."""
    versions = {}
    for line in (ROOT / "scripts" / "versions.env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        versions[key.strip()] = value.strip()
    assert versions.get("CHEERPX_VERSION"), "versions.env missing CHEERPX_VERSION"
    version = versions["CHEERPX_VERSION"]

    fetch = (ROOT / "scripts" / "fetch-cheerpx-runtime.sh").read_text()
    assert "versions.env" in fetch, "fetch-cheerpx-runtime.sh must source versions.env"
    assert "CHEERPX_VERSION" in fetch, "fetch-cheerpx-runtime.sh must use CHEERPX_VERSION"

    pkg = (ROOT / "webvm" / "package.json").read_text()
    assert f'"@leaningtech/cheerpx": "{version}"' in pkg, (
        "webvm/package.json CheerpX pin must match versions.env"
    )
    cheerpx = (ROOT / "webvm" / "src" / "lib" / "cheerpx.js").read_text()
    assert f'const VERSION = "{version}";' in cheerpx, (
        "webvm/src/lib/cheerpx.js VERSION must match versions.env"
    )


# --------------------------------------------------------------------------
# Frontend <-> lib literal pins (the frontend cannot source the shell lib)
# --------------------------------------------------------------------------

def _lib_var(name):
    res = _sh(f'. {shlex.quote(str(LIB))}; printf "%s" "${{{name}}}"')
    assert res.returncode == 0, res.stderr
    return res.stdout


def test_frontend_literals_match_lib():
    """The frontend cannot source the shared lib — its config literals are
    pinned against the lib values so a rename cannot silently desync the
    page from nginx/build.sh."""
    image_dir = _lib_var("WEBVM_IMAGE_DIR")
    image_name = _lib_var("WEBVM_IMAGE_NAME")
    prefix = _lib_var("CACHE_ID_PREFIX")

    config = (ROOT / "webvm" / "config_public_alpine.js").read_text()
    assert f"/{image_dir}/{image_name}?v=" in config, (
        "config_public_alpine.js diskImageUrl must match the lib image dir/name"
    )

    cache = (ROOT / "webvm" / "src" / "lib" / "cacheId.js").read_text()
    assert f'CACHE_ID_PREFIX = "{prefix}"' in cache, (
        "cacheId.js prefix must match the lib CACHE_ID_PREFIX"
    )

    nginx = (ROOT / "server" / "nginx.conf.template").read_text()
    # The template renders the location from the envsubst var (the entrypoint
    # passes the lib value; the T1 drift test pins the envsubst list).
    assert "location /${WEBVM_IMAGE_DIR}/" in nginx, (
        "nginx template must render the image location from the envsubst "
        "WEBVM_IMAGE_DIR (never a hardcoded path)"
    )
    assert "alias /srv/webvm/${WEBVM_IMAGE_DIR}/;" in nginx


# --------------------------------------------------------------------------
# render-webvm-config.py --url without an auth key (browser/none builds)
# --------------------------------------------------------------------------

def test_render_url_without_authkey_has_no_hash():
    """A deployment without a preauth key must print a PLAIN URL (no hash —
    a hash-less URL is the no-secrets browser/none path; a stale hash would
    point at a control plane that does not exist)."""
    base = [
        "python3", str(ROOT / "server" / "render-webvm-config.py"), "--url",
        "--site-port", "9999", "--control-host", "127.0.0.1",
        "--control-port", "8443", "--auth-key", "", "--backend", "browser",
        "--gateway-ip", "", "--webdav-port", "8082",
        "--webdav-user", "", "--webdav-pass", "",
        "--alpine-page", _lib_var("ALPINE_PAGE"),
    ]
    run = subprocess.run(base + ["--lan-ip", "10.0.0.5"],
                         capture_output=True, text=True, check=True)
    assert run.stdout.strip() == f"https://10.0.0.5:9999/{_lib_var('ALPINE_PAGE')}"
    run2 = subprocess.run(base, capture_output=True, text=True, check=True)
    assert run2.stdout.strip() == f"https://127.0.0.1:9999/{_lib_var('ALPINE_PAGE')}"

    # The renderer must REFUSE to run without the lib-derived args (their
    # single home is scripts/lib/webvm-common.sh — no Python defaults).
    missing = [a for a in base if a != "--site-port" and a != "9999"]
    run3 = subprocess.run(missing, capture_output=True, text=True)
    assert run3.returncode != 0
    assert "--site-port" in run3.stderr


def test_github_pages_config_matches_main_config():
    """The GitHub Pages variant (config_public_alpine_github.js) must carry
    the SAME guest launch parameters as the nginx-served config — only the
    disk device type/URL differ (bytes vs chunked github). A divergence would
    boot a different guest on Pages than on the self-hosted server."""
    main = (ROOT / "webvm" / "config_public_alpine.js").read_text()
    gh = (ROOT / "webvm" / "config_public_alpine_github.js").read_text()
    for literal in (
        "export const printIntro = false;",
        "export const needsDisplay = true;",
        'export const cmd = "/sbin/init";',
        "export const args = [];",
        "uid: 0,",
        "gid: 0",
    ):
        assert literal in main, f"main config missing {literal!r}"
        assert literal in gh, f"github config missing {literal!r}"
    assert 'diskImageType = "bytes"' in main
    assert 'diskImageType = "github"' in gh


def test_build_fingerprint_inputs_pinned():
    """The cacheId fingerprint (build.sh) must stay deterministic across the
    working-tree/CI differences that have historically churned it: bytecode,
    macOS cruft and the guest .ssh tree are EXCLUDED; the Dockerfile, the
    fix-shim source, the patched Tcl lib and the credential-bearing sync args
    are INCLUDED. A changed input set silently re-keys every browser overlay
    (or, worse, stops re-keying after a credential change)."""
    build = (ROOT / "build.sh").read_text()
    for needle in (
        "cat diskimage/Dockerfile;",
        "cat diskimage/faccessat-fix.c",
        "cat diskimage/trace/libtcl8.6.so.patched",
        "-not -name '*.pyc'",
        "-not -path '*/__pycache__/*'",
        "-not -name '.DS_Store'",
        "-not -path '*/rootfs/home/user/.ssh/*'",
        'echo "SYNC_URL=$SYNC_URL_EFF SYNC_USER=$SYNC_USER_EFF SYNC_PASS=$SYNC_PASS_EFF";',
        'echo "SAMBA_HOST=$SAMBA_HOST_EFF SAMBA_SHARE=$SAMBA_SHARE_EFF SAMBA_USER=$SAMBA_USER_EFF SAMBA_PASS=$SAMBA_PASS_EFF"',
        'echo "$STORAGE_BACKEND";',
        "shasum -a 256",
        "cut -c1-12",
    ):
        assert needle in build, f"fingerprint input pin missing: {needle}"


# --------------------------------------------------------------------------
# Guest copy of the shared lib (paste-typer.sh sources it in the image)
# --------------------------------------------------------------------------

def test_guest_lib_copy_matches_shared_lib():
    """The guest image cannot source the repo's scripts/lib/webvm-common.sh
    (the diskimage build context is diskimage/, not the repo root), so the
    guest ships a CHECKED-IN copy at diskimage/rootfs/usr/local/lib/. It must
    stay byte-identical to the repo home — the paste-typer and any future
    guest consumer run the SAME helpers the server/gateway run, and this pin
    is the enforcement (like the frontend literal pins)."""
    repo_lib = (ROOT / "scripts" / "lib" / "webvm-common.sh").read_text()
    guest_lib = ROOT / "diskimage" / "rootfs" / "usr" / "local" / "lib" / "webvm-common.sh"
    assert guest_lib.exists(), "guest copy of the shared lib missing"
    assert guest_lib.read_text() == repo_lib, (
        "diskimage/rootfs/usr/local/lib/webvm-common.sh must be byte-identical "
        "to scripts/lib/webvm-common.sh — re-copy it when the shared lib changes"
    )


def test_guest_paste_typer_sources_shared_lib():
    """paste-typer.sh must source the guest copy of the shared lib (the
    supervisor wrapper single home) — never a private copy of its own."""
    typer = (ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "paste-typer.sh").read_text()
    assert "webvm-common.sh" in typer, (
        "paste-typer.sh must source the shared lib (guest copy at "
        "/usr/local/lib/webvm-common.sh)"
    )


# --------------------------------------------------------------------------
# build.sh argument handling (dies before any docker call)
# --------------------------------------------------------------------------

def test_build_sh_rejects_unknown_backend():
    """An unknown STORAGE_BACKEND must fail BEFORE any docker work (the
    check runs before the first docker invocation)."""
    res = subprocess.run(
        ["sh", str(ROOT / "build.sh"), "bogus"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert res.returncode == 1
    assert "Unknown STORAGE_BACKEND" in res.stderr


# --------------------------------------------------------------------------
# gen-certs.sh: SAN construction (IP literal vs hostname), CA reuse, and the
# SAN-coverage skip (a regenerated cert is NOT written when the current one
# already covers the env — `make up` runs this every launch).
# --------------------------------------------------------------------------

def test_gen_certs_san_and_reuse(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    cert_dir = tmp_path / "certs"
    base_env = {
        "PATH": os.environ["PATH"],
        "CERT_DIR": str(cert_dir),
        "CONTROL_HOST": "127.0.0.1",
        "LAN_IP": "192.168.1.10",
        "GATEWAY_CONTROL_IP": "172.28.0.10",
    }

    def run_gen(env):
        return subprocess.run(
            ["sh", str(ROOT / "scripts" / "gen-certs.sh")],
            capture_output=True, text=True, env={**os.environ, **env},
        )

    def san():
        out = subprocess.run(
            ["openssl", "x509", "-in", str(cert_dir / "server.crt"),
             "-noout", "-ext", "subjectAltName"],
            capture_output=True, text=True, check=True,
        ).stdout
        return out

    res = run_gen(base_env)
    assert res.returncode == 0, res.stderr
    for name in ("ca.key", "ca.crt", "server.key", "server.crt"):
        assert (cert_dir / name).exists(), f"missing {name}"

    s = san()
    assert "DNS:localhost" in s
    # IP literals never get DNS entries; the IP SANs cover CONTROL_HOST
    # (127.0.0.1), the LAN IP and the compose-network IP.
    assert "IP Address:127.0.0.1" in s
    assert "IP Address:192.168.1.10" in s
    assert "IP Address:172.28.0.10" in s
    assert "DNS:127.0.0.1" not in s, "an IP literal must not get a DNS SAN entry"

    ca_mtime = (cert_dir / "ca.key").stat().st_mtime_ns
    server_mtime = (cert_dir / "server.key").stat().st_mtime_ns

    # Same env again: the CA is reused AND the server cert is NOT rewritten
    # (its SAN already covers the env — the 2026-08-29 coverage skip).
    res2 = run_gen(base_env)
    assert res2.returncode == 0, res2.stderr
    assert (cert_dir / "ca.key").stat().st_mtime_ns == ca_mtime, \
        "the CA must never be regenerated"
    assert (cert_dir / "server.key").stat().st_mtime_ns == server_mtime, \
        "the server cert must be reused when its SAN already covers the env"

    # A HOSTNAME control host must get a DNS: entry (and the cert is
    # regenerated because the SAN changed).
    res3 = run_gen({**base_env, "CONTROL_HOST": "myhost"})
    assert res3.returncode == 0, res3.stderr
    assert "DNS:myhost" in san()
    assert (cert_dir / "server.key").stat().st_mtime_ns != server_mtime, \
        "a changed CONTROL_HOST must regenerate the server cert"

    # A changed GATEWAY_CONTROL_IP (the cert's compose-network SAN member —
    # the gateway's --login-server target) must ALSO regenerate the cert:
    # a stale SAN there makes the gateway's TLS verification fail silently.
    res4 = run_gen({**base_env, "GATEWAY_CONTROL_IP": "172.28.0.11"})
    assert res4.returncode == 0, res4.stderr
    assert "IP Address:172.28.0.11" in san()
    assert "IP Address:172.28.0.10" not in san()
    assert (cert_dir / "server.key").stat().st_mtime_ns != server_mtime, \
        "a changed GATEWAY_CONTROL_IP must regenerate the server cert"

    # The cert must be valid for more than a day (CERT_DAYS default 3650).
    check = subprocess.run(
        ["openssl", "x509", "-in", str(cert_dir / "server.crt"),
         "-noout", "-checkend", "86400"],
        capture_output=True, text=True,
    )
    assert check.returncode == 0, "server cert expires within a day"


# --------------------------------------------------------------------------
# Supervisor helpers (marker contract — the container-restart mechanism)
# --------------------------------------------------------------------------

def test_webvm_supervise_helpers(tmp_path):
    """The supervise marker contract: the wrapper writes the service's real
    pid + a status marker on exit (a zombie child would make kill -0 lie —
    the marker cannot); the supervisor exits 1 on a pre-existing marker; the
    kill helper actually kills the recorded pid; and the optional
    WEBVM_SUPERVISE_STDIN_FD knob passes a caller-held fd (the paste-typer's
    command FIFO) through to the service's stdin — an async subshell's stdin
    is /dev/null, so without the knob the backend would never see commands."""
    supdir = tmp_path / "sup"
    fifo = tmp_path / "svc.fifo"
    script = f'''
        . {shlex.quote(str(LIB))}
        mkdir -p {supdir}
        export WEBVM_SUPERVISE_DIR={supdir}
        marker=$(webvm_supervise_start sleeper log sleep 1)
        echo "marker=$marker"
        for _i in $(seq 1 50); do [ -f {supdir}/webvm-sleeper.pid ] && break; sleep 0.1; done
        echo "pidfile={supdir}/webvm-sleeper.pid"
        echo "pid=$(cat {supdir}/webvm-sleeper.pid)"
        for _i in $(seq 1 50); do [ -f "$marker" ] && break; sleep 0.1; done
        echo "status=$(cat "$marker")"
        ( webvm_supervise "" "$marker" >/dev/null 2>&1 ); echo "supervise-rc=$?"
        webvm_supervise_start sleeper2 /dev/null sleep 30 >/dev/null
        for _i in $(seq 1 50); do [ -f {supdir}/webvm-sleeper2.pid ] && break; sleep 0.1; done
        webvm_kill_supervised sleeper2
        sleep 0.3
        if kill -0 "$(cat {supdir}/webvm-sleeper2.pid)" 2>/dev/null; then
            echo "sleeper2=alive"
        else
            echo "sleeper2=dead"
        fi
        # fd passthrough: a service reading stdin must see the FIFO. The
        # service (head -n1) exits after ONE line, so the wrapper's wait
        # returns and the wrapper exits — a never-exiting service would leak
        # the wrapper, which inherits a copy of the capture stdout pipe and
        # blocks the test's EOF read forever.
        mkfifo {fifo}
        exec 9<>{fifo}
        WEBVM_SUPERVISE_STDIN_FD=9
        webvm_supervise_start reader /dev/null sh -c 'head -n 1 > {supdir}/reader.out' >/dev/null
        WEBVM_SUPERVISE_STDIN_FD=""
        printf 'hello-from-fifo\\n' >&9
        for _i in $(seq 1 50); do [ -f {supdir}/webvm-reader.status ] && break; sleep 0.1; done
        echo "reader=$(cat {supdir}/reader.out 2>/dev/null)"
        echo "reader-status=$(cat {supdir}/webvm-reader.status 2>/dev/null || echo absent)"
    '''
    res = _sh(script)
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0] == f"marker={supdir}/webvm-sleeper.status"
    assert lines[1] == f"pidfile={supdir}/webvm-sleeper.pid"
    assert lines[2].startswith("pid=") and int(lines[2].split("=", 1)[1]) > 0
    assert lines[3] == "status=dead"
    assert lines[4] == "supervise-rc=1"
    assert lines[5] == "sleeper2=dead"
    assert lines[6] == "reader=hello-from-fifo"
    assert lines[7] == "reader-status=dead"


# --------------------------------------------------------------------------
# Periodic storage-reset countdown (scripts/reset-cycle.sh + renderer)
# --------------------------------------------------------------------------

RESET_CYCLE = ROOT / "scripts" / "reset-cycle.sh"


def _run_reset_cycle(env, cwd=None):
    return subprocess.run(
        [str(RESET_CYCLE)], capture_output=True, text=True,
        env={**os.environ, "WEBVM_COMMON": str(LIB), **env},
        cwd=str(cwd) if cwd else None,
    )


def test_reset_cycle_defaults_are_off():
    """The facility is OPT-IN: the shared lib defaults must leave
    RESET_INTERVAL_HOURS empty (off) so a deployment that never sets it gets
    no countdown and reset-cycle.sh refuses to run."""
    assert _lib_var("RESET_INTERVAL_HOURS") == ""
    assert _lib_var("RESET_STATE_DIR") == "./state/reset"
    assert _lib_var("RESET_DEADLINE_FILE") == "/etc/webvm/reset/deadline"


def test_reset_cycle_refuses_when_not_enabled(tmp_path):
    """Without RESET_INTERVAL_HOURS the cycle must refuse to run (opt-in),
    and a non-numeric / zero interval must fail closed — a wrong cadence must
    never silently start wiping storage."""
    res = _run_reset_cycle({"STORAGE_BACKEND": "webdav"}, cwd=tmp_path)
    assert res.returncode == 1
    assert "RESET_INTERVAL_HOURS" in res.stderr

    res2 = _run_reset_cycle(
        {"STORAGE_BACKEND": "webdav", "RESET_INTERVAL_HOURS": "soon"}, cwd=tmp_path)
    assert res2.returncode == 1
    assert "positive integer" in res2.stderr

    res3 = _run_reset_cycle(
        {"STORAGE_BACKEND": "webdav", "RESET_INTERVAL_HOURS": "0"}, cwd=tmp_path)
    assert res3.returncode == 1
    assert ">= 1" in res3.stderr


def test_reset_cycle_dry_run_writes_deadline(tmp_path):
    """The dry-run performs the deadline computation + write (the only pure,
    testable part of the cycle) and lists the real steps without touching
    docker/git/make. The deadline must be now + interval hours (within a
    small clock skew), and it must be written to RESET_STATE_DIR/deadline —
    the file the server entrypoint bakes into /webvm-config.js."""
    state_dir = tmp_path / "state" / "reset"
    env = {
        "STORAGE_BACKEND": "webdav",
        "RESET_INTERVAL_HOURS": "6",
        "RESET_STATE_DIR": str(state_dir),
        "RESET_CYCLE_DRY_RUN": "1",
    }
    before = int(time.time())
    res = _run_reset_cycle(env, cwd=tmp_path)
    after = int(time.time())
    assert res.returncode == 0, res.stderr
    assert "dry-run" in res.stdout
    assert "stop the stack" in res.stdout
    assert "git pull" in res.stdout
    assert "make up-tailnet" in res.stdout

    deadline = int((state_dir / "deadline").read_text().strip())
    # now + 6h, within the subprocess' wall time
    assert before + 6 * 3600 <= deadline <= after + 6 * 3600


def test_reset_cycle_webdav_wipe_uses_data_dir(tmp_path):
    """The wipe step must target DATA_DIR (the compose webdav mount), never
    the state dir holding the deadline — the deadline must survive the wipe
    and be served to the countdown after the storage is gone."""
    text = RESET_CYCLE.read_text()
    assert 'rm -rf "$DATA_DIR"' in text, "wipe must target DATA_DIR"
    assert "STATE_DIR" in text
    assert "$DATA_DIR" in text and "STATE_DIR" in text


def test_render_config_reset_deadline():
    """--reset-deadline bakes the epoch into the page config as resetDeadline
    (the sidebar countdown's source); omitting it must NOT render the key."""
    base = [
        "python3", str(ROOT / "server" / "render-webvm-config.py"),
        "--control-host", "127.0.0.1", "--control-port", "8443",
        "--auth-key", "k", "--backend", "webdav",
        "--gateway-ip", "100.64.0.1", "--webdav-port", "8082",
        "--webdav-user", "u", "--webdav-pass", "p",
        "--webdav-base-path", "/webdav/", "--alpine-page", "alpine.html",
    ]
    without = subprocess.run(base, capture_output=True, text=True, check=True)
    assert "resetDeadline" not in without.stdout

    with_deadline = subprocess.run(
        base + ["--reset-deadline", "1750000000"],
        capture_output=True, text=True, check=True,
    )
    assert '"resetDeadline": 1750000000' in with_deadline.stdout

    # A deadline must be a whole epoch > 0: anything else is a config bug.
    for bad in ("abc", "0", "-5", "1.5", "1e6"):
        res = subprocess.run(
            base + ["--reset-deadline", bad], capture_output=True, text=True,
        )
        assert res.returncode != 0, f"--reset-deadline {bad} must be rejected"


def test_reset_deadline_key_lockstep():
    """The resetDeadline key must agree across the renderer (server), the
    entrypoint (server), the shared lib docs, and the frontend reader — a
    rename on one side silently kills the countdown on the other."""
    renderer = (ROOT / "server" / "render-webvm-config.py").read_text()
    entrypoint = (ROOT / "server" / "entrypoint.sh").read_text()
    frontend = (ROOT / "webvm" / "src" / "lib" / "resetCountdown.js").read_text()
    assert '"resetDeadline"' in renderer
    assert "--reset-deadline" in entrypoint
    assert "resetDeadline" in frontend
    assert "RESET_INTERVAL_HOURS" in (ROOT / ".env.example").read_text()
    assert "RESET_INTERVAL_HOURS" in (ROOT / "compose.yaml").read_text()
