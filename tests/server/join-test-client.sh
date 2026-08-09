#!/bin/sh
# Join the headscale control plane from a throwaway tailscaled client and
# report connectivity. Used by tests/server/integration.sh (and CI) to prove
# the control plane + private-CA TLS work without a browser.
#
# Usage (env):
#   CONTROL_URL  e.g. https://host.docker.internal:8443   (path-less server_url)
#   AUTHKEY      a reusable preauth key
#   NETWORK      docker network (default: webvm-custom_webvm-net)
#   EXTRA_HOSTS  optional --add-host (default: host.docker.internal:172.28.0.10)
#   CERTS_DIR    dir with ca.crt (default: $PWD/certs)
#   NODE_NAME    node hostname (default: ci-client)
set -eu

CONTROL_URL="${CONTROL_URL:-https://host.docker.internal:8443}"
AUTHKEY="${AUTHKEY:?AUTHKEY is required}"
NETWORK="${NETWORK:-webvm-custom_webvm-net}"
EXTRA_HOSTS="${EXTRA_HOSTS:-host.docker.internal:172.28.0.10}"
CERTS_DIR="${CERTS_DIR:-$PWD/certs}"
NODE_NAME="${NODE_NAME:-ci-client}"

CID=$(docker run -d \
	--network "$NETWORK" \
	--add-host "$EXTRA_HOSTS" \
	-e SSL_CERT_FILE=/certs/ca.crt \
	-v "$CERTS_DIR:/certs:ro" \
	--entrypoint /bin/sh \
	tailscale/tailscale:v1.102.2 -c \
	"tailscaled --tun=userspace-networking --state=/tmp/state --socket=/tmp/ts.sock >/tmp/tsd.log 2>&1 &
	sleep 3
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
			echo "$status" | head -4
			exit 0
			;;
	esac
	sleep 2
done

docker exec "$CID" sh -c 'tail -5 /tmp/tsd.log /tmp/up.log 2>/dev/null' || true
echo "FAIL: $NODE_NAME did not join the tailnet" >&2
exit 1
