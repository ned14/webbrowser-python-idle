# AGENTS.md

## What this repo is

Planning repo for building a **WebVM/CheerpX personal Linux desktop in the
browser** (i386 Alpine + IDLE), with LAN-only networking and configurable
storage. There is **no implementation code yet** — all work is in `plans/`.

## Read first

- `plans/webvm_implementation.md` — the authoritative implementation plan.
  Read it before any work. Current state: complete, four review rounds done,
  all §12 decisions closed, implementation not started. Follow its phasing
  (Phase 1 `browser` mode end-to-end → Phase 2 tailnet/gateway → Phase 3
  git/sync).
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

No scripts or Makefile exist yet (the Makefile is part of the plan: `make
certs/build/up/up-tailnet/down/logs/test/acceptance/url`). Don't invent build
or test commands; check the plan first. There is no test suite yet.

## Working here

- Plan-first: prefer editing `plans/webvm_implementation.md` over writing code
  before the phases begin.
- Keep design decisions recorded in §3/§12 of the plan, and keep the §12/21
  checklist current as versions get pinned.
