#!/bin/sh
# Server integration tests (run against a booted stack — `make up` or
# `make up-tailnet`). Requires: curl, docker compose.
#
#   STORAGE_BACKEND=webdav    exercises the WebDAV + control-plane paths
#   (or HEADSCALE_ENABLED=1)  enables the headscale join test without webdav
set -eu

SITE_PORT="${SITE_PORT:-8081}"
CONTROL_PORT="${CONTROL_PORT:-8443}"
WEBDAV_PORT="${WEBDAV_PORT:-8082}"
# Browser-facing control host: 127.0.0.1 single machine / LAN IP. Hostnames
# are banned (host.docker.internal etc. — never reintroduce).
CONTROL_HOST="${CONTROL_HOST:-127.0.0.1}"
# The server's static compose-network IP: the CONTAINER-side address for the
# join-test client (which runs on webvm-net, not on the host loopback).
GATEWAY_CONTROL_IP="${GATEWAY_CONTROL_IP:-172.28.0.10}"
LAN_IP="${LAN_IP:-127.0.0.1}"
WEBDAV_USER="${WEBDAV_USER:-}"
WEBDAV_PASS="${WEBDAV_PASS:-}"
GATEWAY_AUTHKEY="${GATEWAY_AUTHKEY:-}"

SITE_URL="https://${LAN_IP}:${SITE_PORT}"

# The control plane is reachable from this host at CONTROL_HOST:CONTROL_PORT
# (published on LAN_IP only — 127.0.0.1 single machine, LAN IP on a LAN).
CONTROL_URL="https://${CONTROL_HOST}:${CONTROL_PORT}"

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

echo "==> compose file validates"
docker compose config -q || fail "docker compose config -q"

echo "==> site headers (HTTPS)"
curl -sk -D - -o /dev/null "$SITE_URL/alpine.html" | tee /tmp/hdr.txt | grep -qi "^HTTP/.* 200" || fail "/alpine.html not 200"
grep -qi "cross-origin-opener-policy: same-origin" /tmp/hdr.txt || fail "COOP header missing"
grep -qi "cross-origin-embedder-policy: require-corp" /tmp/hdr.txt || fail "COEP header missing"
grep -qi "content-security-policy:" /tmp/hdr.txt || fail "CSP header missing"
grep -qi "connect-src 'self' https://$CONTROL_HOST:$CONTROL_PORT wss://$CONTROL_HOST:$CONTROL_PORT" /tmp/hdr.txt || fail "CSP connect-src missing/wrong"

echo "==> site redirects"
curl -sk -o /dev/null -w "%{http_code}" "$SITE_URL/" | grep -q "302" || fail "/ should 302 -> /alpine.html"
curl -sk -o /dev/null -w "%{http_code}" "$SITE_URL/alpine" | grep -q "301" || fail "/alpine should 301 -> /alpine.html"

echo "==> baked page config (webvm-config.js)"
curl -sk -D /tmp/hdr-cfg.txt -o /tmp/webvm-config.js "$SITE_URL/webvm-config.js"
grep -qi "^HTTP/.* 200" /tmp/hdr-cfg.txt || fail "/webvm-config.js not 200"
grep -qi "cache-control: no-store" /tmp/hdr-cfg.txt || fail "/webvm-config.js must be no-store"
CFG=$(cat /tmp/webvm-config.js)
echo "$CFG" | grep -q "window.__webvmConfig" || fail "/webvm-config.js not rendered"
if [ "${STORAGE_BACKEND:-browser}" = "webdav" ] || [ "${HEADSCALE_ENABLED:-0}" = "1" ]; then
	echo "$CFG" | grep -q '"authKey"' || fail "baked config missing authKey"
	echo "$CFG" | grep -q '"controlUrl"' || fail "baked config missing controlUrl"
	if [ "${STORAGE_BACKEND:-browser}" = "webdav" ]; then
		echo "$CFG" | grep -q '"syncUrl"' || fail "baked config missing syncUrl"
	fi
else
	echo "$CFG" | grep -q 'window.__webvmConfig = {}' || fail "browser/none mode must serve an empty baked config"
fi

echo "==> ext2 byte ranges"
curl -sk -o /dev/null -w "%{http_code}" "$SITE_URL/custom-disk-images/webvm-custom-disk.ext2" | grep -q "200" || fail "ext2 not 200"
curl -sk -H "Range: bytes=0-1023" -o /dev/null -w "%{http_code}" "$SITE_URL/custom-disk-images/webvm-custom-disk.ext2" | grep -q "206" || fail "ext2 Range not 206"

if [ "${STORAGE_BACKEND:-browser}" = "webdav" ]; then
	echo "==> webdav PROPFIND/PUT/GET round-trip"
	[ -n "$WEBDAV_USER" ] && [ -n "$WEBDAV_PASS" ] || fail "WEBDAV_USER/WEBDAV_PASS not set"
	curl -s -u "$WEBDAV_USER:$WEBDAV_PASS" -X PUT --data-binary "integration test" \
		"http://${LAN_IP}:${WEBDAV_PORT}/webdav/integration.txt" -o /dev/null || fail "webdav PUT failed"
	got=$(curl -s -u "$WEBDAV_USER:$WEBDAV_PASS" "http://${LAN_IP}:${WEBDAV_PORT}/webdav/integration.txt")
	[ "$got" = "integration test" ] || fail "webdav GET mismatch"
	curl -s -u "$WEBDAV_USER:$WEBDAV_PASS" -X PROPFIND -H "Depth: 1" \
		"http://${LAN_IP}:${WEBDAV_PORT}/webdav/" | grep -q "integration.txt" || fail "webdav PROPFIND missing file"
	curl -s -u "$WEBDAV_USER:$WEBDAV_PASS" -X DELETE \
		"http://${LAN_IP}:${WEBDAV_PORT}/webdav/integration.txt" -o /dev/null || true
fi

echo "==> control listener + DERP probe"
[ -n "$GATEWAY_AUTHKEY" ] || fail "GATEWAY_AUTHKEY not set (needed for the join test)"
# CORS: nginx hides headscale's own ACAO (*) and echoes the request Origin
# only when present (a second/empty ACAO value breaks the browser's CORS
# check — MultipleAllowOriginValues — see plans/networking-bug.md §15.2).
# So the probe WITHOUT an Origin must NOT carry ACAO, and WITH an Origin it
# must echo exactly that origin (never "*").
if curl -sk -D - -o /dev/null "$CONTROL_URL/derp/probe" | grep -qi "access-control-allow-origin"; then
	fail "/derp/probe without Origin must not carry Access-Control-Allow-Origin"
fi
PROBE_ACAO=$(curl -sk -D - -o /dev/null -H "Origin: https://example.test" "$CONTROL_URL/derp/probe" \
	| tr -d '\r' | awk -F': ' 'tolower($1)=="access-control-allow-origin" {print $2}')
[ "$PROBE_ACAO" = "https://example.test" ] || fail "/derp/probe with Origin must echo it (got '$PROBE_ACAO')"

echo "==> headscale join test (tailscaled client, private CA)"
# Shared helper: joins a throwaway tailscaled node to the control plane. The
# client container runs on the compose network, so the login URL is the
# CONTAINER-side one: the server's static compose-network IP
# (GATEWAY_CONTROL_IP, cert SAN covers IP:172.28.0.10) — never a hostname.
AUTHKEY="$GATEWAY_AUTHKEY" CONTROL_URL="https://${GATEWAY_CONTROL_IP}:${CONTROL_PORT}" \
	tests/server/join-test-client.sh >/dev/null || fail "join test client did not register"
docker compose exec -T server headscale nodes list | grep -q "ci-client" || fail "ci-client node did not register with headscale"

echo "==> no exit node advertised"
# Assert the NEGATIVE: no node on this headnet advertises a default route
# (an exit node or 0.0.0.0/0 route). The gateway itself never uses
# --advertise-routes and never joins as an exit node, so list-routes must be
# empty of default routes for every node.
docker compose exec -T server headscale nodes list | grep -q "ci-client" \
	|| fail "ci-client node did not register with headscale"
if docker compose exec -T server headscale nodes list-routes 2>/dev/null | grep -E '0\.0\.0\.0/0|::/0'; then
	fail "an exit-node default route is advertised on the headnet"
fi
echo "   (no 0.0.0.0/0 or ::/0 routes on any node — no exit node)"

echo "==> integration PASS"
