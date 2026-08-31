# Gateway: tailscaled (userspace networking) + socat TCP relays in ONE
# container — socat must share the gateway's network namespace with tailscaled
# (its relays bind 127.0.0.1, where tailscaled delivers inbound tailnet-IP
# connections). No host installs, no TUN, no NET_ADMIN.
#
# Build context is the repo ROOT (compose: `context: . dockerfile:
# gateway/Dockerfile`) so the shared lib (scripts/lib/) can be COPY'd in.

FROM tailscale/tailscale:v1.102.2

RUN apk add --no-cache socat

COPY gateway/entrypoint.sh /usr/local/bin/gateway-entrypoint.sh
COPY scripts/lib/webvm-common.sh /etc/webvm/lib/webvm-common.sh
RUN chmod +x /usr/local/bin/gateway-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/gateway-entrypoint.sh"]
