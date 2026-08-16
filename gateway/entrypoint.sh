#!/bin/sh
# Gateway entrypoint: join the self-hosted Headscale control plane with
# tailscaled in userspace-networking mode, then run the socat TCP relays
# (bound to 127.0.0.1 — tailscaled forwards inbound tailnet-IP connections to
# loopback, and socat forwards them on to LAN targets).
set -eu

: "${GATEWAY_AUTHKEY:?GATEWAY_AUTHKEY is required — create it with 'headscale preauthkeys create --user <id> --reusable --expiration 100y' (v0.29.x takes the numeric user id, first user = 1) and record it in .env}"

STORAGE_BACKEND="${STORAGE_BACKEND:-browser}"
CONTROL_PORT="${CONTROL_PORT:-8443}"
CONTROL_WSS_PORT="${CONTROL_WSS_PORT:-443}"
# Server's static compose-network IP (webvm-net, fixed 172.28.0.0/16 — see
# compose.yaml). The ONLY address this container uses for the control plane:
# NO hostnames anywhere (host.docker.internal and /etc/hosts tricks are
# banned — browser-facing config must work with 127.0.0.1 / a LAN IP alone).
GATEWAY_CONTROL_IP="${GATEWAY_CONTROL_IP:-172.28.0.10}"
WEBDAV_PORT="${WEBDAV_PORT:-8082}"
GIT_HTTP_PORT="${GIT_HTTP_PORT:-8083}"

# --- tailscaled (userspace networking: no TUN device, no cap_add needed) ----
tailscaled \
	--tun=userspace-networking \
	--state=/var/lib/tailscale/tailscaled.state \
	--socket=/var/run/tailscale/tailscaled.sock \
	> /var/log/tailscaled.log 2>&1 &
TS_PID=$!

for _i in $(seq 1 60); do
	[ -S /var/run/tailscale/tailscaled.sock ] && break
	sleep 1
done
if [ ! -S /var/run/tailscale/tailscaled.sock ]; then
	echo "FATAL: tailscaled did not start" >&2
	exit 1
fi

# Join the control plane over the compose network: GATEWAY_CONTROL_IP is the
# server's static compose-network IP (172.28.0.10), which the cert SAN covers
# (IP:${SERVER_IP} in gen-certs.sh). It is the CONTAINER-side address and is
# INDEPENDENT of the browser-facing CONTROL_HOST (127.0.0.1 single machine /
# LAN IP), so no /etc/hosts entry or hostname is ever needed anywhere.
# server_url is PATH-LESS in v0.29.x — see the headscale config template.
# GATEWAY_AUTHKEY is reusable + long-lived so a recreated container can
# rejoin; the tailscaled state volume keeps the node key (and therefore the
# allocated tailnet IP) stable.
tailscale up \
	--login-server="https://${GATEWAY_CONTROL_IP}:${CONTROL_PORT}" \
	--authkey="$GATEWAY_AUTHKEY" \
	--hostname=gateway \
	--accept-routes=false \
	--accept-dns=false

echo "gateway tailnet IP: $(tailscale ip -4 2>/dev/null | head -1)"

# --- socat relays (127.0.0.1 only; the guest reaches ONLY these ports) ------
RELAY_PIDS=""
start_relay() {
	port=$1
	target=$2
	socat "TCP-LISTEN:${port},fork,reuseaddr,bind=127.0.0.1" "TCP:${target}" &
	RELAY_PIDS="${RELAY_PIDS} $!"
	echo "relay: 127.0.0.1:${port} -> ${target}"
}

case "$STORAGE_BACKEND" in
	samba)
		: "${SAMBA_LAN_IP:?samba mode requires SAMBA_LAN_IP}"
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
socat "TCP-LISTEN:443,fork,reuseaddr" "TCP:${GATEWAY_CONTROL_IP}:${CONTROL_WSS_PORT}" &
RELAY_PIDS="${RELAY_PIDS} $!"
echo "relay: :443 -> ${GATEWAY_CONTROL_IP}:${CONTROL_WSS_PORT} (control plane WSS)"

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
# gateway; the guest then adds remotes like ssh://git@<GATEWAY_TAILNET_IP>:2222/)
[ -n "${GIT_SSH_LAN_IP:-}" ] && start_relay 2222 "${GIT_SSH_LAN_IP}:22"
[ -n "${GIT_HTTP_LAN_IP:-}" ] && start_relay "${GIT_HTTP_PORT}" "${GIT_HTTP_LAN_IP}:${GIT_HTTP_PORT}"

# --- Supervisor: stop the container if a supervised process dies -------------
while :; do
	for pid in $TS_PID $RELAY_PIDS; do
		if ! kill -0 "$pid" 2>/dev/null; then
			echo "FATAL: gateway process $pid exited" >&2
			exit 1
		fi
	done
	sleep 3
done
