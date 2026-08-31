#!/bin/sh
# Print the URL(s) to open for the current deployment.
#
# The full hash URL carries credentials (authKey, WebDAV user/pass) — treat it
# like a password: it is printed to the terminal and saved by the user, but the
# page strips the hash into sessionStorage after load and removes it from
# history.
#
# The URL (and its params) is derived by server/render-webvm-config.py — the
# SAME code that renders the baked /webvm-config.js — so the hash URL and the
# baked config can never drift (tests/unit/test_scripts.py cross-checks both
# renderings). This script only enforces the per-mode secret requirements.
#
# Precedence: environment > .env > defaults (the shared lib applies the
# defaults and loads .env; the caller's environment always wins).
set -eu

# Shared defaults + .env loader + helpers.
WEBVM_COMMON="${WEBVM_COMMON:-$(dirname "$0")/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"
webvm_load_dotenv

# Per-mode secret requirements: the SAME fail-closed matrix the server
# entrypoint enforces (webvm_require_mode_secrets in the shared lib — the
# entrypoint additionally requires GATEWAY_AUTHKEY via --gateway-key; the
# URL printer does not need the gateway node key).
webvm_require_mode_secrets "$STORAGE_BACKEND"

python3 "$(dirname "$0")/../server/render-webvm-config.py" --url \
	--site-port "$SITE_PORT" --lan-ip "$LAN_IP" \
	--webdav-base-path "$WEBDAV_BASE_PATH" --alpine-page "$ALPINE_PAGE" \
	--control-host "$CONTROL_HOST" --control-port "$CONTROL_PORT" \
	--auth-key "${HEADSCALE_PREAUTHKEY:-}" --backend "$STORAGE_BACKEND" \
	--gateway-ip "${GATEWAY_TAILNET_IP:-}" --webdav-port "$WEBDAV_PORT" \
	--webdav-user "${WEBDAV_USER:-}" --webdav-pass "${WEBDAV_PASS:-}"
