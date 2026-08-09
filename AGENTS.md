# AGENTS.md

## What this repo is

Implementation of a **WebVM/CheerpX personal Linux desktop in the browser**
(i386 Alpine + IDLE), with LAN-only networking and configurable storage.
The authoritative plan is `plans/webvm_implementation.md` (design complete,
reviewed, decisions closed; implementation started and largely validated
locally). Work-in-progress is code in `diskimage/`, `server/`, `gateway/`,
`webvm/`, `tests/`, plus `build.sh`, `compose.yaml`, `Makefile` and the CI
workflow.

## Read first

- `plans/webvm_implementation.md` — the authoritative implementation plan.
  Read it before any work. Current state: complete, six review rounds done,
  all §12 decisions closed, **implementation started and largely validated
  locally** (§12/21 carries the implementation-time verification results).
  Follow its phasing (Phase 1 `browser` mode end-to-end → Phase 2
  tailnet/gateway → Phase 3 git/sync).
- `plans/implementation_options.md` — option comparison that motivated the
  plan.
- `prompts/research.md` — the original research prompt.

## Non-negotiable design facts (don't contradict these)

- **HTTPS only.** The site is `https://127.0.0.1:8081` (single machine) or
  `https://<LAN_IP>:8081` (LAN), served with a private CA the user trusts once
  in the browser. No plain-HTTP path exists.
- `STORAGE_BACKEND=browser|samba|webdav|none`, default `browser`. `browser`/
  `none` run an nginx-only server — no Headscale, no gateway.
- Guest networking exists **only** via CheerpX's Tailscale `networkInterface`
  (`authKey`/`controlUrl`). There is no custom WebSocket proxy API (verified
  against CheerpX 1.2.8 docs).
- **Never use/ship `#authKey` without a matching `controlUrl`** — WebVM then
  auto-registers with public Tailscale.
- Secrets (`HEADSCALE_PREAUTHKEY`, `GATEWAY_AUTHKEY`, `WEBDAV_USER/PASS`,
  Samba creds) are **optional at compose level** (`${VAR:-}`, never
  `${VAR:?err}`) and enforced fail-closed **per mode by the entrypoint**.
  Never commit secrets to this repo.
- Gateway reaches the control plane via `extra_hosts`
  `host.docker.internal:<server-static-ip>` (172.28.0.10 on a fixed
  `172.28.0.0/16` network) — not `host-gateway`/loopback-published ports.
- Tailnet modes are brought up with `make up-tailnet`, not
  `make up --profile tailnet`.
- `diskImageType="bytes"` (HttpBytesDevice), same-origin ext2 with nginx
  byte-range serving. `CloudDevice`/`GitHubDevice` are reference-WebVM
  variants, not used here.

## Version-dependent claims

Several claims (headscale config keys, DERP relay URL derivation, CORS, IDB vs
OPFS, logtail endpoint, SvelteKit output path, `DataDevice.writeFile`,
pysmb-on-3.10) depend on versions pinned **at implementation time**. See
**§12/21 verification checklist** in the plan — verify each item when pinning,
don't assume.

## Commands

Implemented: `make certs/build/up/up-tailnet/down/logs/test/test-unit/acceptance/
url` (Makefile), `build.sh` (guest image → ext2 + content fingerprint),
`scripts/gen-certs.sh`, `scripts/fetch-cheerpx-runtime.sh`, `scripts/
acceptance.sh`. Test suite lives in `tests/` (see `tests/README.md`): unit
(pytest via the `test-unit` compose service), rootfs smoke, server integration
(`tests/server/integration.sh`), Playwright E2E (`tests/e2e`). Check the plan
and `tests/README.md` before inventing new build or test commands.

## Working here

- Plan-first: prefer editing `plans/webvm_implementation.md` over writing code
  before the phases begin.
- Keep design decisions recorded in §3/§12 of the plan, and keep the §12/21
  checklist current as versions get pinned.
