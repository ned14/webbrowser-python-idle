#!/bin/sh
# Join the headscale control plane from a throwaway tailscaled client and
# report connectivity. Used by tests/server/integration.sh (and CI) to prove
# the control plane + private-CA TLS work without a browser.
#
# Usage (env):
#   CONTROL_URL  e.g. https://172.28.0.10:8443  (path-less server_url; the
#                server's static compose-network IP — hostnames are banned)
#   AUTHKEY      a reusable preauth key
#   NETWORK      docker network (default: webvm-custom_webvm-net)
#   CERTS_DIR    dir with ca.crt (default: $PWD/certs)
#   NODE_NAME    node hostname (default: ci-client)
set -eu

# Shared defaults (GATEWAY_CONTROL_IP/CONTROL_PORT) + helpers.
WEBVM_COMMON="${WEBVM_COMMON:-$(dirname "$0")/../../scripts/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"

CONTROL_URL="${CONTROL_URL:-https://${GATEWAY_CONTROL_IP}:${CONTROL_PORT}}"
AUTHKEY="${AUTHKEY:?AUTHKEY is required}"
# The compose project name (compose.yaml `name: webvm-custom`) + network:
# derived from the live compose stack rather than a hand-written literal, so
# a project/network rename cannot silently strand the join test.
NETWORK="${NETWORK:-$(docker compose --profile tailnet ps -q >/dev/null 2>&1 && docker network ls --format '{{.Name}}' | grep 'webvm-custom_webvm-net' | head -1)}"
if [ -z "$NETWORK" ]; then
	echo "FATAL: cannot find the webvm-custom_webvm-net docker network (is the stack up?)" >&2
	exit 1
fi
CERTS_DIR="${CERTS_DIR:-$PWD/certs}"
NODE_NAME="${NODE_NAME:-ci-client}"

CID=$(docker run -d \
	--network "$NETWORK" \
	-e SSL_CERT_FILE=/certs/ca.crt \
	-v "$CERTS_DIR:/certs:ro" \
	--entrypoint /bin/sh \
	tailscale/tailscale:v1.102.2 -c \
	"tailscaled --tun=userspace-networking --state=/tmp/state --socket=/tmp/ts.sock >/tmp/tsd.log 2>&1 &
	# Never race the daemon: wait for the socket (the same poll the gateway
	# entrypoint uses) instead of a fixed sleep — on a slow CI the tailscale
	# 'up' would fail and the node would never register.
	i=0
	while [ ! -S /tmp/ts.sock ] && [ \$i -lt 30 ]; do sleep 1; i=\$((i + 1)); done
	[ -S /tmp/ts.sock ] || { echo 'tailscaled socket never appeared' >&2; exit 1; }
	tailscale --socket=/tmp/ts.sock up --login-server '$CONTROL_URL' \
		--authkey '$AUTHKEY' --hostname '$NODE_NAME' --accept-dns=false >/tmp/up.log 2>&1
	# keep the container alive while the host polls
	tail -f /dev/null")

# shellcheck disable=SC2329  # cleanup is invoked via the EXIT trap below
cleanup() {
	docker rm -f "$CID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Wait for the node to be registered on the control plane
for _i in $(seq 1 30); do
	status=$(docker exec "$CID" tailscale --socket=/tmp/ts.sock status 2>/dev/null || true)
	case "$status" in
		*"$NODE_NAME"*)
			# 2>/dev/null: with many tailnet nodes the status list outlives
			# head -4, and a SIGPIPE'd echo must not print a write error.
			echo "$status" 2>/dev/null | head -4
			exit 0
			;;
	esac
	sleep 2
done

docker exec "$CID" sh -c 'tail -5 /tmp/tsd.log /tmp/up.log 2>/dev/null' || true
echo "FAIL: $NODE_NAME did not join the tailnet" >&2
exit 1
