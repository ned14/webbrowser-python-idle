#!/bin/sh
# Install the current Let's Encrypt lineage into certs/server.{crt,key} — the
# files the server container mounts read-only and nginx serves — then reload
# nginx when the stack is running and the files actually changed.
#
# Used from TWO contexts (cwd- and env-independent, so it re-loads .env and
# resolves the deployment root from its own path):
#   (a) certbot's --deploy-hook (recorded at issue time by gen-certs.sh's
#       LETSENCRYPT branch; re-run by certbot's own renewal cadence — the
#       systemd certbot.timer runs with an arbitrary cwd and a bare env), and
#   (b) the always-run step of gen-certs.sh (a fresh checkout has no certs/
#       yet while certbot's lineage already exists; the deploy hook only fires
#       on an actual issue/renewal, so without this copy the cert would not
#       reach certs/ until the next real renewal).
#
# The nginx reload matters because the certs mount is read-only and nginx
# reads the files only at (re)load: replacing server.crt/server.key on the
# host while a container runs leaves the OLD cert served until a reload or
# restart. Reload is attempted only when a server container is actually
# running (at `make up` time none exists yet — the imminent start reads the
# fresh files); a FAILED reload exits 1 so the caller (certbot log / make)
# surfaces that the running stack still serves the previous cert.
#
# Test/ops knob: LETSENCRYPT_NO_RELOAD=1 skips the docker reload leg (the
# unit tests stub certbot with non-cert files and must never touch a real
# running stack).
set -eu

_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$_SCRIPT_DIR/.."

WEBVM_COMMON="${WEBVM_COMMON:-$_SCRIPT_DIR/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"
webvm_load_dotenv

CERT_DIR="${CERT_DIR:-./certs}"
_live_dir="$LETSENCRYPT_ROOT/live/$LETSENCRYPT_CERT_NAME"
_live_crt="$_live_dir/fullchain.pem"
_live_key="$_live_dir/privkey.pem"
if [ ! -f "$_live_crt" ] || [ ! -f "$_live_key" ]; then
	echo "FATAL: Let's Encrypt lineage not found at $_live_dir (fullchain.pem + privkey.pem) — run gen-certs.sh (make certs) with LETSENCRYPT_EMAIL set first" >&2
	exit 1
fi

mkdir -p "$CERT_DIR"
_changed=0
if [ ! -f "$CERT_DIR/server.crt" ] || ! cmp -s "$_live_crt" "$CERT_DIR/server.crt"; then
	cp "$_live_crt" "$CERT_DIR/server.crt"
	chmod 644 "$CERT_DIR/server.crt"
	_changed=1
fi
if [ ! -f "$CERT_DIR/server.key" ] || ! cmp -s "$_live_key" "$CERT_DIR/server.key"; then
	cp "$_live_key" "$CERT_DIR/server.key"
	chmod 600 "$CERT_DIR/server.key"
	_changed=1
fi

if [ "$_changed" = "1" ]; then
	echo "==> Installed the Let's Encrypt cert ($LETSENCRYPT_CERT_NAME) as $CERT_DIR/server.{crt,key}"
	if [ "${LETSENCRYPT_NO_RELOAD:-}" != "1" ] && command -v docker >/dev/null 2>&1; then
		_svc=$(docker compose ps -q server 2>/dev/null || true)
		if [ -n "$_svc" ]; then
			echo "==> Reloading nginx (server container $_svc) so it serves the new cert"
			if ! docker compose exec -T server nginx -s reload; then
				echo "FATAL: nginx reload failed — the running stack still serves the previous cert (try: docker compose restart server)" >&2
				exit 1
			fi
		else
			echo "==> No running server container — the fresh cert is picked up at the next container start"
		fi
	fi
else
	echo "==> $CERT_DIR/server.{crt,key} already match the current Let's Encrypt cert — nothing to do"
fi
