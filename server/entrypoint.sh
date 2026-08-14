#!/bin/sh
# Server entrypoint: fail-closed per-mode secret checks, config rendering
# (envsubst with an EXPLICIT variable list — never bare envsubst, which would
# mangle $ in credentials), then supervision of headscale + nginx (+ wsgidav
# in webdav mode).
set -u

STORAGE_BACKEND="${STORAGE_BACKEND:-browser}"
HEADSCALE_ENABLED="${HEADSCALE_ENABLED:-0}"
HEADSCALE_BOOTSTRAP="${HEADSCALE_BOOTSTRAP:-0}"
CONTROL_HOST="${CONTROL_HOST:-host.docker.internal}"
LAN_IP="${LAN_IP:-127.0.0.1}"
SITE_PORT="${SITE_PORT:-8081}"
CONTROL_PORT="${CONTROL_PORT:-8443}"
WEBDAV_PORT="${WEBDAV_PORT:-8082}"
STUN_PORT="${STUN_PORT:-3478}"
WEBDAV_ROOT="${WEBDAV_ROOT:-/data/webdav}"

# --- Fail-closed per-mode secret checks ------------------------------------
need_headscale=0
case "$STORAGE_BACKEND" in
	samba|webdav) need_headscale=1 ;;
esac
[ "$HEADSCALE_ENABLED" = "1" ] && need_headscale=1

if [ "$need_headscale" = "1" ] && [ "$HEADSCALE_BOOTSTRAP" != "1" ]; then
	if [ -z "${HEADSCALE_PREAUTHKEY:-}" ]; then
		echo "FATAL: STORAGE_BACKEND=$STORAGE_BACKEND requires HEADSCALE_PREAUTHKEY." >&2
		echo "       Bootstrap:  HEADSCALE_BOOTSTRAP=1 docker compose up -d server" >&2
		echo "       then:        docker compose exec server headscale preauthkeys create --user <id> --reusable --expiration 100y" >&2
		echo "       (the user id comes from 'headscale users list'; the first user is 1)" >&2
		echo "       and record the printed value in .env (see .env.example)." >&2
		exit 1
	fi
	if [ -z "${GATEWAY_AUTHKEY:-}" ]; then
		echo "FATAL: STORAGE_BACKEND=$STORAGE_BACKEND requires GATEWAY_AUTHKEY." >&2
		echo "       Create it as above and record it in .env (see .env.example)." >&2
		exit 1
	fi
fi

if [ "$STORAGE_BACKEND" = "webdav" ]; then
	if [ -z "${WEBDAV_USER:-}" ] || [ -z "${WEBDAV_PASS:-}" ]; then
		echo "FATAL: STORAGE_BACKEND=webdav requires WEBDAV_USER and WEBDAV_PASS." >&2
		exit 1
	fi
fi

# --- Render configuration templates -----------------------------------------
mkdir -p /etc/nginx /etc/headscale /etc/webvm /var/lib/headscale /var/run/headscale

envsubst '$CONTROL_HOST $CONTROL_PORT $SITE_PORT' \
	< /etc/webvm/nginx.conf.template > /etc/nginx/nginx.conf
envsubst '$CONTROL_HOST $CONTROL_PORT $STUN_PORT' \
	< /etc/webvm/headscale/config.yaml.template > /etc/headscale/config.yaml

if [ "$STORAGE_BACKEND" = "webdav" ]; then
	# Basic-auth file for the WebDAV endpoint (never a random password — the
	# sync agent's credentials must match).
	htpasswd -bc /etc/webvm/webdav.htpasswd "$WEBDAV_USER" "$WEBDAV_PASS"
	envsubst '$WEBDAV_PORT $WEBDAV_ROOT $WEBDAV_USER $WEBDAV_PASS' \
		< /etc/webvm/wsgidav.yaml.template > /etc/webvm/wsgidav.yaml
fi

# --- Start headscale (only when needed) -------------------------------------
HS_PID=""
if [ "$need_headscale" = "1" ]; then
	echo "==> Starting headscale"
	headscale serve > /var/log/headscale.log 2>&1 &
	HS_PID=$!

	# The CLI talks to the running server over the unix socket
	for _i in $(seq 1 30); do
		[ -S /var/run/headscale/headscale.sock ] && break
		sleep 1
	done

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
	# NB v0.29.x: `preauthkeys list` takes no --user flag and MASKS keys with
	# ***, so match the configured key against the listed unmasked prefix.
	if [ "$HEADSCALE_BOOTSTRAP" != "1" ]; then
		hs_user_id=$(headscale users list 2>/dev/null \
			| sed -E 's/\x1b\[[0-9;]*m//g' \
			| awk 'NR > 1 { print $1; exit }')
		listed_keys=$(headscale preauthkeys list 2>/dev/null \
			| sed -E 's/\x1b\[[0-9;]*m//g')
		check_key() {
			key_name=$1
			key_value=$2
			listed_prefix=$(printf '%s\n' "$listed_keys" \
				| grep -oE 'hskey-auth-[A-Za-z0-9_-]+\**' \
				| sed 's/\*\+$//' \
				| sort -u)
			matched=""
			for prefix in $listed_prefix; do
				case "$key_value" in
					"$prefix"*) matched=1 ;;
				esac
			done
			if [ -z "$matched" ]; then
				echo "FATAL: $key_name is not present in the headscale DB." >&2
				echo "       Bootstrap:  docker compose exec server headscale preauthkeys create --user $hs_user_id --reusable --expiration 100y" >&2
				echo "       then record the printed value in .env as $key_name and restart." >&2
				kill "$HS_PID" 2>/dev/null
				exit 1
			fi
		}
		check_key HEADSCALE_PREAUTHKEY "$HEADSCALE_PREAUTHKEY"
		check_key GATEWAY_AUTHKEY "$GATEWAY_AUTHKEY"
	fi
fi

# --- Start wsgidav (webdav mode only) ---------------------------------------
WS_PID=""
if [ "$STORAGE_BACKEND" = "webdav" ]; then
	echo "==> Starting wsgidav"
	wsgidav --config /etc/webvm/wsgidav.yaml > /var/log/wsgidav.log 2>&1 &
	WS_PID=$!
fi

# --- Start nginx ------------------------------------------------------------
nginx -t >/dev/null || { echo "FATAL: nginx config invalid" >&2; [ -n "$HS_PID" ] && kill "$HS_PID" 2>/dev/null; exit 1; }
nginx -g 'daemon off;' &
NGINX_PID=$!

echo "==> webvm server up (backend=$STORAGE_BACKEND site_port=$SITE_PORT control_port=$CONTROL_PORT)"

# --- Supervisor: a dead supervised process stops the container so compose's
# restart policy brings a healthy stack back up.
while :; do
	for pid in $HS_PID $NGINX_PID $WS_PID; do
		if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
			echo "FATAL: supervised process $pid exited; stopping container" >&2
			exit 1
		fi
	done
	sleep 3
done
