#!/bin/sh
# Gateway entrypoint: join the self-hosted Headscale control plane with
# tailscaled in userspace-networking mode, then run the socat TCP relays
# (bound to 127.0.0.1 — tailscaled forwards inbound tailnet-IP connections to
# loopback, and socat forwards them on to LAN targets).
set -eu

# Shared defaults + helpers (scripts/lib/webvm-common.sh; COPY'd into the
# image at /etc/webvm/lib/).
WEBVM_COMMON="${WEBVM_COMMON:-/etc/webvm/lib/webvm-common.sh}"
if [ ! -f "$WEBVM_COMMON" ]; then
	echo "FATAL: shared lib not found at $WEBVM_COMMON" >&2
	exit 1
fi
# shellcheck disable=SC1090
. "$WEBVM_COMMON"

# GATEWAY_AUTHKEY is the gateway node's preauth key (reusable + long-lived so
# a recreated container can rejoin; the tailscaled state volume keeps the node
# key and therefore the allocated tailnet IP stable). The per-backend relay
# targets are validated HERE, before any service starts — a missing
# SAMBA_LAN_IP must fail closed immediately, not after tailscaled has joined.
webvm_require_secret GATEWAY_AUTHKEY "create it with 'headscale preauthkeys create --user <id> --reusable --expiration 100y' (v0.29.x takes the numeric user id, first user = 1) and record it in .env"
case "$STORAGE_BACKEND" in
	samba)
		webvm_require_secret SAMBA_LAN_IP "samba mode requires SAMBA_LAN_IP (the existing Samba server's LAN IP)"
		;;
esac

# GATEWAY_CONTROL_IP (default from the shared lib): the server's static
# compose-network IP (webvm-net, fixed 172.28.0.0/16 — see compose.yaml). The
# ONLY address this container uses for the control plane: NO hostnames
# anywhere (host.docker.internal and /etc/hosts tricks are banned — browser-
# facing config must work with 127.0.0.1 / a LAN IP alone).

# --- tailscaled (userspace networking: no TUN device, no cap_add needed) ----
# Supervised via a wrapper subshell (webvm_supervise_start): the wrapper
# records tailscaled's real pid and writes a status marker when it exits, so
# the supervisor below detects a crash even though the exited process stays
# a zombie (PID 1 never reaps — kill -0 would lie).
TS_MARKER=$(webvm_supervise_start tailscaled /var/log/tailscaled.log \
	tailscaled \
	--tun=userspace-networking \
	--state=/var/lib/tailscale/tailscaled.state \
	--socket=/var/run/tailscale/tailscaled.sock)

if ! webvm_wait_until 60 1 [ -S /var/run/tailscale/tailscaled.sock ]; then
	echo "FATAL: tailscaled did not start" >&2
	webvm_kill_supervised tailscaled
	exit 1
fi

# Join the control plane over the compose network: GATEWAY_CONTROL_IP is the
# server's static compose-network IP (172.28.0.10), which the cert SAN covers
# (IP:${GATEWAY_CONTROL_IP} in gen-certs.sh). It is the CONTAINER-side address
# and is INDEPENDENT of the browser-facing CONTROL_HOST (127.0.0.1 single
# machine / LAN IP), so no /etc/hosts entry or hostname is ever needed
# anywhere. server_url is PATH-LESS in v0.29.x — see the headscale config
# template. GATEWAY_AUTHKEY is reusable + long-lived so a recreated container
# can rejoin; the tailscaled state volume keeps the node key (and therefore
# the allocated tailnet IP) stable.
tailscale up \
	--login-server="https://${GATEWAY_CONTROL_IP}:${CONTROL_PORT}" \
	--authkey="$GATEWAY_AUTHKEY" \
	--hostname=gateway \
	--accept-routes=false \
	--accept-dns=false

echo "gateway tailnet IP: $(tailscale ip -4 2>/dev/null | head -1)"

# --- socat relays (127.0.0.1 only; the guest reaches ONLY these ports) ------
RELAY_MARKERS=""
start_relay() {
	port=$1
	target=$2
	bind="${3:-127.0.0.1}"
	# Each relay is supervised the same way as tailscaled (status markers,
	# not kill -0 — see webvm_supervise_start).
	_marker=$(webvm_supervise_start "relay-${port}" /dev/null socat \
		"TCP-LISTEN:${port},fork,reuseaddr,bind=${bind}" "TCP:${target}")
	RELAY_MARKERS="${RELAY_MARKERS} $_marker"
	echo "relay: ${bind}:${port} -> ${target}"
}

case "$STORAGE_BACKEND" in
	samba)
		# SAMBA_LAN_IP validated up front (before tailscaled starts).
		start_relay 445 "${SAMBA_LAN_IP}:445"
		;;
	webdav)
		# The WebDAV endpoint lives in the server container (compose network)
		start_relay "${WEBDAV_PORT}" "${GATEWAY_CONTROL_IP}:${WEBDAV_PORT}"
		;;
esac
# Control plane on the DEFAULT WSS port (443): the CheerpX wasm client drops
# the controlUrl port when building the /ts2021 Noise WebSocket URL
# (wss://<host>/ts2021). The host publishes 443 on THIS container (tailnet
# profile only — browser/none modes never bind the privileged port), and
# socat forwards it to the server's control listener over the compose
# network. Unlike the tailscaled relays it must listen on ALL interfaces
# (Docker's port publish forwards to the container's eth0, not its loopback).
# The LISTENER stays 443 (the compose publish maps host CONTROL_WSS_PORT ->
# container 443); the TARGET is the server's CONTROL_WSS_PORT listener.
start_relay 443 "${GATEWAY_CONTROL_IP}:${CONTROL_WSS_PORT}" "0.0.0.0"

# DERP-map loopback relay (CONTROL_PORT): the netmap's DERP region host is
# derived from headscale's server_url, which is the BROWSER-facing
# CONTROL_HOST — 127.0.0.1 on the zero-config single machine. Inside this
# container 127.0.0.1 is the gateway's OWN loopback, so the DERP relay would
# be unreachable (the guest data path dies: the sync agent's lease never
# lands). Bind a loopback relay on CONTROL_PORT forwarding to the server's
# static compose-network IP, so the gateway's tailscaled reaches
# https://127.0.0.1:${CONTROL_PORT}/derp through it. On LAN deployments the
# DERP host is the LAN IP and is reached directly through the host; this
# relay is then unused but harmless.
start_relay "${CONTROL_PORT}" "${GATEWAY_CONTROL_IP}:${CONTROL_PORT}"

# Git relays (host-side step: set the *_LAN_IP vars in .env and recreate the
# gateway; the guest then adds remotes like ssh://git@<GATEWAY_TAILNET_IP>:2222/).
# The SSH relay port comes from the shared lib (GIT_SSH_PORT).
[ -n "${GIT_SSH_LAN_IP:-}" ] && start_relay "${GIT_SSH_PORT}" "${GIT_SSH_LAN_IP}:22"
[ -n "${GIT_HTTP_LAN_IP:-}" ] && start_relay "${GIT_HTTP_PORT}" "${GIT_HTTP_LAN_IP}:${GIT_HTTP_PORT}"

# --- Relay health probe: socat's fork-mode relays DEGRADE after hours of
# short-lived connections (a relay can reach a half-dead state where it still
# accepts + forwards requests but drops the responses — observed 2026-08-30:
# the browser's control-plane connections through the :443 relay stalled
# while the server itself answered instantly). A half-dead relay still
# passes kill -0, so the marker supervisor never notices. Probe the two
# browser-facing relays END-TO-END through their own listeners; a probe that
# does not complete within its timeout means the relay is unusable — exit
# the container (compose restart policy re-creates it with fresh relays and
# tailscaled re-joins with the same node key/IP).
relay_probe() {
	_name=$1
	_url=$2
	# busybox wget: exit != 0 on timeout/error. 5s covers the relay + the
	# server's TLS handshake on a loaded host.
	if ! wget -q --no-check-certificate --timeout=5 -O /dev/null "$_url" 2>/dev/null; then
		echo "FATAL: relay $_name probe failed ($_url) — exiting for a fresh container" >&2
		exit 1
	fi
	echo "relay health: $_name ok"
}

# --- Supervisor: stop the container if a supervised service dies (shared
# helper; RELAY_MARKERS is a space-separated list of marker paths, split by
# word-splitting) — AND if a browser-facing relay fails its probe. The
# probe interval (10 min) is far below the observed degradation time (hours)
# while adding no meaningful load (one HEAD per relay).
# shellcheck disable=SC2086
while :; do
	webvm_supervise_once $TS_MARKER $RELAY_MARKERS || exit 1
	sleep 300
	relay_probe "control-443" "https://127.0.0.1:${CONTROL_WSS_PORT}/derp/probe"
	relay_probe "derp-${CONTROL_PORT}" "https://127.0.0.1:${CONTROL_PORT}/derp/probe"
	sleep 300
done
