#!/usr/bin/env python3
"""Render the baked page config (/webvm-config.js) from argv — or, with
--url, the explicit-hash session URL that scripts/print-url.sh prints, or
with --render-csp the nginx Content-Security-Policy header text (the single
home of the CSP: the entrypoint renders /etc/nginx/csp.conf from this, so
the page's connect-src allowlist cannot drift from the control-plane URLs
this module derives).

Single source of truth for the URL/param derivation that the page consumes:
the baked /webvm-config.js (opening the site root) and the `make url` hash
URL (explicit sessions, other devices) are TWO renderings of ONE
configuration, so they must be built by one function —
tests/unit/test_scripts.py cross-checks the two renderings so they cannot
drift apart. Named options on purpose: the entrypoint, print-url.sh and the
tests each call this, and a positional-arg reorder would silently render a
wrong config.
"""
import argparse
import json
import sys
import urllib.parse


def build_config(control_host, control_port, auth_key, backend, gateway_ip,
                 webdav_port, webdav_user, webdav_pass,
                 webdav_base_path="/webdav/", reset_deadline=None):
    config = {
        "controlUrl": f"https://{control_host}:{control_port}",
        "authKey": auth_key,
    }
    if backend == "webdav":
        config["syncUrl"] = f"http://{gateway_ip}:{webdav_port}{webdav_base_path}"
        config["syncUser"] = webdav_user
        config["syncPass"] = webdav_pass
    # Optional: epoch-seconds of the NEXT periodic storage reset (the sidebar
    # countdown). Only rendered when the facility is enabled — the entrypoint
    # passes it through from the deadline file written by
    # scripts/reset-cycle.sh. Served to every visitor on purpose (a countdown
    # is not a secret).
    if reset_deadline is not None:
        config["resetDeadline"] = reset_deadline
    return config


def render_csp(control_host, control_port):
    """The nginx Content-Security-Policy header text (single home — the
    entrypoint renders /etc/nginx/csp.conf from this; a second envsubst
    template could drift from the page's connect-src needs). connect-src is
    'self' + the control plane ONLY: it blocks the compiled-in Tailscale
    logtail fetch (and any third-party request) — the page and WASM client
    make ZERO external requests. The control host:port pair covers every
    control-plane URL: the page glue re-inserts the control port into the
    wasm client's port-dropped URLs (wss://<host>/ts2021, /derp and
    https://<host>/derp/probe), so all of them dial CONTROL_PORT — the
    scheme-default 443 is never used (never portless, which would open the
    WHOLE host to connect-src).
    """
    return (
        'add_header Content-Security-Policy "default-src \'self\'; '
        + "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        + "worker-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
        + "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        + "font-src 'self' data:; "
        + f"connect-src 'self' https://{control_host}:{control_port} "
        + f"wss://{control_host}:{control_port}\" always;\n"
    )


def _positive_epoch(value):
    """argparse type for --reset-deadline: a whole-number epoch > 0 (a
    deadline of 0/negative/past-the-epoch is a config bug, not a feature)."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid reset deadline {value!r}: not an integer"
        )
    if n <= 0:
        raise argparse.ArgumentTypeError(
            f"invalid reset deadline {value!r}: must be > 0"
        )
    return n


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[1].strip(),
    )
    parser.add_argument("--url", action="store_true",
                        help="print the explicit-hash session URL (print-url.sh)")
    parser.add_argument("--render-csp", action="store_true",
                        help="print the nginx CSP header text (entrypoint renders /etc/nginx/csp.conf from this)")
    # No defaults for the deployment values here: their single home is
    # scripts/lib/webvm-common.sh (SITE_PORT / WEBDAV_BASE_PATH / ALPINE_PAGE),
    # and every caller (server entrypoint, print-url.sh, the unit tests) must
    # pass the lib values explicitly — a Python-side default would silently
    # become a second home that drifts when the lib changes.
    parser.add_argument("--site-port", default=None,
                        help="SITE_PORT for the URL (REQUIRED for --url; pass the shared lib value)")
    parser.add_argument("--lan-ip", default=None,
                        help="LAN_IP for browser/none URLs (default = CONTROL_HOST)")
    parser.add_argument("--webdav-base-path", default=None,
                        help="WebDAV URL base path (REQUIRED for webdav --backend; pass scripts/lib/webvm-common.sh's WEBDAV_BASE_PATH)")
    parser.add_argument("--alpine-page", default=None,
                        help="desktop page route (REQUIRED for --url; pass scripts/lib/webvm-common.sh's ALPINE_PAGE)")
    parser.add_argument("--control-host", required=True)
    parser.add_argument("--control-port", required=True)
    # Deployment values: required for the config/--url renderings, but NOT
    # for --render-csp — the CSP header must render before any secret exists
    # (the entrypoint calls --render-csp with ONLY host/port). required=True
    # here used to make the entrypoint's render FAIL at parse_args, which the
    # entrypoint's set -u (not -e) silently swallowed: > /etc/nginx/csp.conf
    # truncated the file to EMPTY and the site served without a CSP header
    # (the 2026-08-31 CI regression). The paths that need these values
    # enforce them explicitly below (fail closed — a missing value must never
    # silently render a broken config).
    parser.add_argument("--auth-key", default=None)
    parser.add_argument("--backend", default=None)
    parser.add_argument("--gateway-ip", default=None)
    parser.add_argument("--webdav-port", default=None)
    parser.add_argument("--webdav-user", default=None)
    parser.add_argument("--webdav-pass", default=None)
    parser.add_argument("--reset-deadline", type=_positive_epoch, default=None,
                        help="epoch-seconds of the NEXT periodic storage reset "
                             "(sidebar countdown; the entrypoint passes the "
                             "deadline file written by scripts/reset-cycle.sh)")
    args = parser.parse_args(argv)

    if args.render_csp:
        sys.stdout.write(render_csp(args.control_host, args.control_port))
        return 0

    missing = [f"--{name}" for name in (
        "auth-key", "backend", "gateway-ip", "webdav-port", "webdav-user",
        "webdav-pass",
    ) if getattr(args, name.replace("-", "_")) is None]
    if missing:
        parser.error("the following arguments are required: " + ", ".join(missing))

    if args.backend == "webdav" and args.webdav_base_path is None:
        parser.error(
            "--webdav-base-path is required for webdav (pass scripts/lib/"
            "webvm-common.sh's WEBDAV_BASE_PATH — no defaults live here)"
        )

    config = build_config(
        args.control_host, args.control_port, args.auth_key,
        args.backend, args.gateway_ip, args.webdav_port,
        args.webdav_user, args.webdav_pass,
        webdav_base_path=args.webdav_base_path or "/webdav/",
        reset_deadline=args.reset_deadline,
    )

    if args.url:
        if args.site_port is None:
            parser.error("--site-port is required for --url (pass the shared lib's SITE_PORT)")
        if args.alpine_page is None:
            parser.error("--alpine-page is required for --url (pass the shared lib's ALPINE_PAGE)")
        if config["authKey"]:
            params = {"authKey": config["authKey"], "controlUrl": config["controlUrl"]}
            if config.get("syncUrl"):
                params["syncUrl"] = config["syncUrl"]
                params["syncUser"] = config.get("syncUser", "")
                params["syncPass"] = config.get("syncPass", "")
            base = f"https://{args.control_host}:{args.site_port}/{args.alpine_page}"
            print(base + "#" + urllib.parse.urlencode(params))
        else:
            lan_ip = args.lan_ip or args.control_host
            print(f"https://{lan_ip}:{args.site_port}/{args.alpine_page}")
        return 0

    print("window.__webvmConfig = ", end="")
    json.dump(config, sys.stdout, ensure_ascii=True)
    print(";")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
