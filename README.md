# WebVM personal Linux desktop — LAN-only, self-hosted

<!-- CI badge. Replace OWNER with the GitHub owner/org after the first push.
     The workflow file (planned in plans/webvm_implementation.md §8) will land
     at .github/workflows/ci.yml; until then the badge shows "no status". -->
[![CI](https://github.com/OWNER/webvm-custom/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/webvm-custom/actions/workflows/ci.yml)

A **planning repository** for building a personal Linux desktop that runs
entirely in the browser via [WebVM/CheerpX](https://webvm.io): a minimal
**i386 Alpine** guest with **stdlib-only Python and IDLE** (`idle3.10`),
an Xorg/i3 desktop, **LAN-only networking**, and **configurable persistent
storage**.

There is **no implementation code yet**. All work lives in
[`plans/`](plans/): the authoritative
[`webvm_implementation.md`](plans/webvm_implementation.md) (design complete,
four review rounds, decisions closed — implementation not started) and
[`implementation_options.md`](plans/implementation_options.md) (the option
comparison that motivated it). See `prompts/research.md` for the original
research prompt.

## What this project is

- A single-machine Docker Compose stack that serves `https://127.0.0.1:<SITE_PORT>`
  (or `https://<LAN_IP>:<SITE_PORT>` on a LAN) using a **private CA** you trust
  once in your browser. HTTPS is the only access mode.
- Guest files persist in the **browser (IndexedDB overlay)** by default, or
  sync through a guest-side agent to your existing **Samba** share or a
  **container WebDAV** backend — your choice at build time.
- **LAN-only by design:** the guest can only reach relayed services; it has no
  exit node, no public DERP, and the page makes zero external requests.

## How it differs from existing projects

| Existing project | What it is | How this differs |
|---|---|---|
| **[leaningtech/webvm](https://webvm.io)** | The original Linux-in-the-browser VM (Debian, ~2 GB), hosted publicly. | Public internet + **public Tailscale** login; no LAN-only confinement; no configurable storage backends; big Debian image; telemetry (logtail/plausible) not blocked; multi-user public service. This project is a private, single-user desktop: private CA, self-hosted Headscale, no public network. |
| **[webvm.io/alpine.html](https://webvm.io/alpine.html)** | The Alpine/Xorg/i3 graphical desktop (basis for this project's UI). | Much larger image (gcc, nodejs, LightDM, rofi/polybar…) vs. a stripped-to-Python+IDLE guest (~230 MB, no display manager); still public Tailscale and browser-only persistence. |
| **[Mini.WebVM](https://mini.webvm.io)** | Serverless, GitHub-Pages deployment: Dockerfile → ext2 via CI, chunked image streaming, service-worker COOP/COEP injection. | Fully static with **no server side at all**; terminal-only; public Tailscale; no persistence backends. This project needs (and runs) its own container for nginx/Headscale/WebDAV/gateway, so none of the Pages workarounds are needed. |
| **GitHub Pages forks** of webvm | Any fork deployed via the "Deploy" workflow. | Same as Mini.WebVM, plus unversioned CheerpX and no content control; this project pins exact versions and serves everything from its own private HTTPS origin. |
| **[PythonFiddle](https://pythonfiddle.com)**, **BrowserPod** (Leaning Technologies) | CheerpX-based, but not WebVM: a hosted REPL and a commercial in-browser sandbox product. | Not WebVM implementations; included for context on CheerpX licensing and the "persistent sandbox" space. |

None of the WebVM implementations above provides **LAN-only networking,
self-hosted Headscale/embedded DERP, a gateway relay, configurable storage
backends, or secret handling** — those are the novel parts of this project
(and the reason its Phase 1 runs a `browser`-mode desktop end-to-end before
any tailnet is involved).

## Current status

- [x] Research and option comparison (`plans/implementation_options.md`)
- [x] Implementation plan, reviewed and decisions closed (`plans/webvm_implementation.md`)
- [ ] Phase 1 — site + guest + browser persistence (no tailnet)
- [ ] Phase 2 — Headscale control plane + gateway + relays
- [ ] Phase 3 — guest sync agents + full validation

## Notes

- **Personal use.** CheerpX is free for personal/exploration use; this project
  is not intended for organizational distribution. See
  [cheerpx.io/licensing](https://cheerpx.io/licensing).
- **No secrets in this repo.** Nothing here contains credentials; the plan
  specifies that secrets are supplied at deploy time via `.env`.
