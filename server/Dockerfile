# Server container: nginx (site + control plane + embedded DERP) + headscale
# + wsgidav (webdav mode only).
#
# Base: python:3.14-alpine (current stable python for wsgidav; nginx installed
# from its repos — NOT nginx:1.27-alpine, whose Alpine 3.21 python3/libexpat
# pair breaks pip/pyexpat entirely). Uplifted from python:3.11-alpine on
# 2026-08-18 (plans/update-to-latest.md A5; wsgidav 4.3.5 + cheroot 11.1.2
# verified installing and importing on 3.14).
#
# Build context is the repo ROOT (compose: `context: . dockerfile:
# server/Dockerfile`) so this can COPY the built frontend (`webvm/build`) and
# the guest ext2 (`webvm/custom-disk-images`) — the served alpine.html and the
# image must be inside the server image.

# --- Pinned headscale (binary only; the official image is distroless) -------
# 0.28.0 was originally pinned because it was the newest headscale that
# accepts the CheerpX runtime's bundled wasm client (Tailscale v1.78, capver
# 109): MinSupportedCapabilityVersion=106 AND no /key capability-version gate
# AND /ts2021 registered for WebSocket GET (0.29.x rejects capver 109 —
# headscale #3391 / rejectUnsupported). Since 2026-08-15 the browser client
# is REBUILT from source (v1.102.2, capver 142 — scripts/
# rebuild-tailscale-wasm.sh, plans/networking-bug.md §16), which 0.29.x
# accepts (min supported v1.80.0). 0.29.2 additionally fixed the /ts2021
# WebSocket-GET rejection that matters to wasm clients, and 0.29.3 fixed
# lingering ephemeral nodes — so the pin moved to 0.29.3 (2026-08-18,
# plans/update-to-latest.md A2). Upgrade path from a 0.28.0 DB volume:
# headscale migrates the SQLite schema in place at first serve (verified in
# the Tier A integration run — the pre-existing headscale-data volume came
# up clean under 0.29.3); the volume is forward-compatible, rollback is
# `git revert` of the image + template.
FROM headscale/headscale:0.29.3 AS headscale

FROM python:3.14-alpine

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
COPY server/render-webvm-config.py /etc/webvm/render-webvm-config.py
COPY server/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8081 8443 8082 3478/udp

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
