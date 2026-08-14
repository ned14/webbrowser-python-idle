# WebVM personal Linux desktop — LAN-only, self-hosted

[![CI](https://github.com/ned14/webbrowser-python-idle/actions/workflows/ci.yml/badge.svg)](https://github.com/ned14/webbrowser-python-idle/actions/workflows/ci.yml)

A personal Linux desktop that runs entirely in the browser via
[WebVM/CheerpX](https://webvm.io): a minimal **i386 Alpine** guest with
**stdlib-only Python and IDLE** (`idle3.10`), an Xorg/i3 desktop, **LAN-only
networking**, and **configurable persistent storage** — browser IndexedDB by
default, or Samba / container WebDAV through a guest sync agent.

The authoritative design is
[`plans/webvm_implementation.md`](plans/webvm_implementation.md) (implementation
started; phasing per §7). The repo is a planning-turned-implementation repo —
the plan is fully specified and the code now lives alongside it.

## Try it live

The project website **is the VM**: the latest `main` build runs entirely in your
browser at
[**https://ned14.github.io/webbrowser-python-idle/alpine.html**](https://ned14.github.io/webbrowser-python-idle/alpine.html)
— test-drive it there before running it yourself. The first load streams the
guest image (~230 MB; later visits reuse the browser cache) and needs a
standards-compliant browser with SharedArrayBuffer — GitHub Pages cannot set the
COOP/COEP headers WebVM requires, so the site injects them via a service worker.

## Quick start (browser mode — no tailnet)

```sh
make certs          # private CA + server cert (trust certs/ca.crt once in the browser)
make build          # guest ext2 -> webvm/custom-disk-images, frontend, container images
make up             # https://127.0.0.1:8081/alpine.html
```

Open `https://127.0.0.1:8081/alpine.html` (the private CA must be trusted in
the browser — HTTPS is the only access mode; there is no plain-HTTP path). The
desktop boots to the **file explorer** open on `~/` (a keep-alive daemon
relaunches it whenever the last window closes, so the desktop never sits
empty); new files/folders are created from the toolbar, and `.py` files open in
**IDLE** via the *Open in IDLE* button (or double-click / Ctrl+O) — the
explorer yields the whole screen to IDLE and returns, listing refreshed, when
IDLE exits. Example scripts are baked read-only into `~/python-examples/`
(reference material to copy, not edit in place). Files in `~/` survive reloads
via the browser IndexedDB overlay. Use `make url` to print the session URL.

## Storage backends (`STORAGE_BACKEND`)

| Backend | Guest files live in | Extra services |
|---|---|---|
| `browser` (default) | browser IndexedDB overlay (per-origin, versioned to the image build) | none — nginx-only server |
| `none` | nothing (fresh overlay per session) | none |
| `samba` | your existing LAN Samba share (via the gateway relay + guest `pysmb` agent) | headscale + gateway |
| `webdav` | a wsgidav container on a Docker volume (PROPFIND/PUT/GET) | headscale + gateway |

Set it in `.env` (copy `.env.example`), or edit `compose.yaml`'s inline default.

## Tailnet modes (`samba` / `webdav`)

```sh
# 1. one-time key bootstrap (headscale is started in bootstrap mode, which
#    skips the fail-closed key check)
cat > .env <<EOF
STORAGE_BACKEND=webdav
WEBDAV_USER=webdav
WEBDAV_PASS=<a-real-password>
HEADSCALE_BOOTSTRAP=1
EOF
make build && docker compose up -d server

# 2. create the two reusable, long-lived preauth keys and record them in .env
docker compose exec server headscale users list          # note the user id (first user = 1)
docker compose exec server headscale preauthkeys create --user 1 --reusable --expiration 100y
docker compose exec server headscale preauthkeys create --user 1 --reusable --expiration 100y
#   -> copy BOTH printed values into .env as HEADSCALE_PREAUTHKEY and
#      GATEWAY_AUTHKEY, then set HEADSCALE_BOOTSTRAP=0

# 3. bring up the tailnet stack and read the gateway's assigned tailnet IP
make up-tailnet
docker compose exec server headscale nodes list     # record the gateway's IP as GATEWAY_TAILNET_IP

# 4. print the full session URL (carries credentials — treat it like a password)
make url
```

LAN/multi-device use: set `CONTROL_HOST=<LAN_IP>` and `LAN_IP=<LAN_IP>` in
`.env` (and install `certs/ca.crt` on each device). Single machine keeps
`CONTROL_HOST=host.docker.internal` (add `127.0.0.1 host.docker.internal` to
`/etc/hosts` on the browser machine).

> The control-plane URL is **path-less** (`https://${CONTROL_HOST}:${CONTROL_PORT}`):
> verified against headscale v0.29.3, the Noise register path carries the
> `server_url` path verbatim and headscale's noise router serves it at the root
> (a `/headscale` base path 404s registration). nginx proxies all of
> `CONTROL_PORT` to headscale; the embedded-DERP relay is
> `https://${CONTROL_HOST}:${CONTROL_PORT}/derp`.

## LAN-only by design

- No exit node anywhere, `derp.urls: []` (no public Tailscale DERP), MagicDNS
  off, `disable_check_updates: true`.
- The page and WASM client make **zero external requests** — the stock webvm
  external tags (plausible, Google Fonts, service worker, blog posts, Claude/AI
  tab) are removed, the **CheerpX runtime is self-hosted** (the pinned
  `@leaningtech/cheerpx` npm package normally CDN-loads its core from
  `cxrtnc.leaningtech.com`; the pinned 1.3.7 runtime lives in `webvm/cheerpx/`
  and is served same-origin — see `scripts/fetch-cheerpx-runtime.sh`), and the
  compiled-in Tailscale logtail fetch is blocked by a CSP `connect-src`
  (`'self'` + the control host only).
- Docker ports are published on `LAN_IP` only (loopback-safe default
  `127.0.0.1`) — never all interfaces.
- The guest reaches **only** relayed services through the gateway's tailnet IP
  (`127.0.0.1` socat relays inside the gateway: `445` samba, `<WEBDAV_PORT>`
  webdav, `2222` git SSH). Raw LAN IPs are unreachable from the guest.

## Repository layout

```
diskimage/    i386 Alpine guest (Dockerfile, X/i3 configs, sync agent, rootfs)
webvm/        webvm frontend @ pinned commit (e58fef0) + persistence wiring
server/       nginx + headscale + wsgidav container, entrypoint, templates
gateway/      tailscaled (userspace) + socat relays
compose.yaml  services `server`, `gateway` (profile tailnet), `test-unit`
scripts/      gen-certs.sh, print-url.sh, acceptance.sh
build.sh      guest image -> ext2 pipeline + content fingerprint
tests/        unit / rootfs / server / e2e (see tests/README.md)
.github/      CI workflow (guest matrix, frontend, server integration + E2E, lint)
```

Pinned versions: webvm commit `e58fef0c9a1c815617e57c6704eaaf7c79c3de1c`,
`@leaningtech/cheerpx` 1.3.7 (exact), `headscale/headscale:0.29.3`,
`tailscale/tailscale:v1.102.2` — see `webvm/WEBVM_COMMIT` and
`plans/webvm_implementation.md` §12/21.

## Tests

`make test-unit`, then per-`tests/README.md`: rootfs smoke per backend, server
integration against the booted stack, Playwright E2E (real VM boot in headless
Chromium), and `make acceptance` for the manual/LAN checklist.

## Notes

- **Personal use.** CheerpX is free for personal/exploration use; this project
  is not intended for organizational distribution. See
  [cheerpx.io/licensing](https://cheerpx.io/licensing).
- **No secrets in this repo.** Keys/credentials come from an optional `.env`
  (see `.env.example`); the entrypoints enforce them fail-closed per mode.
- **Served-image credentials (accepted tradeoff).** In `samba`/`webdav` builds
  the baked `/root/.syncrc`/`/home/user/.syncrc` fallback carries the real
  backend credentials, and the ext2 is served to any browser that can reach
  the site (`/custom-disk-images/`). Since ports publish to `LAN_IP` only,
  this is confined to the LAN; on an untrusted LAN, prefer the runtime
  `/opt/syncrc` injection (webdav) or keep the backend share on a private
  VLAN. The guest SSH keypair is generated at **first boot**, never baked.
- **WebDAV is plain HTTP on the LAN.** wsgidav uses Basic auth over `http://`
  (the guest reaches it through the gateway relay); any LAN device can probe
  `http://<LAN_IP>:<WEBDAV_PORT>/webdav/`. It exists for host-side testing and
  the guest relay path; do not expose it past a trusted LAN.
