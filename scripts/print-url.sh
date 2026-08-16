#!/bin/sh
# Print the URL(s) to open for the current deployment.
#
# The full hash URL carries credentials (authKey, WebDAV user/pass) — treat it
# like a password: it is printed to the terminal and saved by the user, but the
# page strips the hash into sessionStorage after load and removes it from
# history.
set -eu

STORAGE_BACKEND="${STORAGE_BACKEND:-browser}"
# Browser-facing control-plane host — must match server/entrypoint.sh (and
# compose.yaml/.env.example): default 127.0.0.1 for the zero-config single
# machine, hardcoded LAN IP on a LAN. HOSTNAMES ARE BANNED (host.docker.internal
# and /etc/hosts tricks are never to be reintroduced — the browser must reach
# the control plane over 127.0.0.1 / a LAN IP alone).
CONTROL_HOST="${CONTROL_HOST:-127.0.0.1}"
LAN_IP="${LAN_IP:-127.0.0.1}"
SITE_PORT="${SITE_PORT:-8081}"
CONTROL_PORT="${CONTROL_PORT:-8443}"
WEBDAV_PORT="${WEBDAV_PORT:-8082}"
GATEWAY_TAILNET_IP="${GATEWAY_TAILNET_IP:-}"

	case "$STORAGE_BACKEND" in
		browser|none)
			# HEADSCALE_ENABLED=1 forces the control plane on for browser/none
			# builds — the baked page config then auto-wires the tailnet, so
			# the explicit hash URL must carry the same params (see
			# tests/unit/test_scripts.py cross-check).
			if [ "${HEADSCALE_ENABLED:-0}" = "1" ]; then
				: "${HEADSCALE_PREAUTHKEY:?HEADSCALE_PREAUTHKEY is not set (see .env.example)}"
				echo "https://${CONTROL_HOST}:${SITE_PORT}/alpine.html#authKey=${HEADSCALE_PREAUTHKEY}&controlUrl=https://${CONTROL_HOST}:${CONTROL_PORT}"
			else
				echo "https://${LAN_IP}:${SITE_PORT}/alpine.html"
			fi
			;;
		samba)
			: "${HEADSCALE_PREAUTHKEY:?HEADSCALE_PREAUTHKEY is not set (see .env.example)}"
			echo "https://${CONTROL_HOST}:${SITE_PORT}/alpine.html#authKey=${HEADSCALE_PREAUTHKEY}&controlUrl=https://${CONTROL_HOST}:${CONTROL_PORT}"
			;;
		webdav)
			: "${HEADSCALE_PREAUTHKEY:?HEADSCALE_PREAUTHKEY is not set (see .env.example)}"
			: "${GATEWAY_TAILNET_IP:?GATEWAY_TAILNET_IP is not set - read it after the gateway first joins (headscale nodes list)}"
			: "${WEBDAV_USER:?WEBDAV_USER is not set (see .env.example)}"
			: "${WEBDAV_PASS:?WEBDAV_PASS is not set (see .env.example)}"
			echo "https://${CONTROL_HOST}:${SITE_PORT}/alpine.html#authKey=${HEADSCALE_PREAUTHKEY}&controlUrl=https://${CONTROL_HOST}:${CONTROL_PORT}&syncUrl=http://${GATEWAY_TAILNET_IP}:${WEBDAV_PORT}/webdav/&syncUser=${WEBDAV_USER}&syncPass=${WEBDAV_PASS}"
			;;
		*)
			echo "Unknown STORAGE_BACKEND: $STORAGE_BACKEND" >&2
			exit 1
			;;
	esac
