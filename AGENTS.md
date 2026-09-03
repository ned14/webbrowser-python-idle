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
- **HOSTNAMES ARE BANNED — absolute rule, never reintroduce them.** No
  `host.docker.internal`, no `/etc/hosts` entries, no custom DNS of any kind
  for LAN users. Everything must work with `127.0.0.1` (zero-config single
  machine) and a hardcoded LAN address such as `192.168.x.x` (LAN) alone:
  - `CONTROL_HOST` is the BROWSER-facing control-host (default `127.0.0.1`,
    LAN deployments set it to the LAN IP). It renders into `server_url`, the
    baked `controlUrl`, the nginx CSP and the URL hash.
  - The gateway container never uses `CONTROL_HOST`: it reaches the control
    plane over the compose network at the server's static IP
    (`GATEWAY_CONTROL_IP`, default `172.28.0.10` on the fixed
    `172.28.0.0/16` network — cert SAN covers it), and its loopback socat
    relay on `CONTROL_PORT` forwards the netmap's DERP host (`127.0.0.1` on
    the single machine) to the server — not `host-gateway`, not
    loopback-published ports, and never a hostname. No `extra_hosts`
    hostname mapping is used (removed 2026-08-16).
  - `tests/unit/test_scripts.py::test_control_host_defaults_consistent`
    FAILS CI if the literal `host.docker.internal` reappears in any runtime
    config, script, test or CI file — keep it green.
- Tailnet modes are brought up with `make up-tailnet`, not
  `make up --profile tailnet`.
- `diskImageType="bytes"` (HttpBytesDevice), same-origin ext2 with nginx
  byte-range serving. `CloudDevice`/`GitHubDevice` are reference-WebVM
  variants, not used here.
- **NEVER add `xdotool` to the guest image — it breaks the image
  completely. Absolute rule (2026-08-28).** Do not install it, do not let
  any dependency pull it in (nothing that depends on `libxdo` either). The
  paste lane types via the XTEST extension through `xsendkeys`
  (`diskimage/xsendkeys.c` — a tiny C binary built in the Dockerfile
  `xsendkeys-build` stage, XTestFakeKeyEvent + XSync per command) driven by
  the shell daemon `diskimage/rootfs/usr/local/bin/paste-typer.sh`; if a
  future change needs another X input path, pick something that is NOT
  xdotool.

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

## Live instance (debugging aid)

A live **public** running deployment of this repo exists for real-world
debugging:
`https://webvm.nedprod.com` (site at `https://webvm.nedprod.com/`, served on the
standard HTTPS port 443). The `webvm.nedprod.com` HOSTNAME is Cloudflare's
frontend for the origin box (DNS 104.21.x.x); SSH DIRECTLY TO THE ORIGIN IP
**82.47.22.78** (host key is registered under the name `webvm.nedprod.com`, so
use `ssh -o HostKeyAlias=webvm.nedprod.com root@82.47.22.78`), git checkout at
`/root/webbrowser-python-idle` (deployment state lives in its `.env`, which is
never committed). It runs `STORAGE_BACKEND=browser` via `make up` (nginx-only,
disconnected sessions, no gateway). A host cron runs `scripts/reset-cycle.sh`
every six hours (02/08/14/20 UTC); the reset script only restarts the service
when the backend has storage to reset (webdav) or when a git pull + rebuild
happened. Use this instance to reproduce and verify frontend/guest behavior in
a real public browser context (cert: private CA at
`/root/webbrowser-python-idle/certs/ca.crt`, so E2E/browser checks use
`ignoreHTTPSErrors`).

> **NOTE (2026-09-01): `webvm.nedprod.com` is now proxied through Cloudflare.**
> Public requests to the site go through Cloudflare's edge: browsers see
> Cloudflare's cert (not the private CA), response headers/caching can be
> re-written by the proxy (e.g. CDN `max-age`, stripped headers, TLS version),
> and the origin still serves 443 but only sees the proxy as the client.
> This may affect later testing results — reproduce/verify with the proxy in
> mind, and reach the origin directly (`--resolve` / the private CA /
> `ignoreHTTPSErrors`) when the proxy itself would skew the check.
>
> **NOTE (2026-09-03): GitHub transport restored.** Pushing from the Mac works
> again (2026-09-03), and the box fetches the repo over plain HTTPS with NO
> stored credentials (verified 2026-09-03) — the GitHub repo is effectively
> fetchable read-only from the box. Deploy now = push from the Mac, then on
> the box `cd /root/webbrowser-python-idle && git pull --ff-only` (a no-op
> when the 6-hourly reset-cycle cron already pulled it — the cron runs
> `git fetch`+`git pull --ff-only` + `make build` + restart whenever upstream
> changes, so an explicit pull right after a push keeps that rebuild off the
> cron's next run when the change is already built/rolled by hand).
> CAVEAT: the cron rebuilds on ANY upstream change including docs-only
> commits — pull doc commits to the box manually right after pushing so the
> cron sees nothing new. Fallback when GitHub is unreachable: `git bundle
> create /tmp/live-update.bundle main`, `scp` it to the box, then on the box
> `git fetch live-update.bundle main:refs/remotes/live/main && git merge
> --ff-only refs/remotes/live/main && rm live-update.bundle`. Box toolchain:
> node 24 + npm 11, ~1 GB RAM (builds are slow; run under nohup and poll).
> Box has ~900 MB free disk — prune (`docker system prune -af`) before big
> rebuilds.
