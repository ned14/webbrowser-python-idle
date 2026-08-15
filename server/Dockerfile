# Server container: nginx (site + control plane + embedded DERP) + headscale
# + wsgidav (webdav mode only).
#
# Base: python:3.11-alpine (stable python for wsgidav; nginx installed from
# its repos — NOT nginx:1.27-alpine, whose Alpine 3.21 python3/libexpat pair
# breaks pip/pyexpat entirely).
#
# Build context is the repo ROOT (compose: `context: . dockerfile:
# server/Dockerfile`) so this can COPY the built frontend (`webvm/build`) and
# the guest ext2 (`webvm/custom-disk-images`) — the served alpine.html and the
# image must be inside the server image.

# --- Pinned headscale (binary only; the official image is distroless) -------
# 0.28.0: originally pinned because it was the newest headscale that accepts
# the CheerpX runtime's bundled wasm client (Tailscale v1.78, capver 109):
# MinSupportedCapabilityVersion=106 AND no /key capability-version gate AND
# /ts2021 registered for WebSocket GET (0.29.x rejects capver 109 — headscale
# #3391 / rejectUnsupported). Since 2026-08-15 the browser client is REBUILT
# from source (v1.102.2, capver 142 — scripts/rebuild-tailscale-wasm.sh,
# plans/networking-bug.md §16), so 0.29.x would accept it too; the pin stays
# at 0.28.0 as the known-good combination (unpinning is a one-line change).
FROM headscale/headscale:0.28.0 AS headscale

FROM python:3.11-alpine

ARG STORAGE_BACKEND=browser
ARG WEBDAV_ROOT=/data/webdav

RUN apk add --no-cache nginx apache2-utils gettext && \
	pip3 install --no-cache-dir --break-system-packages wsgidav==4.3.5 cheroot==11.1.2 && \
	ln -sf /usr/bin/pip3 /usr/bin/pip

COPY --from=headscale /ko-app/headscale /usr/local/bin/headscale

# Frontend build + guest ext2 (produced by make build / CI before this build)
COPY webvm/build/ /srv/webvm/build/
COPY webvm/custom-disk-images/ /srv/webvm/custom-disk-images/

# Templates + entrypoint
COPY server/nginx.conf.template /etc/webvm/nginx.conf.template
COPY server/headscale/config.yaml.template /etc/webvm/headscale/config.yaml.template
COPY server/wsgidav.yaml.template /etc/webvm/wsgidav.yaml.template
COPY server/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8081 8443 8082 3478/udp

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
