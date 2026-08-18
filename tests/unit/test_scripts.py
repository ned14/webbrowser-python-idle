#!/usr/bin/env python3
"""Script hygiene: sh -n on all shell scripts, py_compile on the sync agent,
and a cross-check that the explicit-hash URL (scripts/print-url.sh) and the
baked page config (server/render-webvm-config.py) derive the SAME params."""

import json
import os
import py_compile
import subprocess
import urllib.parse
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
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "open-file-explorer.sh",
    ROOT / "diskimage" / "rootfs" / "usr" / "local" / "bin" / "keep-file-explorer.sh",
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
    ROOT / "diskimage" / "scripts" / "wm-clients.py",
    ROOT / "server" / "render-webvm-config.py",
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
    """The /webvm-config.js rendering (server/render-webvm-config.py args)."""
    run = subprocess.run(
        ["python3", str(ROOT / "server" / "render-webvm-config.py"),
         env["CONTROL_HOST"], env["CONTROL_PORT"], env["HEADSCALE_PREAUTHKEY"], backend,
         env["GATEWAY_TAILNET_IP"], env["WEBDAV_PORT"], env["WEBDAV_USER"], env["WEBDAV_PASS"]],
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
    entrypoint = (ROOT / "server" / "entrypoint.sh").read_text()
    print_url = (ROOT / "scripts" / "print-url.sh").read_text()
    compose = (ROOT / "compose.yaml").read_text()
    env_example = (ROOT / ".env.example").read_text()
    gen_certs = (ROOT / "scripts" / "gen-certs.sh").read_text()
    acceptance = (ROOT / "scripts" / "acceptance.sh").read_text()

    assert "${CONTROL_HOST:-127.0.0.1}" in entrypoint, \
        "server/entrypoint.sh CONTROL_HOST default must be 127.0.0.1"
    assert "${CONTROL_HOST:-127.0.0.1}" in print_url, \
        "scripts/print-url.sh CONTROL_HOST default must be 127.0.0.1"
    assert "${CONTROL_HOST:-127.0.0.1}" in compose, \
        "compose.yaml CONTROL_HOST default must be 127.0.0.1"
    assert "${CONTROL_HOST:-127.0.0.1}" in gen_certs, \
        "scripts/gen-certs.sh CONTROL_HOST default must be 127.0.0.1"
    assert "${CONTROL_HOST:-127.0.0.1}" in acceptance, \
        "scripts/acceptance.sh CONTROL_HOST default must be 127.0.0.1"
    assert "CONTROL_HOST=127.0.0.1" in env_example, \
        ".env.example CONTROL_HOST must document 127.0.0.1"

    # The banned hostname must appear NOWHERE in any runtime config, script,
    # test or CI file (comment lines are exempt — the ban is documented in
    # comments, but must never be USED). Plans may discuss it historically;
    # executable files never may.
    banned = [
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
        "tests/server/integration.sh",
        "tests/server/join-test-client.sh",
        "tests/rootfs/smoke.sh",
        "tests/e2e/playwright.config.js",
        "tests/e2e/lib/desktop.js",
        "tests/e2e/lib/webdav-auth.js",
        "tests/e2e/tests/boot.spec.js",
        "tests/e2e/tests/desktop.spec.js",
        "tests/e2e/tests/error-overlay.spec.js",
        "tests/e2e/tests/network.spec.js",
        "tests/e2e/tests/persistence.spec.js",
        "tests/e2e/tests/sync.spec.js",
        "tests/e2e/repro-tailnet.mjs",
        "tests/e2e/data-path-probe.mjs",
        "tests/e2e/big-put-probe.mjs",
        "tests/e2e/stream-put-probe.mjs",
        "tests/e2e/guest-socket-trace.mjs",
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
