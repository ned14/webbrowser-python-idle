#!/bin/sh
# Server entrypoint: fail-closed per-mode secret checks, config rendering
# (envsubst with an EXPLICIT variable list — never bare envsubst, which would
# mangle $ in credentials), then supervision of headscale + nginx (+ wsgidav
# in webdav mode).
set -u

# Shared defaults + helpers (scripts/lib/webvm-common.sh; COPY'd into the
# image at /etc/webvm/lib/).
WEBVM_COMMON="${WEBVM_COMMON:-/etc/webvm/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"

# `make up` is a HARD-NETWORKLESS launch: whatever .env contains, a
# server-only start has no gateway/control plane, so the page must boot
# fully disconnected — empty baked config, no headscale, no key validation,
# sidebar Networking crossed out and disabled, zero tailnet connection
# attempts. `make up-tailnet` (WEBVM_TAILNET=on) is the ONLY launch that
# may enable networking.
if [ "${WEBVM_TAILNET:-on}" = "off" ]; then
	echo "==> WEBVM_TAILNET=off: networking hard-disabled for this launch (make up)"
	STORAGE_BACKEND="none"
	# shellcheck disable=SC2034 # consumed by webvm_backend_needs_headscale (sourced lib)
	HEADSCALE_ENABLED="0"
	HEADSCALE_BOOTSTRAP="1"
fi
# CONTROL_HOST is the BROWSER-facing control-plane host (baked page config,
# nginx CSP allowlist, headscale server_url -> DERP map). Default 127.0.0.1 =
# zero-config single machine; LAN deployments set it (with LAN_IP) to the
# hardcoded LAN address (e.g. 192.168.1.10). HOSTNAMES ARE BANNED — the
# browser must reach the control plane over 127.0.0.1 / a LAN IP alone,
# never via host.docker.internal or /etc/hosts tricks (the gateway reaches
# the server over the compose network at GATEWAY_CONTROL_IP instead).
# (Defaults come from the shared lib sourced above.)

# --- Fail-closed per-mode secret checks ------------------------------------
# The per-mode matrix lives in the shared lib (webvm_require_mode_secrets —
# print-url.sh enforces the SAME requirements, so a mode added on one side
# and forgotten on the other can never silently weaken the fail-closed
# guarantee). The entrypoint additionally needs the gateway node key
# (--gateway-key) and skips the tailnet-dependent checks during bootstrap.
# need_headscale comes from the SAME shared matrix
# (webvm_backend_needs_headscale) the secret checks use — one home for the
# backend->control-plane decision.
need_headscale=0
if webvm_backend_needs_headscale "$STORAGE_BACKEND"; then
	need_headscale=1
fi

_bootstrap_flag=""
[ "$HEADSCALE_BOOTSTRAP" = "1" ] && _bootstrap_flag="--bootstrap"
_gateway_key_flag=""
if [ "$need_headscale" = "1" ] && [ "$HEADSCALE_BOOTSTRAP" != "1" ]; then
	_gateway_key_flag="--gateway-key"
fi
# shellcheck disable=SC2086
webvm_require_mode_secrets "$STORAGE_BACKEND" $_bootstrap_flag $_gateway_key_flag

# --- Render configuration templates -----------------------------------------
mkdir -p /etc/nginx /etc/headscale /etc/webvm /var/lib/headscale /var/run/headscale

# CSP header: rendered by render-webvm-config.py (the single home of the CSP
# text — the page's connect-src needs and the header cannot drift; there is
# no csp.conf.template anymore).
python3 /etc/webvm/render-webvm-config.py --render-csp \
	--control-host "$CONTROL_HOST" --control-port "$CONTROL_PORT" \
	> /etc/nginx/csp.conf

envsubst '$CONTROL_HOST $CONTROL_PORT $CONTROL_WSS_PORT $SITE_PORT $WEBVM_IMAGE_DIR $ALPINE_PAGE' \
	< /etc/webvm/nginx.conf.template > /etc/nginx/nginx.conf
envsubst '$CONTROL_HOST $CONTROL_PORT $STUN_PORT' \
	< /etc/webvm/headscale/config.yaml.template > /etc/headscale/config.yaml

if [ "$STORAGE_BACKEND" = "webdav" ]; then
	# Basic-auth file for the WebDAV endpoint (never a random password — the
	# sync agent's credentials must match).
	htpasswd -bc /etc/webvm/webdav.htpasswd "$WEBDAV_USER" "$WEBDAV_PASS"
	envsubst '$WEBDAV_PORT $WEBDAV_ROOT $WEBDAV_USER $WEBDAV_PASS $WEBDAV_BASE_PATH' \
		< /etc/webvm/wsgidav.yaml.template > /etc/webvm/wsgidav.yaml
fi

# --- Render the baked page config (/webvm-config.js) ------------------------
# The page reads its networking/sync secrets from the same-origin
# /webvm-config.js when the URL hash carries none, so visiting the site root
# just works — no hash URL needed (`make url` stays for other devices and
# explicit hash overrides). Values are JSON-escaped via render-webvm-config.py
# (never raw envsubst — credentials may contain quotes/backslashes/$). Never
# render keys before they exist: bootstrap mode (and browser/none builds)
# serve an empty config, so the page boots disconnected exactly as before.
if [ "$need_headscale" = "1" ] && [ "$HEADSCALE_BOOTSTRAP" != "1" ]; then
	python3 /etc/webvm/render-webvm-config.py \
		--control-host "$CONTROL_HOST" --control-port "$CONTROL_PORT" \
		--auth-key "${HEADSCALE_PREAUTHKEY:-}" --backend "$STORAGE_BACKEND" \
		--gateway-ip "${GATEWAY_TAILNET_IP:-}" --webdav-port "$WEBDAV_PORT" \
		--webdav-user "${WEBDAV_USER:-}" --webdav-pass "${WEBDAV_PASS:-}" \
		--webdav-base-path "$WEBDAV_BASE_PATH" --alpine-page "$ALPINE_PAGE" \
		> /etc/webvm/webvm-config.js
else
	echo 'window.__webvmConfig = {};' > /etc/webvm/webvm-config.js
fi

# --- Start headscale (only when needed) -------------------------------------
HS_MARKER=""
if [ "$need_headscale" = "1" ]; then
	echo "==> Starting headscale"
	# Supervised via a wrapper subshell (webvm_supervise_start): the wrapper
	# records headscale's real pid and writes a status marker when it exits,
	# so the supervisor below detects a crash even though the exited process
	# stays a zombie (PID 1 never reaps — kill -0 would lie).
	HS_MARKER=$(webvm_supervise_start headscale /var/log/headscale.log headscale serve)

	# The CLI talks to the running server over the unix socket. The socket
	# appears before headscale's RPC/DB layer is ready, so a dead headscale
	# must fail HERE with a clear message, not later at the key check.
	if ! webvm_wait_until 30 1 [ -S /var/run/headscale/headscale.sock ]; then
		echo "FATAL: headscale did not start (socket never appeared); see /var/log/headscale.log" >&2
		webvm_kill_supervised headscale
		exit 1
	fi

	# Ensure the user namespace exists (once). headscale serve creates the
	# socket before its RPC/DB layer is fully ready, so the very first
	# `users create` (and any concurrent bootstrap `preauthkeys create`) can
	# transiently fail with "user not found"; retry until the user shows up.
	for _i in $(seq 1 30); do
		headscale users create headscale >/dev/null 2>&1 || true
		headscale users list 2>/dev/null \
			| sed -E 's/\x1b\[[0-9;]*m//g' \
			| grep -q headscale && break
		sleep 1
	done

	# Verify the .env preauth keys exist in headscale's DB (fail-closed).
	# The pinned headscale (0.29.x): `preauthkeys list` takes no --user flag
	# and prints keys MASKED as a short prefix + *** when output is not a
	# TTY (verified 2026-08-18 — same masking behaviour as 0.28.x), so match
	# the configured key against the listed prefix with the *** stripped
	# (webvm_key_is_listed — the single home of the masked matching).
	if [ "$HEADSCALE_BOOTSTRAP" != "1" ]; then
		hs_user_id=$(headscale users list 2>/dev/null \
			| sed -E 's/\x1b\[[0-9;]*m//g' \
			| awk 'NR > 1 { print $1; exit }')
		listed_keys=$(headscale preauthkeys list 2>/dev/null \
			| sed -E 's/\x1b\[[0-9;]*m//g')
		check_key() {
			key_name=$1
			key_value=$2
			if ! webvm_key_is_listed "$key_value" "$listed_keys"; then
				echo "FATAL: $key_name is not present in the headscale DB." >&2
				if [ "$key_name" = "HEADSCALE_PREAUTHKEY" ]; then
					echo "       Bootstrap:  docker compose exec server headscale preauthkeys create --user $hs_user_id --reusable --ephemeral --expiration 100y" >&2
				else
					echo "       Bootstrap:  docker compose exec server headscale preauthkeys create --user $hs_user_id --reusable --expiration 100y" >&2
				fi
				echo "       then record the printed value in .env as $key_name and restart." >&2
				webvm_kill_supervised headscale
				exit 1
			fi
		}
		check_key HEADSCALE_PREAUTHKEY "$HEADSCALE_PREAUTHKEY"
		check_key GATEWAY_AUTHKEY "$GATEWAY_AUTHKEY"
	fi
fi

# --- Start wsgidav (webdav mode only) ---------------------------------------
WS_MARKER=""
if [ "$STORAGE_BACKEND" = "webdav" ]; then
	echo "==> Starting wsgidav"
	WS_MARKER=$(webvm_supervise_start wsgidav /var/log/wsgidav.log wsgidav --config /etc/webvm/wsgidav.yaml)
fi

# --- Start nginx ------------------------------------------------------------
nginx -t >/dev/null || {
	echo "FATAL: nginx config invalid" >&2
	[ -n "$HS_MARKER" ] && webvm_kill_supervised headscale
	[ -n "$WS_MARKER" ] && webvm_kill_supervised wsgidav
	exit 1
}
NGINX_MARKER=$(webvm_supervise_start nginx /dev/null nginx -g 'daemon off;')

echo "==> webvm server up (backend=$STORAGE_BACKEND site_port=$SITE_PORT control_port=$CONTROL_PORT)"

# --- Supervisor: a dead supervised service stops the container so compose's
# restart policy brings a healthy stack back up. Watches STATUS MARKERS
# (webvm_supervise_start), not pids: the exited services stay zombies under
# PID 1 and kill -0 would never see them (shared helper).
webvm_supervise "$HS_MARKER" "$NGINX_MARKER" "$WS_MARKER"
