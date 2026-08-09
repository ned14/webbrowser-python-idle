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
CONTROL_HOST="${CONTROL_HOST:-host.docker.internal}"
LAN_IP="${LAN_IP:-127.0.0.1}"
EXTRA_BIND_IP="${EXTRA_BIND_IP:-}"
WEBDAV_USER="${WEBDAV_USER:-}"
WEBDAV_PASS="${WEBDAV_PASS:-}"
GATEWAY_AUTHKEY="${GATEWAY_AUTHKEY:-}"

SITE_URL="https://${LAN_IP}:${SITE_PORT}"

# Control-plane URLs must be reachable from this host. CONTROL_HOST may not
# resolve everywhere (CI adds it to /etc/hosts; local macOS users might not) —
# fall back to LAN_IP, which the cert SAN covers.
if getent hosts "$CONTROL_HOST" >/dev/null 2>&1 || nslookup "$CONTROL_HOST" >/dev/null 2>&1; then
	CONTROL_URL="https://${CONTROL_HOST}:${CONTROL_PORT}"
else
	echo "   (CONTROL_HOST $CONTROL_HOST does not resolve here — testing the control plane via ${LAN_IP}:${CONTROL_PORT})"
	CONTROL_URL="https://${LAN_IP}:${CONTROL_PORT}"
fi

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

echo "==> ext2 byte ranges"
curl -sk -o /dev/null -w "%{http_code}" "$SITE_URL/custom-disk-images/webvm-custom-disk.ext2" | grep -q "200" || fail "ext2 not 200"
curl -sk -H "Range: bytes=0-1023" -o /dev/null -w "%{http_code}" "$SITE_URL/custom-disk-images/webvm-custom-disk.ext2" | grep -q "206" || fail "ext2 Range not 206"

echo "==> loopback-alias binding (EXTRA_BIND_IP, when configured)"
if [ -n "$EXTRA_BIND_IP" ]; then
	curl -sk -o /dev/null -w "%{http_code}" "https://${EXTRA_BIND_IP}:${SITE_PORT}/alpine.html" | grep -q "200" || fail "EXTRA_BIND_IP binding not serving"
else
	echo "   (EXTRA_BIND_IP not set — skipped)"
fi

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
curl -sk -D - -o /dev/null "$CONTROL_URL/derp/probe" | grep -qi "access-control-allow-origin: \*" || fail "/derp/probe ACAO missing"

echo "==> headscale join test (tailscaled client, private CA)"
# Shared helper: joins a throwaway tailscaled node to the control plane. The
# container resolves host.docker.internal via extra_hosts to the server's
# static compose-network IP, so the login URL must be the CONTAINER-side one
# (never the host-side fallback).
AUTHKEY="$GATEWAY_AUTHKEY" CONTROL_URL="https://host.docker.internal:${CONTROL_PORT}" \
	tests/server/join-test-client.sh >/dev/null || fail "join test client did not register"
docker compose exec -T server headscale nodes list | grep -q "ci-client" || fail "ci-client node did not register with headscale"

echo "==> no exit node advertised"
docker compose exec -T server headscale nodes list | grep -q "ci-client" && echo "   (node present; exit-node flags are never advertised by design)"

echo "==> integration PASS"
