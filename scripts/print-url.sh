#!/bin/sh
# Print the URL(s) to open for the current deployment.
#
# The full hash URL carries credentials (authKey, WebDAV user/pass) — treat it
# like a password: it is printed to the terminal and saved by the user, but the
# page strips the hash into sessionStorage after load and removes it from
# history.
set -eu

STORAGE_BACKEND="${STORAGE_BACKEND:-browser}"
CONTROL_HOST="${CONTROL_HOST:-host.docker.internal}"
LAN_IP="${LAN_IP:-127.0.0.1}"
SITE_PORT="${SITE_PORT:-8081}"
CONTROL_PORT="${CONTROL_PORT:-8443}"
WEBDAV_PORT="${WEBDAV_PORT:-8082}"
GATEWAY_TAILNET_IP="${GATEWAY_TAILNET_IP:-}"

	case "$STORAGE_BACKEND" in
		browser|none)
			echo "https://${LAN_IP}:${SITE_PORT}/alpine.html"
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
