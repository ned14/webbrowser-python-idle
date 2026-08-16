#!/usr/bin/env python3
"""Unit tests for the server templates (nginx / headscale / wsgidav) and the
entrypoint's fail-closed secret checks.

The templates are rendered with an envsubst-equivalent shim that substitutes
only the EXPLICIT variable list (mirroring `envsubst '$A $B'` — never bare
envsubst, which would mangle `$` in credentials and nginx variables).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"

TEST_ENV = {
    "CONTROL_HOST": "127.0.0.1",
    "CONTROL_PORT": "8443",
    "SITE_PORT": "8081",
    "STUN_PORT": "3478",
    "WEBDAV_PORT": "8082",
    "WEBDAV_ROOT": "/data/webdav",
    "WEBDAV_USER": "webdav",
    "WEBDAV_PASS": "s3cr$et",
}


def envsubst(text, values):
    """Substitute only the listed variables (envsubst 'list' semantics)."""

    def repl(match):
        key = match.group(1) or match.group(2)
        return values.get(key, match.group(0))

    return re.sub(r"\$\{(\w+)\}|\$(\w+)", repl, text)


@pytest.fixture()
def nginx():
    template = (SERVER / "nginx.conf.template").read_text()
    return envsubst(template, TEST_ENV)


@pytest.fixture()
def headscale():
    template = (SERVER / "headscale" / "config.yaml.template").read_text()
    return envsubst(template, TEST_ENV)


@pytest.fixture()
def wsgidav():
    template = (SERVER / "wsgidav.yaml.template").read_text()
    return envsubst(template, TEST_ENV)


class TestNginx:
    def test_site_headers(self, nginx):
        assert 'listen 8081 ssl;' in nginx
        assert 'add_header Cross-Origin-Opener-Policy "same-origin" always;' in nginx
        assert 'add_header Cross-Origin-Embedder-Policy "require-corp" always;' in nginx
        assert 'add_header Cross-Origin-Resource-Policy "cross-origin" always;' in nginx

    def test_csp_connect_src_self_and_control_only(self, nginx):
        # The CSP connect-src must allow only 'self' + the control host/port
        # (blocks logtail and any other third-party fetch).
        csp = [l for l in nginx.splitlines() if "Content-Security-Policy" in l][0]
        assert "connect-src 'self' https://127.0.0.1:8443 wss://127.0.0.1:8443" in csp
        # script-src covers the self-hosted CheerpX runtime (never the CDN)
        assert "script-src 'self'" in csp

    def test_site_redirects(self, nginx):
        assert "location = / {" in nginx
        assert "return 302 /alpine.html;" in nginx
        assert "location = /alpine {" in nginx
        assert "return 301 /alpine.html;" in nginx

    def test_ext2_alias_with_absolute_root(self, nginx):
        assert "location /custom-disk-images/ {" in nginx
        assert "alias /srv/webvm/custom-disk-images/;" in nginx
        assert "gzip off;" in nginx

    def test_headscale_catchall_proxy(self, nginx):
        # The control listener is a catch-all reverse proxy to headscale with
        # WebSocket handling (headscale's DERP handler answers 426 and its
        # TS2021 handler answers 500 to any non-upgraded request).
        assert "location / {" in nginx
        assert "proxy_pass http://127.0.0.1:8080;" in nginx
        assert "proxy_set_header Upgrade $http_upgrade;" in nginx
        assert "proxy_set_header Connection $connection_upgrade;" in nginx
        assert "proxy_buffering off;" in nginx

    def test_derp_locations(self, nginx):
        # The bare /derp path must be served (the embedded-DERP relay URL is
        # https://${CONTROL_HOST}:${CONTROL_PORT}/derp) — the catch-all covers
        # it, and a bare-path note keeps this explicit.
        assert "location / {" in nginx
        assert "/derp" in nginx

    def test_nginx_variables_not_mangled_by_envsubst(self, nginx):
        # $http_upgrade / $connection_upgrade / $uri are nginx variables and
        # must survive rendering untouched.
        assert "$http_upgrade" in nginx
        assert "$connection_upgrade" in nginx
        assert "$uri" in nginx

    def test_control_listener_cors_for_wasm_tailscale(self, nginx):
        # The browser-side CheerpX tailscale client fetches the control plane
        # /key endpoint cross-origin (page origin -> CONTROL_PORT); headscale
        # only answers /derp/probe with ACAO, so nginx must add it for the
        # whole control listener or the fetch is CORS-blocked and the guest
        # tailnet never starts.
        assert 'add_header Access-Control-Allow-Origin $http_origin always;' in nginx
        assert 'add_header Vary Origin always;' in nginx


class TestHeadscale:
    def test_server_url_pathless_from_control_host(self, headscale):
        # Verified against v0.29.3: the noise register path carries the
        # server_url PATH verbatim and headscale's noise router serves it at
        # the root, so server_url MUST be path-less.
        assert 'server_url: "https://127.0.0.1:8443"' in headscale

    def test_no_public_derp(self, headscale):
        assert "urls: []" in headscale

    def test_embedded_derp(self, headscale):
        assert "enabled: true" in headscale
        assert 'region_code: "webvm"' in headscale
        assert 'stun_listen_addr: "0.0.0.0:3478"' in headscale
        # No TEST-NET placeholders advertised
        assert "198.51.100.1" not in headscale
        assert "2001:db8" not in headscale
        assert 'ipv4: ""' in headscale

    def test_lan_only_dns_and_updates(self, headscale):
        assert "magic_dns: false" in headscale
        assert "disable_check_updates: true" in headscale
        assert "logtail:" in headscale
        assert "enabled: false" in headscale


class TestWsgidav:
    def test_provider_mapping_and_port(self, wsgidav):
        assert 'port: 8082' in wsgidav
        assert '"/webdav/": "/data/webdav"' in wsgidav

    def test_basic_auth(self, wsgidav):
        assert '"webdav":' in wsgidav
        assert 'password: "s3cr$et"' in wsgidav  # $ survives (explicit list)


class TestEntrypointFailClosed:
    def test_all_secret_checks_present(self):
        entrypoint = (SERVER / "entrypoint.sh").read_text()
        for needle in (
            "HEADSCALE_PREAUTHKEY",
            "GATEWAY_AUTHKEY",
            "WEBDAV_USER",
            "WEBDAV_PASS",
            "FATAL:",
            "HEADSCALE_BOOTSTRAP",
        ):
            assert needle in entrypoint
