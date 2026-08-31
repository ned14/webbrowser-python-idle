#!/usr/bin/env python3
"""Unit tests for the server templates (nginx / headscale / wsgidav) and the
entrypoint's fail-closed secret checks.

The templates are rendered with an envsubst-equivalent shim that substitutes
only the EXPLICIT variable list (mirroring `envsubst '$A $B'` — never bare
envsubst, which would mangle `$` in credentials and nginx variables). The
CSP header is NOT a template anymore: it is rendered by the production
renderer itself (server/render-webvm-config.py --render-csp), so the test
exercises the exact production function.
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
    "WEBDAV_BASE_PATH": "/webdav/",
    # Rendered by the entrypoint from scripts/lib/webvm-common.sh (the single
    # home of the deployment constants; the compose-drift test in
    # test_scripts.py pins the lib values).
    "ALPINE_PAGE": "alpine.html",
    "WEBVM_IMAGE_DIR": "custom-disk-images",
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
    rendered = envsubst(template, TEST_ENV)
    # The static snippets (shipped to /etc/nginx/ by the Dockerfile) are part
    # of the served config: inline them so the assertions exercise the FULL
    # rendered configuration, exactly as nginx would load it.
    for snippet_name in ("control-location.conf", "site-subresource-headers.conf"):
        body = (SERVER / snippet_name).read_text()
        rendered = rendered.replace(
            f"include /etc/nginx/{snippet_name};", body
        )
    return rendered


@pytest.fixture()
def csp(render_webvm_config):
    # The production renderer is the single home of the CSP text (the
    # entrypoint renders /etc/nginx/csp.conf from it).
    return render_webvm_config.render_csp(TEST_ENV["CONTROL_HOST"], TEST_ENV["CONTROL_PORT"])


@pytest.fixture()
def headscale():
    template = (SERVER / "headscale" / "config.yaml.template").read_text()
    return envsubst(template, TEST_ENV)


@pytest.fixture()
def wsgidav():
    template = (SERVER / "wsgidav.yaml.template").read_text()
    return envsubst(template, TEST_ENV)


# --------------------------------------------------------------------------
# Template <-> envsubst-list drift (boot-time failure class): a ${VAR} added
# to a template but not to its envsubst list ships a LITERAL ${VAR} into the
# container config and nginx -t / headscale fails at container start.
# --------------------------------------------------------------------------

def test_template_vars_covered_by_entrypoint_envsubst_lists():
    entrypoint = (SERVER / "entrypoint.sh").read_text()
    lines = entrypoint.splitlines()

    # Pair every `envsubst '$A $B'` line with the `< /etc/webvm/<template>`
    # input redirect (same line or the wrapped next line).
    lists = {}
    for i, line in enumerate(lines):
        m = re.search(r"envsubst '([^']*)'", line)
        if not m:
            continue
        scope = "\n".join(lines[i: i + 2])
        tm = re.search(r"< /etc/webvm/([^ ]+)", scope)
        assert tm, f"envsubst call without a template input: {line}"
        # Each listed var carries its envsubst '$' prefix — strip it.
        lists[tm.group(1)] = set(v.lstrip("$") for v in m.group(1).split())

    assert "nginx.conf.template" in lists, "nginx envsubst list missing"
    assert "headscale/config.yaml.template" in lists, "headscale envsubst list missing"

    for template_name in ("nginx.conf.template", "headscale/config.yaml.template",
                          "wsgidav.yaml.template"):
        template_path = SERVER / template_name
        if template_name == "headscale/config.yaml.template":
            template_path = SERVER / "headscale" / "config.yaml.template"
        text = template_path.read_text()
        used = set(re.findall(r"\$\{([A-Z_]+)\}", text))
        assert used, f"{template_name}: no ${VAR} found to check"
        missing = used - lists[template_name]
        assert not missing, (
            f"{template_name} uses ${{{missing.pop()}}} but the entrypoint's "
            f"envsubst list for it is '{' '.join(sorted(lists[template_name]))}' — "
            "a literal ${VAR} would ship into the container config and break "
            "nginx -t / headscale at startup"
        )

    # The nginx template additionally renders two lib constants that the
    # entrypoint passes from the shared lib.
    assert "WEBVM_IMAGE_DIR" in lists["nginx.conf.template"]
    assert "ALPINE_PAGE" in lists["nginx.conf.template"]
    assert "WEBDAV_BASE_PATH" in lists["wsgidav.yaml.template"]


class TestNginx:
    def test_site_headers(self, nginx):
        assert 'listen 8081 ssl;' in nginx
        assert 'add_header Cross-Origin-Opener-Policy "same-origin" always;' in nginx
        assert 'add_header Cross-Origin-Embedder-Policy "require-corp" always;' in nginx
        assert 'add_header Cross-Origin-Resource-Policy "cross-origin" always;' in nginx

    def test_csp_connect_src_self_and_control_only(self, nginx, csp):
        # The CSP connect-src must allow only 'self' + the control host:port
        # family (blocks logtail and any other third-party fetch). The scheme-
        # default 443 entries are the wasm client's PORT-DROPPED control-plane
        # URLs (wss://<host>/ts2021, /derp, /derp/probe) — scoped to :443, never
        # portless.
        assert "connect-src 'self' https://127.0.0.1:8443 wss://127.0.0.1:8443" in csp
        assert "https://127.0.0.1:443 wss://127.0.0.1:443" in csp
        # No portless host entry (would open the whole host to connect-src)
        assert "wss://127.0.0.1 " not in csp
        # script-src covers the self-hosted CheerpX runtime (never the CDN)
        assert "script-src 'self'" in csp
        # The header text lives ONCE (rendered by render-webvm-config.py
        # --render-csp) and is included from the site server block and every
        # location that defines its own add_header (nginx does not inherit
        # add_header through them).
        assert nginx.count("include /etc/nginx/csp.conf;") == 3

    def test_csp_lan_host_scoping(self, render_webvm_config):
        # The LAN deployment's CSP (a hardcoded LAN IP as CONTROL_HOST) must
        # carry the same scoped allowlist: control host:port + the portless
        # :443 scheme-default pair, and never a portless host entry.
        csp = render_webvm_config.render_csp("192.168.1.10", "8443")
        assert "connect-src 'self' https://192.168.1.10:8443 wss://192.168.1.10:8443" in csp
        assert "https://192.168.1.10:443 wss://192.168.1.10:443" in csp
        assert "wss://192.168.1.10 " not in csp
        assert "127.0.0.1" not in csp

    def test_webvm_config_location_no_store_and_corp(self, nginx):
        # The baked page config carries the preauth key + sync credentials;
        # it must never be cached AND must refuse cross-origin loads (CORP
        # same-origin) so no other webpage can read window.__webvmConfig.
        assert "location = /webvm-config.js {" in nginx
        assert 'add_header Cache-Control "no-store";' in nginx
        assert 'add_header Cross-Origin-Resource-Policy "same-origin";' in nginx

    def test_site_redirects(self, nginx):
        # The desktop route renders from the lib ALPINE_PAGE via envsubst
        # (single home — the entrypoint passes the value).
        assert "location = / {" in nginx
        assert "return 302 alpine.html;" in nginx
        assert "location = /alpine {" in nginx
        assert "return 301 alpine.html;" in nginx

    def test_ext2_alias_with_absolute_root(self, nginx):
        assert "location /custom-disk-images/ {" in nginx
        assert "alias /srv/webvm/custom-disk-images/;" in nginx
        assert "gzip off;" in nginx
        # The ext2 is content-fingerprinted (?v=<image-build> in the page
        # config), so it can be cached immutable — repeat boots must not
        # revalidate a 137 MiB image.
        assert 'Cache-Control "public, max-age=31536000, immutable"' in nginx

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

    def test_server_url_matches_page_control_url(self, headscale, render_webvm_config):
        # The headscale server_url (envsubst template) and the baked page
        # config's controlUrl (render-webvm-config.py) are TWO renderings of
        # the same URL — the page's control-plane WebSocket and the DERP map
        # headscale derives from server_url must point at the same place.
        config = render_webvm_config.build_config(
            "127.0.0.1", "8443", "k", "webdav", "100.64.0.1", "8082", "u", "p",
        )
        assert f'server_url: "{config["controlUrl"]}"' in headscale

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
