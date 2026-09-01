# WebVM (Option A1) Implementation Plan — Personal Use, Minimal Alpine + IDLE, LAN-Only Networking, Configurable Storage (Browser | Samba | Container WebDAV)

Research date: 2026-08-08 · Revised 2026-08-09 (see note below).

> **Revision note (condensed 2026-08-26; per-round detail is in the §12/21
> records and git history):** rounds 5–7 (2026-08-09) corrected the design —
> the SvelteKit output path (`alpine.html`), WebSocket upgrade headers on
> `/derp`, the headscale fixed-IP claim, runtime external requests (blog
> images, Claude tab, service worker), `nc` in base busybox, DERP/STUN
> TEST-NET placeholders, the CSP set, the content-stable cacheId fingerprint,
> the preauth-key bootstrap, the `python:3.11-alpine` server base (now 3.14,
> §12/21(33)), and the implementation-time deviations recorded in §12/21
> (path-less `server_url`, catch-all 8443 proxy, self-hosted runtime at
> `/cheerpx/`, numeric user id, `HEADSCALE_BOOTSTRAP=1`, dropped
> `EXTRA_BIND_IP`, first-boot SSH key, recursive sync).
> Round 8 added the baked page config (§12/21(28)), round 9 the fatal-error
> overlay (§12/21(29)), round 12 the silent-halt surfacing + vendored cxcore
> trap patch (§12/21(32)), and rounds 10–11 the HOSTNAMES-ARE-BANNED verdict
> (§12/21(30)-(31), enforced in CI).
> all decisions are in the body (§3, §4, §5, §12) and full rationale is in git
> history. Current standpoints in one paragraph: **HTTPS is the only access
> mode** — `https://127.0.0.1:<SITE_PORT>` single-machine (private CA trusted
> once) or `https://<LAN_IP>:<SITE_PORT>` on a LAN; `STORAGE_BACKEND=browser`
> is the default and works end-to-end in Phase 1 with no tailnet; networking
> (Phase 2/3) uses a self-hosted Headscale control plane with an **embedded
> DERP relay**, a `gateway` container (tailscaled userspace + socat relays to
> the LAN), the gateway tailnet IP **recorded after first join and kept stable
> by persistent node state**, and `derp.urls: []` (no public
> Tailscale DERP). The guest is a minimal i386 Alpine 3.24 (uplifted from
> 3.17, §12/21(34)) with stdlib-only Python + IDLE (`idle3.14`), an Openbox
> desktop running a Tk file explorer; persistence is a browser IndexedDB
> overlay, or a guest sync agent against Samba (`pysmb`) or container WebDAV
> (wsgidav).

## 1. Summary

Build a Docker container that serves a website which boots a **graphical Linux
desktop inside the browser** using WebVM/CheerpX. The Linux runs entirely
**client-side** in WASM. The container also provides the services that give each
browser VM **LAN-only networking**; persistent files are stored on a
**configurable backend** — by default in the **browser's IndexedDB** (zero
infrastructure), with optional **Samba** or container **WebDAV** backends
(configurable served path).

Scope: **personal use only**; Alpine base; **Python + IDLE** (launched on
demand from the autostarted file explorer); terminal + file manager; a **git
client** (clone/pull/push to
LAN remotes); no other GUI apps; **minimal ext2 image size**; guest network
restricted to the **LAN only — the guest must never have public internet
access**. The repo is public and is validated by **GitHub Actions CI** (guest
image + ext2 build, frontend build, server smoke test, lint) and a **layered
test suite** — unit, rootfs smoke, server integration, and a real-browser
Playwright E2E that boots the VM — plus a LAN acceptance script (§10).

**The site, the control plane and DERP are all served over HTTPS with a
private CA, and HTTPS is the only access mode** (this is mandatory: CheerpX
requires SharedArrayBuffer, which is only available in a secure context, and
plain HTTP on a LAN IP is not one). Single-machine use is
`https://127.0.0.1:<SITE_PORT>` — the private CA is installed/trusted in the
browser once, exactly as for LAN use, so there is one port, one trust story,
and one CI/E2E path (§5).

References: https://github.com/leaningtech/webvm ,
https://github.com/leaningtech/alpine-image ,
https://cheerpx.io/docs/guides/custom-images ,
https://cheerpx.io/docs/tutorials/full_os

## 2. How WebVM works (architecture)

- **CheerpX** (`@leaningtech/cheerpx`) JIT-compiles x86→WASM, emulates Linux
  syscalls. Guest networking exists **only** via `networkInterface:
  { authKey, controlUrl, stateUpdateCb, netmapUpdateCb, … }` — the
  Tailscale/Headscale protocol tunnelled over WebSocket. **Re-verified
  2026-08-09 against the CheerpX 1.2.8 docs (Networking guide):** the only
  networking API is the Tailscale `networkInterface`; **there is no custom
  WebSocket/TCP proxy API** (that is v86's model — v86 ships a generic
  WebSocket proxy; CheerpX routes all guest traffic through the browser-side
  Tailscale client). `stateUpdateCb`/`netmapUpdateCb` are required callbacks
  that the stock webvm frontend already wires up.
- Guest root filesystem = **ext2 image** over HTTP byte ranges; writes go to an
  overlay whose cache is **IndexedDB** (`IDBDevice`) — or `OpfsDevice` if the
  pinned build uses OPFS; **verify the pinned webvm/CheerpX version's device
  choice at implementation**, since the E2E persistence assertion depends on it
  — keyed by a `cacheId` string passed in the page config. **The cache key is
  per-origin shared by default** — all tabs on the same site share the same
  overlay (this drives the single-session guard in §4).
- `needsDisplay=true`, `cmd="/sbin/init"` boots the guest; Xorg renders through
  the **KMS canvas** (`cx.setKmsCanvas`); `99-screen-resize.sh` matches the X
  resolution to the canvas.
- **Cross-origin isolation is mandatory** (SharedArrayBuffer ⇒ secure context):
  the site must be **HTTPS** (private CA on the LAN or on `127.0.0.1`), with
  COOP `same-origin`, COEP `require-corp`, CORP `cross-origin`. **There are no
  plain-HTTP access paths** — `http://localhost`/`127.0.0.1` are secure
  contexts in principle, but the plan deliberately serves the site over HTTPS
  on every access path so a single TLS/CA story applies to single-machine and
  LAN use alike (§5).
- **Browser-side telemetry disclosure:** the browser Tailscale client
  (tsconnect WASM) configures Tailscale **logtail** logging to logtail's
  default endpoint (`logpolicy.NewConfig(logtail.CollectionNode)`;
  `logtail.Config.BaseURL` defaults to `https://log.tailscale.com`, collection
  `tailnode.log.tailscale.io` — source-verified; re-check the exact endpoint in
  the pinned build). It is browser-side
  only — never guest egress — and the plan **blocks** it with a CSP
  `connect-src` (see §5), so the page and WASM client make **zero** external
  requests; the E2E no-egress test asserts this (§9.4).

**Public WebVM implementations examined (2026-08-09):** the reference
`leaningtech/webvm` repo (README, `src/lib/WebVM.svelte`,
`config_github_terminal.js`, `.github/workflows/deploy.yml`), its live
deployments, and the Mini.WebVM launch post were read/visited directly. None
of them provides LAN-only networking, self-hosted Headscale/embedded DERP, a
gateway relay, configurable storage backends, or secret handling — those are
the novel parts of this plan. Comparison summary (full detail in git
history): **webvm.io** = public Tailscale + Debian terminal VM; its
**alpine.html** desktop = the reference for this plan's desktop (Alpine +
Xorg + KMS canvas) but far larger (gcc/LightDM/rofi…) and with public-
Tailscale-only networking; **mini.webvm.io / Pages forks** = serverless
deployments that need the ext2 pre-chunked (`diskImageType:"github"`) and a
service worker to inject COOP/COEP — workarounds this plan's self-hosted
nginx makes unnecessary (native Range serving); **PythonFiddle** = a CheerpX
REPL, noted only for licensing/UX framing. Worth copying: the frontend
mechanics (KMS canvas resize, the Dockerfile→ext2 pipeline); worth avoiding:
their public-internet assumptions and the anti-HTTPS workarounds
(all rejected here: LAN-only, no exit node, `derp.urls: []`, CSP-blocked
logtail, fingerprint-versioned overlay, sessionStorage secrets).

## 3. Decisions (resolved)

1. **License:** personal use → CheerpX free (package README). No commercial
   license; do not distribute the site organizationally.
2. **Base image:** `i386/alpine:3.24.1` (was 3.17 at design time; Tier B
   uplift 2026-08-20, §12/21(34)). The Dockerfile rewrites both repositories
   to the pinned branch (`https://dl-cdn.alpinelinux.org/alpine/v3.24/{main,community}` —
   rewrite, not append).
 3. **Apps:** `python3`, `python3-tkinter`, **IDLE** (the Alpine `python3-idle`
    package ships `/usr/bin/idle3.14` + `idlelib` — there is no `idle3`
    binary — and it hard-depends on `python3-tests`, an ~85 MiB install of the
    CPython test suite; the Dockerfile therefore **extracts `idlelib` and
    `idle3.14` from the package with `apk fetch` + `tar` instead of
    `apk add python3-idle`** so the guest stays minimal, Step 2), `xterm`,
    and **the file explorer** — a stdlib-only Tk app
    (`/usr/local/bin/file-explorer.py`, §12/25). **No display manager** (direct
    `su user -c startx` → openbox; fallback LightDM autologin). **The file explorer
    autostarts on the user's home directory**; `.py` files open in IDLE via
    `idle3.14-launcher` (the explorer yields the screen to IDLE while it runs);
    and a keep-alive daemon (`keep-file-explorer.sh`) relaunches the explorer
    whenever the last window closes, so the desktop never sits empty.
4. **No guest internet:** enforced by design (no exit node, LAN-bound services,
   host firewall; the page makes **no** public-host requests — Tailscale logtail
   is blocked via CSP `connect-src`, §5) — see §5.
5. **Persistence backend (build-time toggle):** `STORAGE_BACKEND` ∈
   `browser` (default) | `samba` | `webdav` | `none`.
   - `browser`: files persist in the browser **IndexedDB overlay** (WebVM
     default); no sync agent, no container file service.
   - `samba`/`webdav`: a guest **sync agent** keeps files in the network backend
     (native SMB/DAV *mounting* is impossible in the guest — no kernel FS
     modules, no FUSE — so IDLE/Tk keeps local paths).
   - `none`: no persistence — the frontend uses a **per-session random
     `cacheId`** so the overlay exists only for the lifetime of the tab
     (fresh overlay on every reload); no sync agent.
6. **Guest → backend reachability:** via the gateway's **tailnet IP** + **TCP
   relays** (socat), both provided by an in-compose `gateway` service (no host
   installs). Source-verified (tailscale `wgengine/netstack/netstack.go`):
   the browser Tailscale client hard-codes `RouteAll: false`
   (`cmd/tsconnect/wasm/wasm_js.go`) and never accepts advertised LAN subnet
   routes, so per-port relays are used. **Also source-verified:** tailscaled in
   userspace-networking mode rewrites inbound connections to the node's tailnet
   IP to `127.0.0.1:<same-port>` (`case isTailscaleIP: dialIP = ipv4Loopback`),
   so the socat relays bind to **`127.0.0.1`** inside the gateway container,
   not to the tailnet IP.
7. **pip caveat:** no guest internet ⇒ no runtime `pip install`; preinstall
   packages at image build time.
 8. **Git tooling:** `git` + `openssh-client-default` in the image; remotes are LAN-only
    (SSH or smart-HTTP) and target the **gateway tailnet IP on a relayed port**.
    The gateway's tailnet IP is **recorded after first join and kept stable by
    persistent node state** (§5.4 — headscale has no fixed-IP mechanism) so
    baked/runtime URLs and `known_hosts` stay stable.
 9. **Version pinning (new):** the webvm frontend is cloned at a **pinned commit
    SHA**; `@leaningtech/cheerpx` is pinned to an **exact version** in
    `package.json` (committed `package-lock.json`); Headscale and the
    `tailscale/tailscale` gateway image use pinned tags. CheerpX "every build is
    immutable" only holds if the dependency is frozen.
10. **Python packages (decided, revised 2026-08-09):** beginner-first
    curriculum ⇒ **stdlib is the entire baseline** — `turtle` (needs Tk;
    `python3-tkinter` already covers it), `random`, `math`, `time`,
    `datetime`, `json`, `csv`, `pathlib`, `os`, `sys`, `re`, `string`,
    `collections`, `statistics`. **`py3-numpy`, `py3-matplotlib`,
    `py3-requests`, and `py3-pytest` are NOT baked in** (removed 2026-08-09:
    nothing to install that a first course depends on; runtime `pip install`
    still fails — no guest internet — and every baked package adds ext2 size).
    Keep **`py3-pip`** so `pip` exists and the environment looks normal.
    **`pygame`/`ipython`/`black` are not packaged for Alpine 3.17 i386** — skip
    them; IDLE is the intended editor/REPL. (The samba sync agent still
    pip-installs `pysmb` at build — infrastructure, not curriculum.)
11. **Git remotes (decided):** tooling only — no remotes preconfigured; remotes
    are added from inside the guest later through a `2222` SSH relay (§4/§5).
12. **cacheId versioning (decided):** `blocks_alpine_<image-build>` where
    `<image-build>` is a **content-stable fingerprint** computed by `build.sh`
    (§12/10 — **not** the raw ext2 bytes) — the suffix changes whenever the
    image content changes, and stays put for content-identical rebuilds
    (§4/Step 4).
13. **Control-plane host (decided; REVISED Round 10 + Round 11, 2026-08-16):**
    all control-plane/DERP URLs are built from a single **`CONTROL_HOST`**
    value — `https://${CONTROL_HOST}:${CONTROL_PORT}`
    — rendered into `server_url`, the URL hash `controlUrl`, and the baked
    page config. Default is **`127.0.0.1`** (zero-config single machine);
    LAN use sets `CONTROL_HOST=<LAN_IP>` (hardcoded LAN address, §5).
    **HOSTNAMES ARE BANNED (Round 11, user mandate):** no
    `host.docker.internal`, no `/etc/hosts` entries, no custom DNS for LAN
    users — the browser must reach the control plane over 127.0.0.1 / a LAN
    IP alone, and `tests/unit/test_scripts.py` enforces the ban in CI. The
    Round 10 attempt to default 127.0.0.1 was reverted because it appeared
    to break the guest data path; Round 11 supersedes that: the gateway
    reaches the control plane at the server's static compose-network IP
    (`GATEWAY_CONTROL_IP` = 172.28.0.10, cert SAN covered) and runs a
    loopback socat relay on CONTROL_PORT so the netmap's DERP host
    (`127.0.0.1` single machine / the LAN IP) is reachable from inside the
    gateway container (plans/networking-bug.md §16.10).

## 4. Persistence (configurable backend: browser | samba | webdav | none)

**Why not a mount:** verified against the CheerpX API — the only virtual
filesystems are WebDevice, IDBDevice, DataDevice, HttpBytesDevice,
OverlayDevice (plus the CloudDevice/GitHubDevice block-device variants used by
the reference WebVM); no kernel FS modules, no `/dev/fuse`. `mount.cifs`, `davfs2`,
`rclone mount`, NFS, sshfs are all impossible; the network backends are used via
a userspace **sync agent** (tarball snapshot exchange keeps round-trips low over
the WebSocket tunnel).

**Mode A — Browser (default, `STORAGE_BACKEND=browser`):**
- No sync agent and no container file service. The live overlay persists in the
  browser (`OverlayDevice` + `IDBDevice`), with a **cacheId versioned to the
  image build** — `blocks_alpine_<image-build>` where `<image-build>` is a
  **content-stable fingerprint** of the guest-image inputs computed by
  `build.sh` (§12/10 — deliberately **not** the raw ext2 bytes, which embed
  `mkfs.ext2` timestamps and a random UUID and would churn the key on every
  content-identical rebuild). Injected at frontend build time. Survives
  reloads and container restarts on the same browser profile; bound to that
  profile.
  **Why versioned:** the IndexedDB overlay stores block deltas against the
  specific base ext2; if the image is rebuilt but the cacheId stays constant,
  stale deltas are applied to a different base → corrupted filesystem on next
  boot. Versioning the cacheId makes an image upgrade start a fresh overlay
  automatically — while a no-op rebuild keeps the same fingerprint and thus
  the same overlay.

**Mode B — Samba (`STORAGE_BACKEND=samba`):**
- Target: the user's existing Samba server at its LAN IP (routed through the
  tailnet gateway; no software added to the Samba machine). The container runs
  no Samba server.
- Agent client (default = smallest): the sync agent uses **`pysmb`** (pure
  Python, pip-installed at build, ~0.5 MB installed, no compiled deps —
  SMB1/2, fine for typical Samba shares and Python 3.10). Upgrade path:
  **`smbprotocol`** (~5–6 MB incl. `py3-cryptography`) if SMB3 is required;
  **`samba-client`** (`smbclient`, ~25 MB closure dominated by `samba-libs`) is
  only a compatibility fallback. Tested against the target share in §10.
- Inputs: Samba LAN IP/name, share, credentials → `/root/.syncrc` (the guest
  reaches it via the gateway's recorded tailnet IP + port-445 relay). The values
  are **`.env.example` deploy-time inputs (`SAMBA_LAN_IP`/`SAMBA_SHARE`/
  `SAMBA_USER`/`SAMBA_PASS`) passed to the guest image build as `build.sh`
  args**, so the baked `/root/.syncrc` is **functional out of the box**; the
  runtime `/opt/syncrc` injection (§4 Mode C) overrides it without a rebuild.
  In CI the args default to placeholders.

**Mode C — Container WebDAV (`STORAGE_BACKEND=webdav`):**
- The server container runs **wsgidav** (Python WebDAV server; chosen over
  nginx `ngx_http_dav_module` because the nginx dav module implements **only**
  PUT/DELETE/MKCOL/COPY/MOVE — **no PROPFIND** — and the sync agent requires
  PROPFIND for its per-file mtime manifest; "WebDAV clients that require
  additional WebDAV methods to operate will not work with this module").
  wsgidav supports PROPFIND, LOCK, basic auth, and large-file uploads.
- Serves a **configurable root directory** (`WEBDAV_ROOT`, default
  `/data/webdav`) on a Docker volume (`${DATA_DIR}:${WEBDAV_ROOT}`), published
  on the LAN IP (`WEBDAV_PORT`) with `auth_basic`-style credentials (htpasswd).
- The guest sync agent targets
  `http://<gateway-tailnet-IP>:<WEBDAV_PORT>/webdav/` with basic auth. The
  endpoint is **injected at runtime** (subject to the DataDevice spike below),
  not baked, so host port remapping needs no image rebuild. Zero extra guest
  packages (Python stdlib `urllib` PUT/GET/PROPFIND; `curl` optional).
- Runtime injection: the page reads `syncUrl`/`syncUser`/`syncPass` **from the
  URL hash only** (there is no usable "same-origin default": the WebDAV
  endpoint lives at `http://<gateway-tailnet-IP>:<WEBDAV_PORT>/webdav/`, which
  is neither same-origin with the page nor reachable by the guest via the LAN
  IP — the guest has no subnet routes), **moves them — together with
  `authKey`/`controlUrl` — to `sessionStorage` and strips the hash via
  `history.replaceState`** (so no secrets persist in browser history), then
  writes the sync config into a `DataDevice` mounted at `/opt`
  (`writeFile("/syncrc", …)` → guest path `/opt/syncrc`, §4 Mode C spike)
  before boot; the guest sync agent reads that at startup, falling back to
  baked `/root/.syncrc`.
- **DataDevice injection (RESOLVED — works, §12/21(i)):** the page writes the
  sync config via `dataDevice.writeFile("/syncrc", …)` with the device mounted
  at `/opt` (paths are relative to the device root — mounting at `/opt/syncrc`
  would produce `/opt/syncrc/syncrc`). The **baked `/root/.syncrc`** fallback
  (built from real build args, Mode B/Step 2) works without the injection;
  port remapping then requires an image rebuild (the baked fallback already
  targets the recorded gateway tailnet IP, so it only changes when
  ports/creds change).
- `WEBDAV_ROOT` is configurable at build time (baked into the wsgidav config)
  and at run time (entrypoint runs `envsubst` over a template).

**Mode D — None (ephemeral, `STORAGE_BACKEND=none`):**
- No persistence: the frontend generates a **random `cacheId` per session**, so
  the overlay is created fresh for the tab and discarded on reload. Use for
  throwaway or read-only sessions; not recommended when files must survive.

**Multiple sessions / tabs — single-session guard (all persistent modes):**
- Because the IndexedDB overlay cache is **shared per origin** (fixed
  `blocks_alpine_<image-build>`), two live tabs would share one overlay and can
  corrupt it — in `browser` mode too. Fix: a **browser-level session guard**
  implemented in the frontend using `localStorage` + `BroadcastChannel`:
  - On page load, acquire the origin lock (token + ~10 s heartbeat, ~90 s
    expiry, released on `pagehide`/`beforeunload`).
  - If another live tab holds it, this tab **boots the VM with an ephemeral
    overlay** (random `cacheId`, like `none` mode) and shows a "session already
    active in another tab" notice — it never writes to the shared overlay.
  - **Throttling-safe liveness:** hidden/backgrounded tabs get their timers
    throttled (Chrome intensive throttling can slow `setInterval` to ~1/min),
    which could let the ~90 s expiry false-claim a still-alive holder. The guard
    therefore uses **`BroadcastChannel` as the primary liveness signal** (ping
    the holder before taking over; only reclaim on expiry *and* a missed ping)
    and keeps the heartbeat interval well below the worst-case throttle so a
    genuinely alive tab never loses the lock. Residual risk: Chromium's
    intensive throttling can also delay delivery of message events to a hidden
    tab, so the takeover window is small but nonzero — acceptable for personal
    use; keep the heartbeat margin generous.
  - This guard is independent of the storage backend: it protects the live
    IndexedDB overlay in `browser`/`samba`/`webdav` modes. It also means a
    "Reset" in one tab can never wipe another tab's data. **Scope caveat:** the
    guard is per browser **profile** (localStorage/BroadcastChannel are
    per-profile, not per-machine) — two separate profiles/browsers on one
    machine are not serialized by it and must not run against the same origin
    concurrently; the supported workflow is one live session per profile.
    **Different site origins are separate sessions:** `https://127.0.0.1:<SITE_PORT>`
    and `https://<LAN_IP>:<SITE_PORT>` get separate IndexedDB overlays *and*
    separate guards, so a profile can legitimately run one session per origin —
    each with its own data. (Samba/WebDAV sync is additionally arbitrated
    across machines by the backend lease.)
- In `samba`/`webdav` modes the guest **sync agent** additionally holds a
  **backend lease** (`/lock` with a timestamp, refreshed every ~15 s, released
  on shutdown; stale leases > 90 s expire) so a second *non-browser* session
  (or a machine restoring from the backend) never races the sync.
  - In `samba`/`webdav` builds the **ephemeral fallback tab still contains the
    sync agent and `/opt/syncrc`**; its boot-pull/push attempts are refused by
    the **backend lease** (only the persistent session holds it), so the guard
    never needs to block the agent itself.
- **Non-destructive pull:** pull only overwrites files whose backend version is
  **newer than the file's mtime at the time it was last pushed** (per-file
  manifest compared against the agent's local last-push record — **not**
  against wall-clock "now", so guest/browser vs backend clock skew cannot cause
  wrong overwrites), so a crash after local edits is not clobbered by an older
  snapshot. The manifest is read via **PROPFIND on the WebDAV backend and via
  SMB file metadata on the Samba backend** (same agent logic, different
  transport). **Deletions are not propagated** (documented limitation,
  acceptable for this workload): a file deleted locally stays orphaned on the
  backend and is never resurrected by a pull; a backend-side deletion does not
  remove the local file.
- Documented usage: one VM session at a time is the supported workflow.

**Samba/WebDAV modes:** pull the `~/` snapshot **on boot, before the desktop
starts** (from `/etc/local.d/desktop.start`, with a wait-for-tailnet retry
loop — up to ~90 s, every 5 s — because the guest network comes up only after
the browser Tailscale client connects), then **push right after writes**: the
agent scans `~/` every ~5 s and pushes a debounced (~2 s) delta whenever files
change (a full `~/` tarball is uploaded only when no per-file manifest exists
yet), plus a final best-effort push on shutdown (tab close / `SIGTERM` — in a
WASM guest this last push is unreliable, so the effective recovery point is
the write-triggered push, i.e. seconds). The IndexedDB overlay remains the
live overlay, the backend is the durable copy. **The agent is a single
process** started by `desktop.start` as `user` (`su user -c sync-home.sh …` —
the boot pull and the push loop are one invocation, so they cannot race the
lease or the manifest); the WM autostarts the file manager only. **The boot-pull is
best-effort: it runs as `user`, and X starts after the timeout regardless** (a
misconfigured tailnet must not delay the desktop indefinitely). **X itself is
also started as `user`** (`su user -c startx` in `desktop.start` — never as
root: Xorg refuses to run as root, and the WM's autostarts must land in
`/home/user`). `browser`/`none` need neither a sync agent nor a storage
endpoint.

## 5. Networking (LAN-only)

**Mechanism (required by CheerpX):** the guest's Tailscale client connects to a
**self-hosted Headscale** control server in the container, which also runs the
**embedded DERP relay**. All browser→LAN traffic stays on-LAN (WebSocket to the
container). The guest joins automatically via the URL hash
(`#authKey=…&controlUrl=…`) **— or, since Round 8 (2026-08-16), with NO URL at
all**: in tailnet modes the server entrypoint renders the same credentials into
the same-origin `/webvm-config.js` (`window.__webvmConfig`), and the page seeds
`sessionStorage` from it when the URL carries no hash, so visiting the site
root (`https://127.0.0.1:8081` → 302 `/alpine.html`) just works. Any explicit
hash makes the session fully explicit — the baked config is ignored, so saved
`make url` URLs behave exactly as before (see Step 4 and §12/21(28)).

**TLS (mandatory, not "production-only"):**
- **The site itself is served over HTTPS** on `SITE_PORT` (private CA). CheerpX
  needs SharedArrayBuffer, which requires a secure context; plain HTTP on a LAN
  IP is not one, so the VM would not boot otherwise. **HTTPS is the only access
  mode** (revised 2026-08-09): single-machine and LAN both use it.
- The **control plane and DERP** are WSS on `CONTROL_PORT` behind nginx.
- One private CA issues certs for the site, control, and DERP; the cert SAN
  includes `CONTROL_HOST`, `127.0.0.1`, `localhost`, the LAN IP, and the
  hostname used in the URLs. **The CA must be installed and trusted in the
  browser** before the page or control plane can be used — once, for
  single-machine *and* LAN use alike (TLS spike, Step 8). There is no "zero CA
  setup" loopback path.
- **Default access paths (decided):** single-machine use —
  **`https://127.0.0.1:<SITE_PORT>`** with the private CA trusted once;
  LAN/multi-device use — **`https://<LAN_IP>:<SITE_PORT>`** with the private CA
  installed on each device. Both are validated in §10.1; the E2E suite
  exercises the HTTPS path.
- **All control/DERP URLs derive from `CONTROL_HOST`**
  (`https://${CONTROL_HOST}:${CONTROL_PORT}`): default **`127.0.0.1`** for
  the zero-config single machine — **no `/etc/hosts` entry, no hostnames of
  any kind** (hostnames are banned: `host.docker.internal` and /etc/hosts
  tricks must never reappear; `tests/unit/test_scripts.py` enforces it in
  CI). The `gateway` container reaches the control plane at the **server
  container's static compose-network IP** (`GATEWAY_CONTROL_IP` =
  `172.28.0.10`, cert SAN covered) and forwards the netmap's DERP host
  (`127.0.0.1` single machine / the LAN IP) through a loopback socat relay
  on CONTROL_PORT (§5.2, networking-bug.md §16.10); LAN/multi-device use
  sets `CONTROL_HOST=<LAN_IP>` (hardcoded LAN address). See §12/13 and
  Step 6.
- **CORS (verified at implementation — §6 Step 6 + networking-bug.md
  §15.2.1):** the browser Tailscale client receives the DERP map **inside the
  control-protocol netmap over the WSS control channel** (headscale v0.29.x
  serves no `/derpmap` HTTP endpoint), so no cross-origin request fetches the
  DERP map; the one cross-origin HTTP request — `/derp/probe` — is handled by
  the 8443 listener's ACAO/Vary/CORP rules (Step 6). Headscale has **no
  `cors` config option**; the fallback is nginx `add_header` rules.

**Components:**
1. **Container:** nginx with **two HTTPS listeners inside the `server`
   container** — **8081 (`SITE_PORT`) for the site** with COOP/COEP/CORP, and
   **8443 (`CONTROL_PORT`) for the control plane and DERP**; both bind
   `0.0.0.0` so the `gateway` container can reach 8443 over the compose
   network (§5.2) — plus **Headscale** (plain-HTTP listener on
   `127.0.0.1:8080`, WSS-terminated by nginx on 8443) with embedded DERP; in
   WebDAV mode the container also runs **wsgidav** (port 8082) on a
   configurable root. In `samba`/`browser`/`none` modes no file service runs
   in the container. **Path routing (VERIFIED against the pinned headscale
   v0.29.3 — see §12/21(c)):** `server_url` is **PATH-LESS**
   (`https://${CONTROL_HOST}:${CONTROL_PORT}`), because the tailscale client
   posts `/machine/register` over the Noise channel with the `server_url` path
   verbatim and headscale's noise-internal router serves it at the root — a
   `/headscale` base path 404s node registration. The **embedded DERP relay is
   at the root-level `/derp`** (with `/derp/probe` and `/bootstrap-dns`
   alongside), and the DERP-map relay URL headscale hands clients is derived
   from `server_url` as `https://${CONTROL_HOST}:${CONTROL_PORT}/derp`
   (confirmed at runtime: with server_url `https://127.0.0.1:8443` the DERP
   map lists `HostName: 127.0.0.1`, `DERPPort: 8443`). nginx therefore proxies the **entire** 8443 listener to
   headscale with a single catch-all `location /` carrying the WebSocket
   upgrade headers (`proxy_http_version 1.1`, `Upgrade`/`Connection:
   $connection_upgrade`, `proxy_buffering off`, generous timeouts): headscale's
   DERP handler answers `426 Upgrade Required` and its TS2021 handler answers
   `500` to any non-upgraded request, and the browser connects to the control
   plane and the relay over WebSockets.
2. **Gateway node (no host installs):** a `gateway` compose service runs
   `tailscaled` in **userspace-networking mode** and the **socat relays in the
   same container** (a small `gateway/Dockerfile` built from the official
   `tailscale/tailscale` pinned tag with `socat` added — socat must share the
    gateway's network namespace with tailscaled, so it cannot be a separate
    service) joined to the container's Headscale as a plain member
     (**no `--advertise-routes`**, no exit node). Everything lives in Docker;
      nothing is installed on the host. The gateway joins with
      `--login-server https://${GATEWAY_CONTROL_IP}:${CONTROL_PORT}`
      (`GATEWAY_CONTROL_IP` = the server's static compose-network IP,
      default `172.28.0.10` — path-less, matches `server_url`; §12/21(c))
      and `--authkey $GATEWAY_AUTHKEY` (reusable — see §12/12 — so a recreated
      container can rejoin) and persists its tailscaled state on a named volume.
   **Reachability (fixed — works on Linux and Docker Desktop, NO hostnames):**
   because published ports bind only the host's loopback
   (`127.0.0.1`/`127.0.0.2`), a container reaching the host via `host-gateway`
   (the bridge IP, e.g. `172.17.0.1`) **cannot** reach them. Instead the
   `server` container gets a **static IP on a fixed-subnet compose network**
   (`172.28.0.10` on `172.28.0.0/16`, cert SAN `IP:${SERVER_IP}`) and the
   gateway points `--login-server` at `https://172.28.0.10:8443` directly —
   **no `extra_hosts` hostname mapping** (removed 2026-08-16). The netmap's
   DERP-map host (derived from the browser-facing `server_url` — `127.0.0.1`
   on the single machine, the LAN IP on a LAN) is reached by the gateway
   through a **loopback socat relay on CONTROL_PORT forwarding to
   `GATEWAY_CONTROL_IP:CONTROL_PORT`**: on the single machine
   `https://127.0.0.1:8443/derp` inside the gateway container is its own
   loopback, and the relay makes it reach the server (without it the guest
   data path dies exactly as §16.9 observed); on a LAN the DERP host is the
   LAN IP, reached directly through the host (the relay is unused but
   harmless). The gateway also **trusts the private CA** for control/DERP
   TLS: mount `./certs:/certs:ro` and set
   `SSL_CERT_FILE=/certs/ca.crt` (Go honors this). socat binds ports below
   1024 (`445`, `2222`), so the gateway runs as root (the default user of the
   tailscale image).
3. **Reachability (resolved — no subnet routing, verified inbound path):** the
   browser Tailscale client hard-codes `RouteAll: false`
   (`cmd/tsconnect/wasm/wasm_js.go`), so it never accepts advertised LAN subnet
   routes. Instead the `gateway` service runs **socat TCP relays bound to
   `127.0.0.1`**; tailscaled's userspace netstack forwards inbound tailnet-IP
   connections to `127.0.0.1:<same-port>` (verified in
   `wgengine/netstack/netstack.go`), and socat forwards them on to LAN targets:
   - `127.0.0.1:445 → <samba-LAN-IP>:445` (samba mode; LAN IP from `.env`,
     filled at deploy)
   - `127.0.0.1:2222 → <git-host-LAN-IP>:22` (added **by the administrator on
     the host** — set `GIT_SSH_LAN_IP` in `.env` and recreate the gateway;
     the in-guest step is only `git remote add …:2222` afterwards; one relay
     per git/SSH host)
   - `127.0.0.1:<WEBDAV_PORT> → server:<WEBDAV_PORT>` (webdav mode; via the
     compose network, `server` = service name)
   The guest uses **only tailnet IPs** — raw LAN IPs are unreachable, which is
   what keeps the guest confined to the relayed services.
   *Alternative (host install):* if in-container `tailscaled` misbehaves on a
   given Docker Desktop version, run Tailscale on the Docker host instead
   (`tailscale up --login-server … --authkey …`) and keep the socat relays in
   the `gateway` service (on the host the relays bind the tailnet IP or
   `127.0.0.1` as appropriate).
4. **Gateway tailnet IP (corrected):** headscale v0.29.x has **no fixed-IP or
   reserved-IP mechanism** (verified: `headscale nodes` offers only
   register/list/list-routes/expire/rename/delete/backfillips/tag/
   approve-routes, and allocation is `prefixes.allocation: sequential` starting
   at `100.64.0.1` in registration order), so an IP cannot be pinned by the
   server. Stability is instead achieved by **persistence**: headscale's SQLite
   DB on a named volume (§6 Step 6) plus the gateway's tailscaled state on a
   named volume (§6 Step 7) keep the node record — and therefore its allocated
   IP — stable across recreations. **Procedure:** after the gateway's first
   join, read its assigned IP (`headscale nodes list` / `tailscale status`) and
   record it as `GATEWAY_TAILNET_IP` in `.env`; the value must be the **actual**
   assigned IP, not a pre-reserved default. If the headscale DB is ever wiped,
   the IP changes and `GATEWAY_TAILNET_IP`, baked `syncrc`/remotes/`known_hosts`
   must be updated (documented recovery step). Re-check the pinned headscale
   version for a fixed-IP feature (§12/21(l)).

**Host-published ports (containers listen on `0.0.0.0` internally; exposure is
restricted on the host):** the "LAN + loopback only" rule is enforced with
Docker's port binding, **not** by binding the LAN IP inside the container (the
container has no interface for the host's LAN IP). Compose publishes each port
**on `${LAN_IP}`** — never on all interfaces (**REVISED at implementation: the
planned second `${EXTRA_BIND_IP}` binding was dropped** — `127.0.0.2` is not
bindable on macOS Docker Desktop (EADDRNOTAVAIL), compose cannot express a
conditional second port line, and a single loopback binding is all zero-config
single-machine use needs; §12/21):
- Defaults: `SITE_PORT=8081` (HTTPS site), `CONTROL_PORT=8443` (Headscale
  control + DERP behind nginx wss), `WEBDAV_PORT=8082` (webdav mode),
  **`STUN_PORT=3478` (UDP; the embedded DERP's mandatory STUN listener — see
  Step 6)**. Set directly in `compose.yaml` via inline defaults, e.g.
  `"${LAN_IP:-127.0.0.1}:${SITE_PORT:-8081}:${SITE_PORT:-8081}"`
  (same pattern for `CONTROL_PORT`/`WEBDAV_PORT`/`STUN_PORT`). **The default is
  loopback-safe:** `LAN_IP` defaults to `127.0.0.1`, so zero-config `make up`
  works on a single machine with no duplicate-bind collision. LAN/multi-device
  use sets `LAN_IP=<lan-ip>`. **No scheme-default WSS port (443) anywhere
  (REVISED 2026-09-01):** the page glue re-inserts the control port into the
  wasm client's port-dropped control-plane URLs (wss://\<host\>/ts2021, /derp,
  https://\<host\>/derp/probe — all dialed on CONTROL_PORT), so the gateway no
  longer publishes 443 and the tailnet runs on machines where host 443 is
  already occupied (the old `CONTROL_WSS_PORT` listener/relay/CSP-443 entries
  were removed; see §12/21 item 38).
- The page is opened at the published site port, so its own origin is always
  correct. The **URL hash** must carry the effective endpoints:
  `controlUrl=https://${CONTROL_HOST}:${CONTROL_PORT}` (path-less — see
  §12/21(c)) and (webdav
  mode) `syncUrl=http://<gateway-tailnet-IP>:<WEBDAV_PORT>/webdav/`.
- The entrypoint renders the nginx and Headscale templates from
  `CONTROL_HOST`/`LAN_IP`/`SITE_PORT`/`CONTROL_PORT`/`WEBDAV_PORT`/`STUN_PORT`
  env vars, so the advertised control/DERP address, the DERP-map relay URL, and
  the STUN endpoint match what the host publishes. The private-CA cert SAN must
  include `CONTROL_HOST`, `127.0.0.1`, `localhost`, and the LAN IP.

**Route-acceptance finding (was "SPIKE GATE Step 8"):** resolved by source
analysis — the stock browser client sets `RouteAll: false` and `ipn/prefs.go`
defaults GOOS=js to false, so advertised LAN subnet routes are **not** accepted.
Re-verify at runtime once (cheap) when the stack is up; if CheerpX later exposes
an accept-routes option or a patched client, subnet routing could replace the
socat relays.

**"Never public internet" enforcement (all must hold):**
- **No exit node** anywhere (`--advertise-exit-node` never used) → the guest has
  no default route; only tailnet IPs (and the relayed ports) are routable.
- **No public DERP regions:** headscale config sets `derp.urls: []` (default is
  Tailscale's public DERP map) so clients are never pointed at
  `controlplane.tailscale.com`; only the embedded region is served (enabled per
  the pinned version's schema — verify the key names at implementation).
  `disable_check_updates: true` keeps the server itself off the network too.
- Docker ports published to the **LAN IP and loopback only** (see above); host
  services likewise.
- **Host firewall** (macOS `pf` or router ACL): drop egress beyond RFC1918
  (belt-and-braces; the no-exit-node rule is primary).
- **Page-level requests:** the stock webvm frontend loads
  `https://plausible.leaningtech.com/js/script.js`, preconnects to Google
  Fonts, and its `/` route fetches a public `disks.webvm.io` image — **all of
  these are removed**, along with the blog-post fetch/`og:image` URLs, the
  `serviceWorker.js` script, and the Claude/AI sidebar tab (Step 4); nginx
  `location = /` → 302 `/alpine.html`. **Tailscale logtail is
  blocked, not permitted:** the browser's Tailscale WASM client is
  **compiled-in** to log to logtail's default endpoint (BaseURL defaults to
  `https://log.tailscale.com`; the rebuilt wasm client's uploads were removed
  at source, networking-bug.md §16.1/16.7) — so the nginx site CSP
  `connect-src 'self' https://${CONTROL_HOST}:${CONTROL_PORT} wss://${CONTROL_HOST}:${CONTROL_PORT}`
  rejects the logtail fetch (CSP applies to no-cors fetches too), and
  headscale's netmap `Debug.DisableLogTail` (`logtail.enabled: false`, the
  default) may disable it client-side as well — either way the page and the
  WASM client make **zero external requests**, asserted by DevTools and the
  E2E no-egress test (§9.4); the host firewall drops egress beyond RFC1918 as
  a second layer. Logtail failures are **non-fatal** to the client.
- Headscale ACLs default (tailnet-only); MagicDNS off (`dns.magic_dns: false`
  — the guest uses IPs, never DNS).
- **TLS spike:** issue a private-CA cert (SAN: `CONTROL_HOST`, `127.0.0.1`,
  `localhost`, container hostname, LAN IP) covering the **site, control, and
  DERP**; install the CA in the browser (and in host Tailscale,
  `--operator`/trust). Validate before everything else.

## 6. Target architecture

```
webvm-custom/
├─ diskimage/
│  ├─ Dockerfile            # i386 Alpine guest (ARG STORAGE_BACKEND selects agent;
│  │                        # ARG SAMBA_*/SYNC_* render /root/.syncrc)
│  ├─ examples/             # curriculum scripts baked READ-ONLY into ~/examples
│  ├─ scripts/99-screen-resize.sh
│  ├─ config/               # xinitrc, openbox config (file-manager autostart), .Xresources
│  ├─ sync/                 # sync.py per backend (samba/webdav) + sync-home.sh
│  │                        # (not built for browser/none)
│  ├─ rootfs/root/.syncrc   # backend endpoint + credentials; rendered at build
│  │                        # from ARG SAMBA_*/SYNC_* (placeholders by default)
│  ├─ rootfs/home/user/.ssh # SSH key only — no remotes preconfigured (gitignored;
│  │                        # remotes added in-guest later via the recorded
│  │                        # gateway tailnet IP)
│  ├─ rootfs/home/user/.gitconfig   # user.name/email, defaults
│  └─ rootfs/etc/hosts      # optional LAN hostname → IP entries for remotes
│                          # runtime /opt/syncrc: injected via DataDevice from
│                          # URL-hash syncUrl/syncUser/syncPass (moved to
│                          # sessionStorage, hash stripped) — subject to the
│                          # DataDevice-population spike (§4 Mode C); baked
│                          # /root/.syncrc fallback
├─ webvm/                   # WebVM app (cloned from leaningtech/webvm @ PINNED SHA)
│  ├─ config_public_alpine.js   # needsDisplay=true, cmd=/sbin/init, image URL,
│  │                            # URL-hash params (authKey/controlUrl/syncUrl)
│  └─ src/lib/…             # persistence wiring: cacheId per mode, single-session
│                           # guard (localStorage+BroadcastChannel), /opt/syncrc
│                           # DataDevice injection, hash→sessionStorage
├─ server/
│  ├─ Dockerfile            # nginx + headscale (+ embedded DERP) + wsgidav (webdav mode)
│  ├─ entrypoint.sh         # fail-closed per-mode secret checks → envsubst nginx+
│  │                        # headscale+wsgidav templates (CONTROL_HOST, LAN_IP,
│  │                        # ports, STUN_PORT, WEBDAV_ROOT) → webdav.htpasswd →
│  │                        # headscale (only when needed; DB on a named volume)
│  │                        # → nginx → (webdav mode) wsgidav
│  ├─ nginx.conf.template   # two HTTPS server blocks: site on SITE_PORT (COOP/
│  │                        # COEP/CORP, CSP connect-src, = / → /alpine.html
│  │                        # redirect, serves alpine.html);
│  │                        # control+DERP on CONTROL_PORT (path-less catch-all
│  │                        # wss proxy to headscale; /key /ts2021 /derp all root)
│  │                        # WebSocket upgrade headers on the catch-all location)
│  ├─ headscale/config.yaml # server_url from CONTROL_HOST, embedded DERP + STUN,
│  │                        # derp.urls: [], no exit node, MagicDNS off, disable updates
│  ├─ wsgidav.yaml.template # WEBDAV_ROOT + basic auth (webdav mode only)
│  └─ webdav.htpasswd       # basic-auth file for the WebDAV endpoint (gitignored;
│                           # derived from WEBDAV_USER/WEBDAV_PASS env, fail-closed)
├─ gateway/
│  └─ Dockerfile            # tailscale/tailscale pinned tag + socat; trusts /certs
├─ compose.yaml             # services: `server` (static IP on a fixed-subnet
│                           # network; headscale DB + certs volumes), `gateway`
│                           # (tailscaled+socat in one image, profile "tailnet"
│                           # — NOT started in browser/none), `test-unit`
│                           # (pytest); ALL options inline with defaults
│                           # (${VAR:-default}); ports published on
│                           # ${LAN_IP:-127.0.0.1} (only; EXTRA_BIND_IP dropped)
│                           # UDP ${STUN_PORT:-3478}; gateway joins the control
│                           # plane at GATEWAY_CONTROL_IP (server's static IP,
│                           # no extra_hosts hostnames) + state volume;
│                           # pinned image tags; optional x-* block
├─ .env.example             # OPTIONAL overrides/secrets only (CONTROL_HOST,
│                           # LAN_IP, ports, WEBDAV_ROOT, DATA_DIR,
│                           # GATEWAY_TAILNET_IP, WEBDAV_USER/WEBDAV_PASS,
│                           # SAMBA_LAN_IP/SAMBA_SHARE/SAMBA_USER/SAMBA_PASS,
│                           # HEADSCALE_PREAUTHKEY, GATEWAY_AUTHKEY,
│                           # GIT_SSH_LAN_IP, GIT_HTTP_LAN_IP); compose falls
│                           # back to the inline defaults; secrets are only
│                           # enforced by the entrypoint in the modes that use
│                           # them (browser/none need no .env)
├─ certs/                   # private CA + server cert covering site/control/DERP (gitignored;
│                           # generated by scripts/gen-certs.sh — SAN: CONTROL_HOST,
│                           # 127.0.0.1, localhost, LAN IP; CI generates a throwaway set)
├─ .github/workflows/ci.yml # CI: guest matrix build + ext2, frontend, server smoke, lint
├─ tests/
│  ├─ unit/                 # sync agent + entrypoint/template unit tests (pytest);
│  │                        # frontend session-guard unit tests (vitest, optional)
│  ├─ rootfs/               # guest rootfs smoke tests (docker run webvm-guest)
│  ├─ server/               # compose integration: headers, ext2 range, WebDAV
│  │                        # (PROPFIND/PUT/GET via wsgidav), tailscaled→headscale join
│  ├─ e2e/                  # Playwright: real VM boot in headless Chromium
│  │                        # (render, no-egress, control plane, sync, persistence)
│  └─ fixtures/             # fake home dir, fake WebDAV server, test CA
├─ scripts/acceptance.sh    # manual/LAN acceptance checklist (CI can't cover LAN)
├─ build.sh                 # guest image → ext2 pipeline (accepts STORAGE_BACKEND;
│                           # still uses `docker build`/`docker create`/`docker export`)
└─ Makefile                 # thin wrappers: make certs (generate the private CA
                            # if missing) / build / up (depends on certs) /
                            # up-tailnet (= up + --profile tailnet) / down /
                            # logs / test / acceptance / url (print the full
                            # session URL from .env secrets)
```

Build config: `STORAGE_BACKEND=browser|samba|webdav|none` (default `browser`) and
the ports/roots are set **directly in `compose.yaml`** with inline defaults
(`${VAR:-default}`); `.env` is optional and only overrides them (and carries
secrets). `make build` runs the ext2 pipeline then `docker compose build`;
`make up` runs `docker compose up -d`. **The control plane is gated:**
`browser`/`none` builds start **neither Headscale nor the `gateway` service**
(nginx-only server); `samba`/`webdav` builds (and Phase 2 validation) run them
via the `tailnet` compose profile (`make up-tailnet`) and `HEADSCALE_ENABLED=1`.
The guest only ever connects when the URL hash carries a `controlUrl`.

## 7. Implementation steps (phased)

The work is phased so the **default `browser` mode works end-to-end before the
tailnet stack is added** (Phase 1; the control plane and `gateway` are **not
started** — compose profile + entrypoint gate), then networking (Phase 2) and
network backends (Phase 3) are layered on.

### Phase 1 — Site + guest + browser persistence (no tailnet)

#### Step 1 — Clone the frontend (pinned)
`git clone https://github.com/leaningtech/webvm.git webvm` at a **pinned commit
SHA** (record the SHA in `webvm/WEBVM_COMMIT`). `package.json` pins
`@leaningtech/cheerpx` to an **exact version** (commit the regenerated
`package-lock.json`). The `labs` git dependency (`git@github.com:leaningtech/labs.git`,
used only for the navbar) is not SSH-fetchable in CI: the frontend build script
**rewrites that URL to `https://github.com/leaningtech/labs.git`** before
`npm ci` (documented in Step 5).

#### Step 2 — Minimal guest image (`diskimage/Dockerfile`)
`FROM docker.io/i386/alpine:3.24.1` (was 3.17 at design time; Tier B uplift,
§12/21(34)); build-only DNS; point **both** repositories at the pinned
branch — **rewrite, don't append**:
```
cat > /etc/apk/repositories <<'EOF'
https://dl-cdn.alpinelinux.org/alpine/v3.24/main
https://dl-cdn.alpinelinux.org/alpine/v3.24/community
EOF
```
then:
```
apk add --no-cache alpine-base udev-init-scripts udev-init-scripts-openrc eudev \
  xorg-server xinit xf86-input-evdev xrandr openbox xprop xsetroot font-dejavu \
  python3 python3-tkinter xterm git openssh-client-default \
  busybox-extras dbus
```
(**dbus** added to the package list — the reference's `rc-update add dbus`
needs the package installed.) (Verified against the v3.24 x86 index: the
package names are **`xinit`** (not `xorg-xinit`) and
**`openssh-client-default`** (not `openssh-client`). `xf86-input-evdev` +
the static `/etc/X11/xorg.conf` input sections replaced the libinput path
(update-to-latest.md §9.1 item 7 — the udev input backend finds nothing in
CheerpX's shallow sysfs).
- `nc` is **already provided by the base busybox** (verified empirically in
  `i386/alpine:3.17`: `/usr/bin/nc` exists without any extra package; Alpine's
  `busyboxconfig` ships `CONFIG_NC=y`, while `busyboxconfig-extras` disables
  it), so the Step 8 route checks and the acceptance script need no extra
  package. `busybox-extras` is still installed as cheap optional diagnostics
  (telnet, traceroute, ftpget, …).
- Preinstalled Python packages: **`apk add --no-cache py3-pip` only** — the
  beginner curriculum is stdlib-only (`turtle` is covered by
  `python3-tkinter`); **`py3-numpy`, `py3-matplotlib`, `py3-requests`, and
  `py3-pytest` are deliberately NOT installed** (Decision 10; nothing in a
  first course needs them, and every package adds ext2 size).
- **IDLE without the 85 MiB `python3-tests` dependency:** `python3-idle`
  (community) hard-depends on `python3-tests` (the CPython test suite, ~85 MiB
  installed), so instead of `apk add python3-idle` the Dockerfile does:
  ```
  apk fetch --no-cache python3-idle && tar -xzf python3-idle-*.apk -C / \
    usr/bin/idle3.14 usr/lib/python3.14/idlelib && rm -f python3-idle-*.apk
  ```
  This installs the **`idle3.14` binary + `idlelib`** (the package provides
  only `/usr/bin/idle3.14` — **there is no `idle3`**), skipping the test-suite
  dependency. (Do **not** add the `python3-tkinter-tests` package either.)
  The rootfs smoke tests assert `idle3.14` (display-free: binary presence +
  `python3 -c "import tkinter, idlelib"`).
- Sync agent (selected by `ARG STORAGE_BACKEND`, installed only for
  `samba`/`webdav`): **samba** → the **`pysmb`** pure-Python agent by default
  (~0.5 MB installed, pip-installed at build; **pin its version** for
  reproducible builds and smoke-test it against Python 3.10 and a real Samba
  share early — it is unmaintained; `smbprotocol` at ~5–6 MB only if SMB3 is
  needed; `samba-client` at ~25 MB only as a compatibility fallback — see §4
  Mode B); **webdav** → no extra package (Python stdlib `urllib`;
  `curl` optional); **browser**/**none** → nothing extra. **`gvfs-smb` is
   excluded** (no guest `smb://` browsing — gio-only, does not help IDLE/Tk
   dialogs, and adds ~10–20 MB).
- Users/groups: `adduser -D -s /bin/ash user`; `addgroup user video input tty`;
  set `user`/`root` passwords. Bake `/root/.syncrc` from **build args**
  (`ARG SAMBA_LAN_IP`/`SAMBA_SHARE`/`SAMBA_USER`/`SAMBA_PASS`, and
  `ARG SYNC_URL`/`SYNC_USER`/`SYNC_PASS` for WebDAV
  `http://<gateway-tailnet-IP>:<WEBDAV_PORT>/webdav/` + basic auth) — the args
  **default to placeholders** (CI), and deploy-time values are passed by
  `build.sh` from `.env`, so the baked fallback is **functional without the
  `/opt/syncrc` injection**; the runtime injection remains the no-rebuild
  override. The **gateway tailnet IP must be the recorded one** (§5.4), so baked
  fallbacks stay valid.
- Git tooling (baked, **no remotes preconfigured** — remotes are added from
  inside the guest later): `~user/.gitconfig` (`user.name`/`user.email`
  placeholders to set at deploy, `init.defaultBranch=main`);
  `StrictHostKeyChecking=accept-new` (no host key baked). **REVISED at
  implementation:** the ed25519 keypair is **generated at first boot** by
  `/etc/local.d/desktop.start` (`ssh-keygen` if `~user/.ssh/id_ed25519` is
  absent) instead of being baked — the ext2 is served unauthenticated to
  browsers, and a baked key would be extractable and identical across all
  guests; the public key then authorizes on any LAN git server.
  **Adding a git remote is a two-step host+guest flow:** the
  administrator first sets `GIT_SSH_LAN_IP=<git-server-LAN-IP>` in `.env` and
  recreates the gateway (this adds the `127.0.0.1:2222 → <git-server>:22`
  socat relay); only then does the guest run `git remote add` pointing through
  the relay — **never the raw LAN host**. **Because the relay is on port 2222,
  the remote URL must use the explicit port:**
  `ssh://git@<gateway-tailnet-IP>:2222/<path>` (or an `~/.ssh/config` `Host`
  alias with `Port 2222`) — the shorthand `git@<IP>:…` would use port 22. The
  host key verified on first connect is the **LAN git server's** key (the
  gateway only relays TCP); if a pre-seeded `known_hosts` is desired it must
  hold the git server's public key, indexed by
  `<gateway-tailnet-IP>:2222`. No DNS in the guest (use IPs or `/etc/hosts`).
- No display manager (saves ~30–50 MB vs LightDM + GTK greeter; autologin is
  implicit — nothing here needs a login screen). Consequences handled manually:
   `99-screen-resize.sh` → `/etc/X11/xinit/xinitrc.d/`; `config/xinitrc` →
   `/home/user/.xinitrc` (`exec openbox-session` under `dbus-run-session`, plus
   `xsetroot -solid black`); `config/openbox` → `/home/user/.config/openbox`
   (`rc.xml` maximizes every window and binds `W+Return`→xterm,
   `W+Shift+f`→`open-file-explorer.sh`; `autostart` runs `open-file-explorer.sh`
   and `keep-file-explorer.sh`); optional `.Xresources`; keyboard layout via
   `setxkbmap` in `.xinitrc`. (The WM was switched 2026-08-18 from i3 to Openbox
   so each window gets a real titlebar ✕ Close button — i3 renders no close
   button. Openbox has no `i3-msg -t get_tree` tree IPC, so window enumeration
   is via xprop reading the EWMH `_NET_CLIENT_LIST` root property the WM
   maintains (the keep-alive counts it with the shell `wm-clients.sh`; the
   file explorer reads it in-process via `_wm_client_windows`). The sync
   agent is a **single process started by
   `desktop.start`** — not an Openbox autostart — so the boot pull and the push loop
   cannot race, §4.)
- X bootstrap without a seat manager: rely on udev + group membership
  (`video`/`input`/`tty`, added above) for the emulated DRM/input devices;
  create `XDG_RUNTIME_DIR=/run/user/1000` (owned by `user`, plus optional dbus
  session) in `/etc/local.d/desktop.start` so the Tk apps (file explorer, IDLE)
  behave. **Enable the openrc `local` service**
  (`rc-update add local default`) so `/etc/local.d/*.start` actually runs.
- Boot to X: `/etc/local.d/desktop.start` starts X **as the `user` account**
  (`su user -c startx` — never as root: Xorg refuses root, and the WM's
  autostarts must land in `/home/user`); if `startx`'s VT handling misbehaves
  in the WASM guest (no real TTYs), fall back to launching `Xorg :0
  -nolisten tcp -noreset` as `user` and then openbox. Ultimate fallback: the
  LightDM-autologin
  reference setup (add `lightdm` then). **In `samba`/`webdav` modes,
  `desktop.start` runs the boot pull FIRST (wait-for-tailnet retry loop, up to
  ~90 s, every 5 s) and only then starts X as `user`** (see Step 9).
- **Guest NIC config (RESOLVED, Phase 2):** the core never creates eth0
  (networking-bug.md §16.6); guest networking works through the core's
  syscall-level socket dispatcher, so the guest needs no interfaces file.
  `desktop.start`'s eth0 retry loop + udhcpc runs only when eth0 exists
  (networking-bug.md §15.2.6).
- **Build-time DNS and guest resolv.conf:** during `docker build` the container
  uses the build's DNS (BuildKit sandbox or the engine's `127.0.0.11` stub);
  `apk add`/`apk fetch` need it to reach the CDN. **REVISED at implementation:**
  the Dockerfile CANNOT overwrite `/etc/resolv.conf` — it is a
  container-managed bind mount (`rm` fails with "Resource busy", writes fail),
  but `docker export` already produces an **empty** resolv.conf in the exported
  rootfs, which is exactly right: the guest uses IPs only (no DNS, MagicDNS
  off; see the git-remote and syncrc IP-based URLs above).
- Size: strip `/usr/share/{doc,man,info}`, trim `/usr/share/locale`; `apk add
  --no-cache` + `rm -rf /var/cache/apk/*`.

#### Step 3 — Image build pipeline (`build.sh`)
`docker build --platform=linux/i386` → `docker create`/`docker export` → untar →
`mkfs.ext2 -m 0 -b 4096 -d rootfs image.ext2 <size>` — drop `-r 0`; the
reference CheerpX custom-image guide uses exactly `mkfs.ext2 -b 4096 -d <dir>
image.ext2 <size>` (max image size 2 GB) (rootfs + ~20% headroom, min 100 MB,
≤ 2 GB). No host installs: run the ext2 steps inside a throwaway helper
container, **with the untar + `mkfs.ext2 -d` on a container-local path and
only the final `image.ext2` written back into the mounted host dir** — e.g.
`docker run --rm -v $PWD:/work ubuntu sh -c 'apt-get update && apt-get install -y e2fsprogs && mkdir -p /tmp/rootfs && cd /work && tar -xzf rootfs.tar -C /tmp/rootfs && mkfs.ext2 -m 0 -b 4096 -d /tmp/rootfs /work/image.ext2 <size> && debugfs -R "stat /home/user" /work/image.ext2'`
(or a small `e2fsprogs` build image; needs e2fsprogs ≥ 1.46 for `-d`).
**Ownership (known pitfall):** `docker export` keeps uid/gid in the tar, but
they survive only if the untar + `mkfs.ext2 -d` run as root on a
**container-local** path — never into the macOS-mounted `$PWD`, which would
remap uids to the host user (the upstream `deploy.yml` documents the same loss
even with `docker cp -a`). Sanity-check with `debugfs` in the same container:
`/sbin/init`, `/home/user/.config/openbox/`, `/usr/bin/idle3.14`, the sync
agent, **and ownership** (`/home/user` = `1000:1000`, busybox applet symlinks
intact, setuid bits preserved).

**Image size (measured):** the original v3.17 estimate (≈190 MiB rootfs /
≈230 MiB ext2, computed from APKINDEX installed sizes) is superseded. After
the 2026-08-21 trims — the ~246 MiB Mesa/LLVM GL stack replaced by a 176 KiB
no-op libGL stub (Xorg runs ShadowFB/AccelMethod none; nothing loads GLX) and
a round-2 removal of unused runtime components (pyc prebake via compileall
retained; full list in §12/21) — the rootfs is ≈ 134.7 MiB and the webdav
ext2 builds at **137 MiB** (~161 MiB logical; browser/none builds comparable)
— about a third of the reference `alpine-image`. The SSH keypair is **not**
part of the image: it is generated at first boot by `desktop.start`, so the
image has no baked key.

#### Step 4 — Point the web app at the image + persistence wiring
`config_public_alpine.js`: `diskImageUrl="/custom-disk-images/webvm-custom-disk.ext2"`,
`diskImageType="bytes"`, `needsDisplay=true`, `cmd="/sbin/init"`, `args=[]`,
`opts={uid:0,gid:0}`. (`diskImageType="bytes"` is confirmed by the webvm
README's local-serving instructions.) Frontend edits (in
`webvm/src/lib/WebVM.svelte` and the page config):
- **cacheId per mode (image-versioned):** `browser`/`samba`/`webdav` use
  `blocks_alpine_<image-build>`, where `<image-build>` is a **content-stable
  fingerprint** of the guest-image inputs computed by `build.sh` (§12/10 —
  **not** the raw ext2 bytes, which embed `mkfs.ext2` timestamps/UUID and
  would churn the overlay key on every content-identical rebuild) so a rebuilt
  image starts a **fresh overlay** instead of applying stale deltas to a new
  base, while no-op rebuilds keep the overlay (§4). `none` uses a **random
  per-session id** (fresh overlay every load). In
  the default config the overlay is `OverlayDevice(blockDevice,
  IDBDevice(cacheId))` (or `OpfsDevice` if the pinned build uses OPFS — §2);
  in `none` mode the random cacheId makes it ephemeral without code-path
  changes. **Mode plumbing:** `build.sh` passes `STORAGE_BACKEND` and the
  image fingerprint to the frontend build (env vars `WEBVM_MODE`/
  `WEBVM_IMAGE_BUILD` read by `vite.config.js`/a generated
  `config_public_alpine.js`), so cacheId mode, URL-hash parsing (webdav sync
  params) and the single-session guard are selected per build — the frontend
  must know the storage backend at build time, not just the server.
- **Single-session guard:** acquire the origin lock (localStorage +
  BroadcastChannel, heartbeat/expiry as in §4) before mounting the fixed
  overlay; on contention, boot with an ephemeral (random) cacheId and show a
  notice.
- **URL-hash handling (all secrets out of the hash; Round 8 = baked page
  config, §12/21(28)):** on page load read `authKey`, `controlUrl`, and
  (webdav mode) `syncUrl`/`syncUser`/`syncPass` from the URL hash, move them
  to `sessionStorage`, then strip the hash via `history.replaceState` so **no
  secrets persist in browser history**. In tailnet modes the server entrypoint
  additionally renders the same values into the same-origin **`/webvm-config.js`**
  (JSON-escaped via `server/render-webvm-config.py` — never raw envsubst;
  `Cache-Control: no-store` + CORP same-origin), and the `app.html` inline
  script seeds `sessionStorage` from it **when the URL carries no hash** —
  visiting the site root auto-wires the tailnet. **Any explicit hash disables
  the baked config entirely** and marks the tab explicit for its lifetime
  (`webvm-explicit-session`), so saved `make url` URLs behave exactly as
  before and a later hash-less reload never re-arms the config. The
  hash→sessionStorage move must run in an inline script at the top of
  `app.html` before the bundle (network.js reads `sessionStorage`, adapted
  from the stock hash read). Bootstrap mode and `browser`/`none` render `{}`
  (disconnected). The webdav fail-closed set gained `GATEWAY_TAILNET_IP`
  (skipped during bootstrap — keep `HEADSCALE_BOOTSTRAP=1` until the IP is
  recorded, else the server crash-loops before the gateway joins).
  Write the sync config into a `DataDevice` mounted at `/opt` via
  `dataDevice.writeFile("/syncrc", …)` → guest sees `/opt/syncrc` (paths are
  relative to the device root; mounting at `/opt/syncrc` would yield
  `/opt/syncrc/syncrc`); the baked `/root/.syncrc` fallback stays (build
  args, functional out of the box). UX caveat: after the strip a new tab at
  the site root is **connected** (re-seeds from `/webvm-config.js`), not
  silently disconnected — `make url` prints the explicit-hash URL for the
  cases that need it.
- **No public-request frontend edits (required for the no-egress tests):**
  strip from `src/app.html`: the `https://plausible.leaningtech.com/js/script.js`
  tag, the `fonts.googleapis.com`/`fonts.gstatic.com` preconnects, **and the
  `serviceWorker.js` script** (also drop `serviceWorker.js` from the
  `viteStaticCopy` targets in `vite.config.js` — the stock SW is inert only
  while COOP/COEP are served, and otherwise rewrites redirects to 301-with-
  null-body and force-reloads on update, which would break the `/` redirect
  and the E2E); **remove the remaining runtime external content:** the
  `+layout.server.js` blog-post load (7 `labs.leaningtech.com` URLs fetched at
  prerender time, whose `og:image` URLs the Posts tab loads at runtime) and
  the Claude/AI sidebar tab (`AnthropicTab.svelte`, which POSTs to
  `api.anthropic.com` when used; remove the icon in `SideBar.svelte` and the
  `anthropic.js` import in `WebVM.svelte`) — so "zero external requests" is
   literal, not conditional on which sidebar tab is open; the root `/` route
   (`config_public_terminal.js` → a public `disks.webvm.io` image) is
   redirected at nginx (`location = /` → 302 `/alpine.html`) so it can never
   load.
- **CheerpX runtime self-hosting (REVISED at implementation, §12/21):** the
  pinned `@leaningtech/cheerpx` package is only a thin wrapper that
  dynamic-imports its core from `https://cxrtnc.leaningtech.com/<version>/` —
  an external request the page must never make. The pinned 1.3.7 runtime is
  downloaded at pin time into `webvm/cheerpx/` (committed; regenerable via
  `scripts/fetch-cheerpx-runtime.sh`) and served same-origin from `/cheerpx/`;
  the frontend imports it through `src/lib/cheerpx.js` (a vite alias — the
  bundler never rewrites the dynamic-import URL, so the runtime's relative
  module loads stay on our origin), and the CSP allows it (see the CSP note in
  §6).
- **Route:** SvelteKit (adapter-static, default `trailingSlash: 'never'`)
  emits the alpine page as **`build/alpine.html`** — verified: the upstream
  `deploy.yml` does `rm build/alpine.html`, the README and live site link
  `/alpine.html`, and the root `+page.svelte` links `/alpine.html`. nginx
  serves `alpine.html` statically and redirects the bare path
  (`location = /alpine` → `return 301 /alpine.html`); the `/` route redirect
  is `location = /` → `return 302 /alpine.html`. **Verify the actual static
  output path of the pinned webvm commit at implementation** (if a pinned
  commit changes `trailingSlash` and emits `alpine/index.html`, flip the
  redirects to match).

#### Step 5 — Build the frontend (reproducible)
`cd webvm` → **rewrite the `labs` dependency URL to HTTPS *before* the first
`npm ci`** and regenerate + commit the `package-lock.json` together with the
rewritten `package.json` (a lockfile that predates the rewrite fails `npm ci`
integrity checks) → `npm ci` (committed lockfile, exact cheerpx version) →
`npm run build` → `webvm/build`; copy the `.ext2` into
`webvm/custom-disk-images/`. **The build must be deterministic and offline-safe
apart from npm:** after the Step 4 edits the prerender no longer fetches
`labs.leaningtech.com` blog posts, so `npm run build` has no external runtime
or build-time dependency beyond the pinned npm packages.

#### Step 6 — Server container (`server/`, via `compose.yaml`)
- Define the single `server` service in `compose.yaml` with all options inline
  using `${VAR:-default}` defaults (**no `.env` required for `browser`/`none`**):
  - `build: { context: . , dockerfile: server/Dockerfile, args: { STORAGE_BACKEND: ${STORAGE_BACKEND:-browser}, WEBDAV_ROOT: ${WEBDAV_ROOT:-/data/webdav} } }`
    (**REVISED at implementation: the context is the repo root** so the
    Dockerfile can `COPY webvm/build/` and `webvm/custom-disk-images/` — the
    served `alpine.html` and the ext2 must be inside the server image)
  - `ports:` published on `"${LAN_IP:-127.0.0.1}:${SITE_PORT:-8081}:${SITE_PORT:-8081}"`
    (same pattern for `CONTROL_PORT` 8443 and, in webdav mode, `WEBDAV_PORT`
    8082; plus the **UDP STUN port**
    `"${LAN_IP:-127.0.0.1}:${STUN_PORT:-3478}:${STUN_PORT:-3478}/udp"` — the
    container side uses the same env variable so arbitrary remapping works,
    and `EXTRA_BIND_IP` was dropped (§12/21); the embedded
    DERP requires `stun_listen_addr` **configured** (headscale refuses to start
    without it); publishing it is optional and only ever used by the gateway —
    the browser client has no UDP socket, so relay-only is the steady state,
    and the gateway can reach STUN over the compose network at
    `172.28.0.10:3478` without any host publish (keep the publish as harmless
    extra). See §5 for the duplicate-bind guard when `LAN_IP` is loopback).
    Containers listen on `0.0.0.0` internally; exposure is restricted by the
    published bindings.
  - **Always-published ports:** compose publishes `CONTROL_PORT`/`STUN_PORT`
    (and `WEBDAV_PORT`) even in `browser`/`none` builds where headscale/wsgidav
    do not run — the listeners simply refuse connections (harmless, keeps the
    compose file static); note it in the README so a refused port is not
    mistaken for a broken stack.
  - `networks:` a **fixed-subnet user-defined network** (e.g. `172.28.0.0/16`)
    with a **static `ipv4_address` for `server`** (e.g. `172.28.0.10`) so the
    `gateway` reaches the control plane at that IP directly
    (`GATEWAY_CONTROL_IP`; REVISED Round 11 — no `extra_hosts` hostname
    mapping, §5.2) — no host interface involved.
  - `volumes:` `./certs:/certs`, a **named volume for headscale's state
    (`/var/lib/headscale`)** — its SQLite DB holds the preauth keys and the
    gateway's node record (and therefore its allocated tailnet IP), which is
    what keeps the guest URL and `GATEWAY_TAILNET_IP` valid across container
    recreation (§5.4 — headscale has no fixed-IP mechanism; persistence is the
    mechanism) — and (webdav mode) `${DATA_DIR:-./data}:${WEBDAV_ROOT:-/data/webdav}`
  - `environment:` `CONTROL_HOST`, `LAN_IP`, `SITE_PORT`, `CONTROL_PORT`,
    `WEBDAV_PORT`, `STUN_PORT`, `WEBDAV_ROOT`, `WEBDAV_USER`, `WEBDAV_PASS`,
    `HEADSCALE_PREAUTHKEY`, `GATEWAY_AUTHKEY`, `HEADSCALE_ENABLED`,
    `GATEWAY_TAILNET_IP`. **Secrets are optional at compose level (`${VAR:-}` —
    never `${VAR:?err}`, which would block `browser`/`none` builds and CI's
    `docker compose config -q`); the entrypoint enforces them fail-closed per
    mode** (§6 entrypoint).
  - Pin image tags for any external images; optionally group shared values in
    an `x-server-config:` extension block (YAML anchors) reused by
    `environment:`/`ports:`/`build.args:`.
  - Secrets (Headscale keys, WebDAV auth, Samba creds) must NOT be committed to
    the public repo — they come from an optional `.env` or the shell, and are
    only required in the modes that use them (Step 10).
- **nginx** — two HTTPS `server` blocks inside the container (both bind
  `0.0.0.0`):
  - **`SITE_PORT` (8081, the site):** site from `build/` plus a dedicated
    `location /custom-disk-images/` for the ext2 image (Step 4's
    `diskImageUrl`) using an **explicit absolute `root`/`alias`** (e.g.
    `alias /srv/webvm/custom-disk-images/;` — do NOT rely on nginx `root .`,
    which resolves against the nginx prefix directory and 404s unless nginx
    runs with a pinned `-p` matching the layout); TLS cert from `/certs`;
    COOP/COEP/CORP; **`Content-Security-Policy` (REVISED at implementation —
    the full directive set, not just `connect-src`):**
    `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:;
    worker-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; style-src 'self'
    'unsafe-inline'; img-src 'self' data:; font-src 'self' data:;
    connect-src 'self' https://${CONTROL_HOST}:${CONTROL_PORT} wss://${CONTROL_HOST}:${CONTROL_PORT}`
    — `connect-src` is still the only external host list (it blocks the
    compiled-in logtail fetch, §5); `'unsafe-eval'` and `blob:` are required by
    CheerpX (its x86→WASM JIT evaluates strings and spawns `blob:` workers)
    and the self-hosted runtime modules (note the CSP does not restrict
    `img-src`'s cross-origin uses, so the removed blog-post `og:image` URLs
    must be gone from the build, not merely CSP-blocked)**; gzip on for
    JS/WASM + `Cache-Control: max-age=31536000, immutable` on `/cheerpx/` and
    `/_app/immutable/` (the security `add_header`s are repeated inside those
    locations — nginx does not inherit them); gzip off for `.ext2`; serve the
    static `alpine.html` file and redirect the bare path to it
    (`location = /alpine` → `return 301 /alpine.html`; **the SvelteKit
    adapter-static output is `build/alpine.html`, not `alpine/index.html` —
    verified against the upstream `deploy.yml`**);
    `location = /` → `return 302 /alpine.html`
    (the stock Debian `/` route must never load — it fetches a public image).
  - **`CONTROL_PORT` (8443, control + DERP):** **REVISED at implementation
    (§12/21(c)):** a single catch-all `location /` reverse-proxies the entire
    listener to Headscale (`proxy_pass http://127.0.0.1:8080;` — no prefix
    stripping; `server_url` is **path-less**, so `/key`, `/ts2021`,
    `/machine/register` and `/derp` are all root paths), with WebSocket upgrade
    headers (`proxy_set_header Upgrade $http_upgrade; proxy_set_header
    Connection $connection_upgrade;`), `Host`, `True-Client-IP`/X-Forwarded
    headers, `proxy_buffering off;` and generous `proxy_read_timeout` —
    headscale's DERP handler answers `426 Upgrade Required` and its TS2021
    handler `500` to any non-upgraded request.
- **CORS (verified at implementation, §5/§12/21(d) + networking-bug.md
  §15.2.1):** the browser-side Tailscale client fetches the control plane's
  **`/key` endpoint cross-origin**, and headscale answers `/derp/probe` ONLY
  with `ACAO: *` — every other control-plane response lacked it, so the
  fetch was CORS-blocked and the tailnet never started. The 8443 listener
  therefore adds **`Access-Control-Allow-Origin: $http_origin` + `Vary:
  Origin`** (LAN-only personal control plane; non-credentialed fetch),
  plus `proxy_hide_header Access-Control-Allow-Origin;` (headscale's `*` on
  `/derp/probe` must not be echoed alongside it — `MultipleAllowOriginValues`)
  and **`Cross-Origin-Resource-Policy: cross-origin`** belt-and-braces.
  WebSockets (`/ts2021`, `/derp`) are not subject to CORS.
- **Server container base (REVISED at implementation):** built on
  **`python:3.11-alpine`** with `nginx` installed from its repos — the
  `nginx:alpine` python3/libexpat pair breaks `pip`/`pyexpat` entirely
  (a relocated-symbol error on `import pyexpat`), so the pinned `nginx`
  image cannot host wsgidav. `apache2-utils` (htpasswd) and `gettext`
  (envsubst) are added.
- **wsgidav** (webdav mode only, 8082): `WEBDAV_ROOT` (default `/data/webdav`)
  backed by the volume; basic auth from `webdav.htpasswd`, which the entrypoint
  generates **from `WEBDAV_USER`/`WEBDAV_PASS`** — the entrypoint **aborts
  (fail-closed) in webdav mode if either is unset/empty** — "never a random
  password, or the sync agent's credentials would not match"; PROPFIND/LOCK/
  PUT/GET enabled; **REVISED at implementation:** the pinned wsgidav 4.3.x
  config schema is **flat** — `server: "cheroot"` (a type string, not a dict),
  top-level `host`/`port`, `provider_mapping`, `simple_dc.user_mapping`,
  `dir_browser`; `max_request_body_size` is not a wsgidav key, and `cheroot`
  is an extra pip install. Rendered
  by the entrypoint from a template.
- **Headscale** `config.yaml` (schema of the **pinned version** — several keys
  have changed across headscale releases, so verify each against the pinned
  version at implementation): `server_url:
  https://${CONTROL_HOST}:${CONTROL_PORT}` (**REVISED at implementation —
  PATH-LESS; §12/21(c): the Noise register path carries the `server_url` path
  verbatim and v0.29.3's noise router serves it at the root, so a `/headscale`
  base path 404s registration. The DERP-map relay URL headscale builds from
  `server_url` is `https://${CONTROL_HOST}:${CONTROL_PORT}/derp` (confirmed at
  runtime), which is why nginx serves the root**); the embedded
  DERP enabled with **`stun_listen_addr: "0.0.0.0:${STUN_PORT:-3478}"`**
  (mandatory — headscale refuses to start the embedded DERP without it) and
  the region's `region_id`/`region_code` per the pinned schema;
  **`derp.server.ipv4`/`ipv6` must be set to empty or real addresses — do NOT
  copy the config-example's `198.51.100.1`/`2001:db8::1` TEST-NET placeholders,
  which headscale advertises verbatim in the DERP map**;
  `derp.server.verify_clients` stays at its default `true` (only known nodes
  may relay — desired for LAN confinement); `derp.urls: []` (no public
  Tailscale DERP), `dns.magic_dns: false`, `disable_check_updates: true`, no
  exit node, TLS behind nginx
  (`tls_cert_path: ""`, `listen_addr: 127.0.0.1:8080`). **There is no `cors`
  config option in headscale — do not look for one.** **State:** headscale's
  SQLite DB lives on a **named volume (`/var/lib/headscale`)** so the preauth
  keys and the gateway's node record (and therefore its allocated tailnet IP)
  survive container recreation (§5.4). Pre-auth keys: `headscale users create
  headscale` (once),
  then the **admin creates two long-lived reusable keys once** with
  `headscale preauthkeys create --user <id> --reusable --expiration <long>`
  (**REVISED at implementation — v0.29.x takes the NUMERIC user id**, not the
  name: `headscale users list` shows it and the first user is `1`; §12/21)
  (e.g. `100y` — **a short 180 d expiry is unnecessary** for a personal LAN key
  and would silently break the saved URL; headscale generates the key values)
  and **records them in `.env`** as **`HEADSCALE_PREAUTHKEY`** (browser nodes
  are ephemeral) and **`GATEWAY_AUTHKEY`** (the **`gateway` node** joins with
  its own separate key — a non-reusable key is consumed on first join and
  would block a recreated gateway container; the gateway's tailscaled state
  lives on a named volume regardless).
- **entrypoint.sh**: **fail-closed per mode** — if the control plane is needed
  (`STORAGE_BACKEND=samba|webdav` or `HEADSCALE_ENABLED=1`) and
  `HEADSCALE_PREAUTHKEY` is empty, abort with a clear message (likewise
  `GATEWAY_AUTHKEY` when the `gateway` service is used, and
  `WEBDAV_USER`/`WEBDAV_PASS` in webdav mode); `browser`/`none` need no
  secrets. Then `envsubst` with an **explicit variable list**
  (e.g. `envsubst '$CONTROL_HOST $LAN_IP $SITE_PORT $CONTROL_PORT $WEBDAV_PORT
  $STUN_PORT $WEBDAV_ROOT $WEBDAV_USER $WEBDAV_PASS'` — never bare `envsubst`,
  which would mangle `$` in credentials) over the nginx + headscale + wsgidav
  templates → generate `webdav.htpasswd` from `WEBDAV_USER`/`WEBDAV_PASS` if
  missing → **start headscale when needed** (`browser`/`none` sessions run
  nginx only), then ensure the headscale user namespace exists (create once)
  and **verify the `.env` preauth keys are present in headscale's DB** —
  **REVISED at implementation: `preauthkeys list` takes no `--user` flag and
  MASKS keys with `***`**, so the check matches each configured key against
  the listed unmasked prefix; if a key is missing, fail with a clear message
  pointing at `headscale preauthkeys create --user <id>
  --reusable --expiration 100y` (`<id>` from `headscale users list`, first
  user is 1) — headscale generates the key values and the admin copies them
  into `.env`. **First-run bootstrap is a documented two-step sequence:**
  (1) bring the server up once with **`HEADSCALE_BOOTSTRAP=1`** (the
  implementation's override that skips the fail-closed key check on the very
  first run) so headscale creates its SQLite DB, then run
  `docker compose exec server headscale preauthkeys create --user <id>
  --reusable --expiration 100y` twice and record both
  printed values in `.env` as `HEADSCALE_PREAUTHKEY`/`GATEWAY_AUTHKEY`;
  (2) restart the server (and start the gateway) — the entrypoint's check
  then passes. CI performs step (1) in a one-off container before
  `docker compose up` (§8.3). Headscale runs with `restart: unless-stopped`
  + a healthcheck so a headscale crash doesn't silently kill networking →
  nginx → (webdav mode) wsgidav. **Implementation fix (CI bootstrap race):**
  headscale's socket file appears before its RPC/DB layer is ready, so an
  immediate `preauthkeys create` can fail with "user not found" (the error
  goes to **stderr**); the CI bootstrap therefore **retries key creation
  until a real `hskey-auth-*` value is returned** (never an empty key, which
  would crash-loop both the server and gateway), and the entrypoint **retries
  `headscale users create` until the namespace is listed** (2026-08-14).

#### Phase 1 validation
`make build && make up`, open **`https://127.0.0.1:<SITE_PORT>/alpine.html`**
(the private CA must be trusted in the browser once — single-machine and LAN
use share this single trust step; no plain-HTTP path exists) **with no
`authKey`/`controlUrl` params** (a disconnected session — no tailnet at all),
confirm the desktop boots to the file manager on `~/` (IDLE opens on demand),
and a file in `~/` survives a reload (`browser` mode) or does not (`none`
mode). **Never add `#authKey=…`
without a `controlUrl`:** WebVM then auto-registers with *Tailscale's public
control server*, which is both an internet egress attempt and a tailnet-join
you do not control.

### Phase 2 — Control plane + gateway + relays

**Phase 2 requires the tailnet stack to be running** — bring the stack up with
`STORAGE_BACKEND=webdav` (or `HEADSCALE_ENABLED=1` + `make up-tailnet`);
`browser`/`none` builds don't start Headscale or the gateway.

#### Step 7 — Gateway service + TCP relays (no host installs)
A `gateway` compose service runs both `tailscaled` and `socat` in one
container, built from a small `gateway/Dockerfile` on top of the official
`tailscale/tailscale` image (**pinned tag**) with **`socat` added** — socat
**must** share the gateway's network namespace with tailscaled (its relays
bind `127.0.0.1`, where tailscaled delivers inbound tailnet-IP connections),
so a separate socat container would break the design. The service is behind
**`profiles: ["tailnet"]`** and is started only for `samba`/`webdav` builds
(`make up-tailnet`) or explicitly for Phase 2 validation —
`browser`/`none` builds do not run it:
- `tailscaled --tun=userspace-networking` joined to Headscale:
  `--login-server https://${GATEWAY_CONTROL_IP}:${CONTROL_PORT} --authkey $GATEWAY_AUTHKEY`
  (a **separate node key** from the browser `HEADSCALE_PREAUTHKEY`; **both keys
  are reusable** so a recreated gateway can rejoin; **no `--advertise-routes`**,
  no exit node). The service mounts a **named volume for tailscaled state**
  (`/var/lib/tailscale`) so the node key survives container recreation. The
  gateway reaches the control plane/DERP at the server's static compose-
  network IP `GATEWAY_CONTROL_IP` (default `172.28.0.10`, cert SAN
  `IP:${SERVER_IP}`; **REVISED Round 11 — no `extra_hosts` hostname mapping,
  hostnames are banned**) — this works on Linux and Docker Desktop alike and
  does **not** depend on the host-published loopback ports (§5.2). The
  netmap's DERP host (derived from the BROWSER-facing `server_url`: `127.0.0.1`
  single machine / LAN IP) is forwarded from the gateway's own loopback via a
  **socat relay on CONTROL_PORT → `GATEWAY_CONTROL_IP:CONTROL_PORT`** — without
  it the gateway can never reach the DERP relay on the single machine (§16.10).
  The gateway **trusts the private CA** via
  `SSL_CERT_FILE=/certs/ca.crt` with `./certs:/certs:ro` (§5.2).
  **No `cap_add: NET_ADMIN` is required in userspace mode** (there is no TUN
  device); grant it only if the fallback TUN path is used.
- **Gateway tailnet IP (corrected):** headscale v0.29.x has **no fixed-IP or
  reservation mechanism** (verified against the CLI and config), so the IP is
  stabilised by persistence — the headscale SQLite DB volume (§6 Step 6) and
  the gateway's tailscaled state volume keep the node record (and its
  allocated IP) across recreations. After the gateway's **first** join, read
  the assigned IP (`headscale nodes list` or `tailscale status` in the
  gateway) and record it as `GATEWAY_TAILNET_IP` in `.env`; the value must be
  the **actual** assigned IP (sequential allocation starts at `100.64.0.1`),
  not a pre-reserved one. If the headscale DB is ever wiped, the IP changes —
  update `GATEWAY_TAILNET_IP` and the baked `syncrc`/remotes/`known_hosts`
  (documented recovery step).
- socat relays **bound to `127.0.0.1`** (tailscaled forwards tailnet-IP:port →
  `127.0.0.1:port`, verified in `netstack.go`); socat runs as **root** in the
  gateway so it can bind privileged ports (`445`, `2222`):
  ```
  socat TCP-LISTEN:445,fork,reuseaddr,bind=127.0.0.1 TCP:${SAMBA_LAN_IP}:445                          # samba mode
  socat TCP-LISTEN:2222,fork,reuseaddr,bind=127.0.0.1 TCP:${GIT_SSH_LAN_IP}:22                       # only when GIT_SSH_LAN_IP is set
  socat TCP-LISTEN:${GIT_HTTP_PORT:-8080},fork,reuseaddr,bind=127.0.0.1 TCP:${GIT_HTTP_LAN_IP}:${GIT_HTTP_PORT:-8080}   # only if smart-HTTP git is used
  socat TCP-LISTEN:${WEBDAV_PORT},fork,reuseaddr,bind=127.0.0.1 TCP:server:${WEBDAV_PORT}             # webdav mode
  ```
  (The gateway's entrypoint wrapper starts each relay only when its `*_LAN_IP`
  env var is set. `WEBDAV_PORT`/`GIT_HTTP_PORT` must be passed to the gateway
  service too, so relayed ports always match the URL-hash/`syncrc` ports.
  **Adding the git SSH relay is a host-side step**: set `GIT_SSH_LAN_IP` in
  `.env` and recreate the gateway; the in-guest `git remote add …:2222`
  happens afterwards. The `GIT_HTTP_PORT` default `8080` numerically matches
  headscale's internal `127.0.0.1:8080` in the *server* container — different
  containers, so no real conflict, but pick a distinct default (e.g. `8083`)
  to avoid confusion.)
- Verify: `docker compose exec gateway tailscale status` shows the node; a
  tailnet peer reaches the relayed ports (§10.2).
- Fallback if in-container `tailscaled` misbehaves on this Docker Desktop:
  install Tailscale on the host (brew) and run the socat relays in the `gateway`
  service as before.
No host packages are required: `openssl`/`curl`/`git` are preinstalled on
macOS, and everything else (e2fsprogs, pytest, shellcheck, yamllint, socat,
tailscaled) runs in containers (Steps 3/6/7, §9.1, §8.4).

#### Step 8 — TLS spike + route re-verification (RESOLVED — kept as the acceptance recipe)
1. Install the private CA in the browser (single-machine uses
   `https://127.0.0.1:<SITE_PORT>` — same CA, same one-time step); confirm the
   **site loads over HTTPS with cross-origin isolation intact**, and the
   guest's network panel shows CONNECTED with a tailnet IP over WSS.
2. CORS/COEP re-check: `/derp/probe` answers ACAO (the 8443 listener also
   sets CORP cross-origin; the ACAO `$http_origin` + `proxy_hide_header`
   rules landed — networking-bug.md §15.2.1/15.2.4).
3. Route check (**backend-aware**, from the guest): the relayed port for the
   running backend must succeed — `nc -z <gateway-tailnet-IP> ${WEBDAV_PORT}`
   in `webdav` mode, `nc -z <gateway-tailnet-IP> 445` in `samba` mode, `nc -z
   <gateway-tailnet-IP> 2222` when a git SSH relay is configured — and
   `nc -z <raw-LAN-IP> 445` must fail (no subnet routes — expected). (`nc` is
   already in the base busybox.) Note the guest's own listen path never
   accepts (§16.9) — the E2E network spec covers both probe directions.

### Phase 3 — Guest sync agents + full validation

#### Step 9 — Guest sync agent (per `STORAGE_BACKEND`)
Read the endpoint config (runtime-injected `/opt/syncrc` if present, else the
baked `/root/.syncrc` — functional out of the box via build args, Step 2).
Cadence: **pull on boot before the desktop starts** (`desktop.start`,
wait-for-tailnet retry ~90 s, best-effort — X starts regardless), then
**push right after writes** (scan `~/` every ~5 s; debounced ~2 s per-file
delta), plus a **final best-effort push on shutdown** (unreliable in a WASM
guest — the write-triggered push is the effective recovery point). **Push
uses the same per-file mtime manifest as pull** — compare backend mtimes
against the local **last-push** record (not wall-clock "now", §4 clock-skew);
a full `~/` tarball is uploaded only when no manifest exists yet.
Concurrency: the browser session guard (§4) serializes live tabs; the agent
additionally acquires a **backend lease** (heartbeat ~15 s, expiry ~90 s)
before enabling sync — refuse to sync if another live session holds it.
**Implementation notes (§12/21):** the manifest covers **subdirectory files**
(recursive WebDAV `Depth: infinity` / SMB tree walk; **MKCOL parent
collections** before nested PUTs, else 409); pull is **non-clobbering**
(first-sync restore skips existing members; per-file pulls never overwrite
pre-existing unrecorded files); remote listings are untrusted — `..`/
absolute paths rejected, `EXCLUDE_NAMES` (`.ssh`, `.cache`, `.syncrc`, …)
not pulled; the agent is a **single process** (pull + push loop, per
networking-bug.md §16.3) started by `desktop.start`.
- **samba mode:** target `//<gateway-tailnet-IP>/<share>` (port 445 relayed)
  via **`pysmb`** (default; `smbprotocol`/`smbclient` only for SMB3 needs —
  §4 Mode B); mtimes via SMB `listPath`/`getAttributes`, not PROPFIND.
- **webdav mode:** target `http://<gateway-tailnet-IP>:<WEBDAV_PORT>/webdav/`
  via Python stdlib (`urllib` PUT/GET/PROPFIND; basic auth); the URL comes
  from the injected `syncUrl`, else the baked `/root/.syncrc` (remapped ports
  then need an image rebuild).
Keep it silent and small.

#### Step 10 — Run & validate
`make build && make up` (= `docker compose build` + `docker compose up -d`) —
options come from `compose.yaml` (inline defaults); edit the compose file to
change them, or create an optional `.env` to override. Secrets are optional at
compose level; the entrypoint enforces them fail-closed per mode: `browser`/
`none` need **no `.env`**; `webdav` mode needs `WEBDAV_USER`/`WEBDAV_PASS`;
`samba` mode needs `SAMBA_LAN_IP`/`SAMBA_SHARE`/`SAMBA_USER`/`SAMBA_PASS`;
tailnet modes need `HEADSCALE_PREAUTHKEY`/`GATEWAY_AUTHKEY` (reusable,
long-lived keys created once with `headscale preauthkeys create` and kept in
`.env`). **Full end-to-end validation (tailnet + sync) runs with a `samba`/
`webdav` build and the `tailnet` profile up** (`make up-tailnet`); open the
**site root** — `https://${CONTROL_HOST}:<SITE_PORT>` (302 → `/alpine.html`) —
which, since Round 8, carries the baked `/webvm-config.js` (authKey/controlUrl/
syncUrl rendered from `.env` at container start) and auto-wires the tailnet
with no hash; the explicit session URL
`https://${CONTROL_HOST}:<SITE_PORT>/alpine.html#authKey=…&controlUrl=https://${CONTROL_HOST}:<CONTROL_PORT>&syncUrl=http://<gateway-tailnet-IP>:<WEBDAV_PORT>/webdav/…`
still works unchanged (any hash overrides the baked config) — with
`CONTROL_HOST=127.0.0.1` for the zero-config single machine (**no /etc/hosts
entry, no hostnames — banned, §12/13**; the gateway's loopback DERP relay
makes the 127.0.0.1 DERP host reachable, §16.10) or
`CONTROL_HOST=<LAN_IP>` (hardcoded LAN address) on a LAN; `browser`/
`none` sessions use
`https://127.0.0.1:<SITE_PORT>/alpine.html` (baked config is `{}` there). The
explicit hash URL (printed by `make url`) carries the preauth and WebDAV
credentials — treat it like a password in terminal scrollback/logs. Use
`docker compose logs -f` and `make down`.

## 8. CI testing (GitHub Actions)

The repo is public, so CI runs on every push/PR. **CI has no secrets or LAN
config**: the guest Dockerfile and scripts must build with placeholders (no
`/root/.syncrc`, no real SSH key; defaults come from build args).
`.github/workflows/ci.yml` has four jobs (all `runs-on: ubuntu-latest`):

1. **guest-image** — `docker/setup-buildx-action` (layer cache; the QEMU
   action is optional belt-and-braces — i386 runs natively on amd64 runners),
   matrix `STORAGE_BACKEND: [browser, samba, webdav, none]`; `build.sh` +
   `debugfs` verification of `/sbin/init`, `/usr/bin/idle3.14`,
   `/home/user/.config/openbox/`, the sync agent, **and ownership**
   (`/home/user` = `1000:1000`, setuid bits — the untar must run as root on a
   container-local path, Step 3); upload the ext2 **and its content
   fingerprint** (`WEBVM_IMAGE_BUILD`) as artifacts.
2. **frontend** — `actions/setup-node` (Node 24, npm cache) + rewrite the
   `labs` dependency to HTTPS **before** `npm ci` (the committed lockfile must
   match the rewritten `package.json` — Step 5) + download the guest-image
   artifact and export its fingerprint as `WEBVM_IMAGE_BUILD` so the built
   cacheId matches the served image + `npm run build`; upload `webvm/build`.
   The build must perform **no external fetch** beyond npm.
3. **server** — `docker compose config -q` (secrets are `${VAR:-}`, passes
   with none set); generate a throwaway private CA + server cert into
   `certs/` first (SAN: `127.0.0.1`, `localhost`, `IP:${LAN_IP}`,
   `IP:${SERVER_IP}` — no hostnames); place the frontend build and the ext2
   where the `server/Dockerfile` consumes them; export throwaway
   `WEBDAV_USER`/`WEBDAV_PASS`/`HEADSCALE_PREAUTHKEY`/`GATEWAY_AUTHKEY`
   secrets; `CONTROL_HOST=127.0.0.1` and `LAN_IP=127.0.0.1` (loopback-safe
   defaults, §5); the gateway and the §9.3 join-test client reach the control
   plane at `GATEWAY_CONTROL_IP` (`172.28.0.10`, cert SAN covered — no
   hostnames) and trust the CA via `SSL_CERT_FILE=/certs/ca.crt`;
   **bootstrap the preauth keys in the fresh headscale DB before the real
   `up`** — start the server once with `HEADSCALE_BOOTSTRAP=1`, run
   `docker compose exec server headscale preauthkeys create --user 1
   --reusable --expiration 100y` twice (v0.29.x takes the **numeric** user
   id; retry until a real `hskey-auth-*` value is returned — the socket
   appears before the RPC/DB layer is ready), and export the printed values;
   smoke test with `STORAGE_BACKEND=webdav` + `make up-tailnet`: `curl -k`
   COOP/COEP/CORP on `/alpine.html` (`/` → 302), ext2 Range → 206, and a
   WebDAV PROPFIND/PUT/GET round-trip with basic auth; the Headscale join
   test (§9.3) runs against this stack.
4. **lint** — `shellcheck` on `build.sh`, `server/entrypoint.sh`, the guest
   sync scripts; `yamllint` on `compose.yaml` and the workflows; via Docker
   images (`koalaman/shellcheck`, `cytopia/yamllint`) — no host installs.

**Notes:**
- The i386 guest build runs **natively** on the amd64 runner (no QEMU needed);
  the main CI costs are the four-backend matrix and image size, mitigated with
  buildx layer caching. On an **Apple Silicon (arm64)** dev machine
  (Docker Desktop 4.85), `linux/i386` runs under the **bundled QEMU/binfmt**
  (verified: `uname -m` → `i686`), so local guest builds are emulated and
  slower than CI; everything else is unaffected.
- The private-CA/TLS and control-plane spike is covered by the Headscale-join
  integration test and the E2E control-plane check; subnet-route acceptance is
  source-resolved (`RouteAll=false`), and only the socat-relay path is
  validated (manual in §10).
- **Tailnet tests need no hostname resolution (Round 11):** the browser
  reaches `CONTROL_HOST=127.0.0.1` directly (no `/etc/hosts` entry); the
  gateway/client containers reach the server's static compose-network IP
  directly; the gateway's loopback relay forwards the netmap's DERP host
  (`127.0.0.1`), so the DERP-map relay URL is reachable from both sides.
- Optionally upload the ext2 as a workflow artifact for manual download; do
  **not** publish it to a package registry.

## 9. Test suite (unit → integration → E2E)

Goal: prove the system "definitely works". Because the VM only truly runs in a
browser, the suite is layered — fast deterministic checks first, a real browser
E2E as the gate — plus a LAN acceptance script for what CI cannot reach. CI jobs
live in `tests/` (wired into §8; layout in `tests/README.md`); run everything
locally with `make test` and `make acceptance`.

### 9.1 Unit tests (CI, fast) — `tests/unit/`
- **Sync agent** (`sync.py`): snapshot create/extract; non-destructive pull
  (per-file mtime manifest vs the **last-push record**); change detection +
  debounced push; lease acquire/refresh/expiry + single-session refusal;
  endpoint config parsing (`/opt/syncrc` vs baked); WebDAV PUT/GET/PROPFIND
  against a fake-server fixture; SMB behind an interface mock.
- **Templates/entrypoint**: rendered nginx/Headscale/wsgidav configs — COOP/
  COEP/CORP, the CSP `connect-src` (`'self'` + control host only), LAN-bound
  ports, the path-less catch-all control proxy with WebSocket upgrade headers,
  the site redirects, `server_url`/DERP addresses from `CONTROL_HOST`, and the
  per-mode fail-closed secret checks. **Plus the hostname-ban tripwire**
  (`test_control_host_defaults_consistent`) and the CORS templates test.
- Frontend session guard (lock acquire/contention/heartbeat), script hygiene
  (`sh -n`, `py_compile`). pytest via a compose `test-unit` service — no host
  Python packages needed.

### 9.2 Rootfs smoke tests (CI) — `tests/rootfs/`
Run against the built guest image and ext2 (i386 runs natively on the amd64
runner): `import tkinter, idlelib` succeeds and **`/usr/bin/idle3.14`
exists**; `xterm openbox xprop git ssh nc` present and **`pcmanfm`/
`spacefm` absent** (Tk file explorer, §12/25); **curriculum packages absent**
(`import numpy/requests/pytest` fail; `pip` succeeds); openbox autostarts
`open-file-explorer.sh` + `keep-file-explorer.sh`; `desktop.start` starts the
sync agent as a single process (samba/webdav) and runs the boot-pull before X.
**In-guest GUI suite** under an in-image `Xvfb`: `file-explorer-tests.py`
(121 checks incl. the disable→IDLE→re-enable flow and the own-window
exclusion regression), a real IDLE launch, and
the keep-alive relaunch check (§12/25). Ext2: `e2fsck -f` clean; `debugfs`
shows `/sbin/init`, `/usr/bin/idle3.14`, the openbox config, the sync agent.

### 9.3 Server integration tests (CI) — `tests/server/`
`docker compose config -q`; stack up; assert COOP/COEP/CORP over HTTPS,
`/alpine.html` → 200, `/` → 302, ext2 Range → 206, control listener
`/derp/probe` ACAO (and the `MultipleAllowOriginValues` guard, networking-bug
§15.2.4), and (webdav) a PROPFIND/PUT/GET round-trip with basic auth.
**Headscale join test** (`join-test-client.sh`): a `tailscaled` client
container trusting the test CA joins via
`https://${GATEWAY_CONTROL_IP}:${CONTROL_PORT}` (`172.28.0.10`, cert SAN
covered — no hostnames), registers, and reaches a second node via the
embedded DERP. Assert no exit node is advertised anywhere.

### 9.4 E2E tests (Playwright; CI + local) — `tests/e2e/`
The definitive check — boot the real VM in headless Chromium against the
running server (`ignoreHTTPSErrors: true` for the private CA):
- **boot**: HTTPS + cross-origin isolation intact; canvas non-blank within a
  generous timeout; no console errors (logtail CSP warnings are allowlisted —
  the assertion is "no *successful* external request"); the `browser`-mode
  case opens with **no `authKey`/`controlUrl`** (no auto-login attempt).
- **no-egress**: intercept all requests **and WebSockets** (`page.on('websocket')`
  — the control connection is a WS `page.route` does not see) and assert every
  URL is same-origin or the control host/port; no logtail success; no
  plausible/fonts/serviceWorker/blog images/Claude tab; `/` → `/alpine.html`.
- **control plane**: `headscale nodes list` gains a new ephemeral node.
- **sync/network (webdav)**: `webvm.lock` + a `~/` snapshot appear on the
  backend within ~2 min, reload pulls; the network spec runs the user's exact
  root-visit acceptance (baked config → lease → `cjTailscale` nc-twin connect
  + HTTP round trip → listen-twin bind+listen; §16.9 accept-path limitation).
- **error overlay** (`webvm-test-bootfail`/`webvm-test-trapreport` hooks):
  exact reason shown, Reload recovers. **persistence**: browser mode survives
  a reload (IDB/OPFS per §2); none mode does not. **single-session guard**:
  second tab shows the ephemeral notice and never writes the shared overlay.
- Matrix over `[browser, webdav]` (samba/none logic is unit + rootfs; Samba
  E2E needs a live Samba server → §10). CI: retries + long timeouts; this job
  is the gate.

### 9.5 Local / LAN acceptance — `scripts/acceptance.sh`
Semi-automated, run on the LAN host after `make up`; covers what CI can't:
private-CA browser trust for the **site and control plane**, socat relay
reachability from the guest, Samba share connect via the relay, the no-internet
proofs (public IP unreachable, raw LAN IP unreachable, no exit node), port-remap
round-trip, host firewall checks — plus a printed checklist for the visual
items (desktop renders, IDLE usable, canvas resize).

## 10. Manual & LAN acceptance

Manual complements to the automated suite in §9 (run via
`scripts/acceptance.sh`): site access paths (`https://127.0.0.1:<SITE_PORT>`
and `https://<LAN_IP>:<SITE_PORT>`, CA trusted once per browser); **no
internet** proofs (DevTools zero external hosts + blocked logtail; guest
cannot reach a public IP or a raw LAN IP; no exit node anywhere; never
`#authKey` without `controlUrl`); guest CONNECTED with a tailnet IP over WSS;
desktop (explorer auto-opens, `.py` → IDLE, keep-alive relaunch, resize);
**storage sync per backend** (samba push/reconnect; webdav push + host-side
WebDAV client + alternate `WEBDAV_ROOT`; both: reload → pull restores `~/`,
a save in IDLE is pushed within seconds); browser/none persistence behavior;
concurrent-tab single-session notice; **git** (host-side `GIT_SSH_LAN_IP`
relay first, then in-guest `ssh://git@<gateway-tailnet-IP>:2222/<path>` clone/
pull/push); image size + first-load time ≤ 2 GB; **port remapping** in
compose/.env with no image rebuild (URL-hash + `syncrc` ports must match the
gateway relays).

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|---|
| Browser Tailscale client ignores LAN subnet routes (source-verified: `RouteAll=false`) | socat TCP relays on the gateway (tailnet IP → LAN ports); join a machine to the tailnet if a port can't be relayed |
| Gateway inbound delivery in userspace mode | Source-verified: tailscaled forwards tailnet-IP:port → `127.0.0.1:port` (`netstack.go`); socat binds `127.0.0.1`; Step 8 re-verifies with `nc`; host-tailscale fallback documented |
| Browser/CA trust: private-CA site + control endpoint rejected | Import the CA in the browser once — required for **both** `https://127.0.0.1:<SITE_PORT>` and LAN use (no plain-HTTP path exists) |
| **Site over plain HTTP on a LAN IP → no SharedArrayBuffer → VM won't boot** | Site always served over HTTPS with the private CA — the only access mode; verified by E2E boot over HTTPS and acceptance |
| nginx `dav_module` lacks PROPFIND (sync agent needs it) | **wsgidav** (PROPFIND/LOCK/auth); integration test covers PROPFIND/PUT/GET |
| IDLE packaging: `python3-idle` depends on `python3-tests` (~85 MiB) and ships only `/usr/bin/idle3.14` | Extract `idlelib`/`idle3.14` from the package (`apk fetch` + `tar`, Step 2) |
| X fails to bootstrap without a display manager (DRM/input perms, VT handling) | udev + `video`/`input`/`tty` groups; `XDG_RUNTIME_DIR` + dbus session; **Xorg launched as root** with `-novtswitch`, user session via `~/.xinitrc` (§12/22 — the "start X as `user`" note in the original steps is superseded) |
| Gateway tailnet IP changes on rejoin (breaks URLs/remotes/known_hosts) | headscale has **no fixed-IP mechanism** (verified); persistence (headscale SQLite DB + gateway state volumes) keeps the node record/IP stable; record the **actual** assigned IP as `GATEWAY_TAILNET_IP`; document the recovery path if the DB is wiped (§12/9) |
| Two VM tabs sync concurrently → last-writer-wins data loss / shared IndexedDB corruption | **Browser-level single-session guard** (localStorage+BroadcastChannel, all modes, throttling-safe via BroadcastChannel pings) + backend lease for sync agents + non-destructive pull (mtime manifest vs last-push record); one-session-at-a-time usage documented |
| Rebuilt ext2 leaves stale IndexedDB overlays (deltas against an old base → corrupt FS) | **CacheId versioned to the image build** (`blocks_alpine_<image-build>`); upgrades start fresh overlays (§4/Step 4) |
| `#authKey` in the URL without a `controlUrl` auto-registers with **public Tailscale** | Never ship/use `#authKey` alone; E2E + acceptance assert this |
| Unpinned CheerpX/WebVM/Headscale/Tailscale drift breaks the integration | Pin webvm commit, exact `@leaningtech/cheerpx`, committed lockfile, image tags; `labs` dep rewritten to HTTPS for CI; the §12/21 checklist is re-run per bump (update-to-latest.md) |
| Tailscale logtail telemetry from the browser | **Blocked, not permitted**: nginx CSP `connect-src` allows only `'self'` + the control host (and the rebuilt wasm client's uploads are removed at source); host firewall drops egress beyond RFC1918; logtail failures are non-fatal; E2E asserts **zero** external hosts |
| Accidental internet exposure | No exit node; **`derp.urls: []` so no public DERP**; ports published to LAN IP + loopback only; host pf firewall; DevTools egress check; stock frontend external refs stripped/redirected (Step 4) |
| Gateway container cannot reach the control plane/DERP over the host's loopback | **Fixed (Round 11):** gateway reaches the control plane at the server's static compose-network IP (`GATEWAY_CONTROL_IP` = `172.28.0.10`, cert SAN covered) + a loopback socat relay on CONTROL_PORT forwards the netmap's DERP host (127.0.0.1 single machine) — no hostnames (networking-bug.md §16.10) |
| Guest inbound accept path is dead in the rebuilt wasm client (§16.9) | Runtime limitation: guest LISTEN services never accept; the E2E listen-twin asserts bind+listen ONLY; IDLE's launcher gates subprocess mode on a loopback round trip (display-bug.md §2.11); revisit if the wasm client is rebuilt |
| Gateway auth key consumed on first join → recreated gateway can't rejoin | `GATEWAY_AUTHKEY` reusable + named volume for tailscaled state (§12/12) |
| Headscale DB loss invalidates preauth keys and the gateway IP assignment | SQLite DB on a named volume; on DB loss, re-register the gateway, read its new IP, update `GATEWAY_TAILNET_IP` + baked `syncrc`/remotes/`known_hosts`; preauth keys are **long-lived** (`100y`) so saved session URLs don't silently expire |
| Two profiles/browsers on one machine bypass the browser-level guard (per-profile storage) | Documented: one live session per profile; samba/webdav are additionally arbitrated across machines by the backend lease (§4). **Different site origins are separate sessions** (`https://127.0.0.1:<SITE_PORT>` vs `https://<LAN_IP>:<SITE_PORT>`): separate overlays and guards, one session per origin per profile |

## 12. Resolved decisions (open questions closed 2026-08-09)

1. **Default storage backend:** `STORAGE_BACKEND=browser` (default confirmed).
   Samba and webdav remain optional modes; samba is wired with deploy-time
   inputs.
2. **Samba inputs:** deploy-time inputs in `.env.example` (`SAMBA_LAN_IP`/
   `SAMBA_SHARE`/`SAMBA_USER`/`SAMBA_PASS`), passed to the guest image build
   as `build.sh` args so the baked `/root/.syncrc` is functional; the runtime
   `/opt/syncrc` injection overrides it without a rebuild.
3. **WebDAV:** `WEBDAV_ROOT=/data/webdav` on `${DATA_DIR:-./data}`; credentials
   via `WEBDAV_USER`/`WEBDAV_PASS` at deploy (htpasswd generated fail-closed).
4. **Relayed services:** samba `445` (samba mode), webdav `<WEBDAV_PORT>`
   (webdav mode); a git SSH relay (`2222`) is added **by the administrator on
   the host** (`GIT_SSH_LAN_IP` in `.env`, then recreate the gateway) when a
   git remote is wanted; LAN IPs come from `.env`.
5. **Sync cadence:** pull-on-boot (before X, wait ≤ 90 s, best-effort),
   **write-triggered push** (the agent scans `~/` every ~5 s and pushes a
   debounced ~2 s delta after changes — right after writes), final best-effort
   push on shutdown.
 6. **Git:** tooling only — no preconfigured remotes; remotes are added in-guest
    later, pointing at the recorded gateway tailnet IP through a `2222` relay.
7. **Host ports:** defaults `SITE_PORT=8081`, `CONTROL_PORT=8443`,
   `WEBDAV_PORT=8082` accepted; arbitrary remapping supported via `.env` and the
   runtime URL hash (§10.9). **No privileged scheme-default WSS port (443)
   needed since 2026-09-01** — the page glue re-inserts the control port into
   the wasm client's port-dropped URLs, so the control plane (and DERP) are
   reached on `CONTROL_PORT` alone; the gateway publishes no host ports
   (§12/21 item 38).
8. **Pinned versions:** webvm commit SHA, exact `@leaningtech/cheerpx`,
   Headscale tag, `tailscale/tailscale` tag — looked up and recorded in the
   repo at implementation time (a lookup-and-record step, not a design
   decision).
9. **Gateway fixed IP (corrected 2026-08-09):** headscale v0.29.x has **no
   fixed-IP/reservation mechanism** (verified against the CLI and config), so
   the IP is stabilised by persistence — headscale SQLite DB + gateway
   tailscaled state on named volumes — and `GATEWAY_TAILNET_IP` is recorded as
   the **actual** IP assigned at the gateway's first join (`headscale nodes
   list` / `tailscale status`), not a pre-reserved default (sequential
   allocation starts at `100.64.0.1`). Keep a §12/21 checklist item to re-check
   for a fixed-IP feature in the pinned headscale version.
10. **cacheId versioning:** `blocks_alpine_<image-build>`, where `<image-build>`
    is a **content-stable fingerprint** computed by `build.sh` — the SHA-256 of
    the deterministic inputs that define the guest image (the `diskimage/`
    Dockerfile + rootfs/scripts/config/sync tree, the resolved `apk` package
    list, `STORAGE_BACKEND`, and the sync-agent build args) — **not** the raw
    ext2 bytes, which embed `mkfs.ext2` timestamps and a random UUID and would
    therefore churn the cacheId on every content-identical rebuild and orphan
    the browser overlay for no reason. The fingerprint changes whenever the
    image content actually changes, so a real upgrade starts a fresh overlay
    while no-op rebuilds keep it.
11. **smart-HTTP git:** not used (SSH-tooling-only path); the `GIT_HTTP_PORT`
    relay remains available if a smart-HTTP remote is preferred later.
12. **Headscale keys:** `HEADSCALE_PREAUTHKEY` reusable + **long-lived**
    (e.g. `100y` — a short 180 d expiry is unnecessary for a personal LAN key
    and would silently break the saved URL), created once with
    `headscale preauthkeys create` and kept in `.env`; **`GATEWAY_AUTHKEY` is
    also reusable and long-lived** (a non-reusable key is consumed on first
    join and would block a recreated gateway container — §6/Step 7) and the
    gateway's tailscaled state lives on a named volume; both are **user-provided
    `.env` secrets, optional at compose level** (the entrypoint enforces them
    fail-closed only in the modes that use them). The headscale user namespace
    is created once (`headscale users create headscale`); headscale's SQLite DB
    is on a named volume.
13. **DataDevice spike:** baked `/root/.syncrc` is the guaranteed path **and is
    functional by default** (built from `SAMBA_*`/`SYNC_*` build args;
    placeholders only in CI); runtime `/opt/syncrc` injection is a best-effort
    enhancement — if the spike fails, port/creds remapping requires an image
    rebuild (accepted).
14. **Ephemeral-tab sync arbitration:** in `samba`/`webdav` builds the ephemeral
    fallback tab still carries the sync agent + `/opt/syncrc`; the **backend
    lease** refuses its sync attempts (§4) — the guard need not block the agent.
15. **Default access UX:** **HTTPS everywhere** — single-machine use is
    `https://127.0.0.1:<SITE_PORT>` with the private CA trusted once in the
    browser; LAN/multi-device use is `https://<LAN_IP>:<SITE_PORT>` with the
    private CA on each device. No plain-HTTP path exists (§5).
16. **Compose profiles:** the `gateway` service is behind `profiles: ["tailnet"]`
    and Headscale is started by the entrypoint only when needed
    (`STORAGE_BACKEND=samba|webdav` or `HEADSCALE_ENABLED=1`) — `browser`/`none`
    builds run a nginx-only server with no tailnet processes at all (§6, Step 6,
    Step 7, Phase 2; use `make up-tailnet` for tailnet modes).
17. **Python curriculum (revised):** the guest is **stdlib-only** — `py3-numpy`,
    `py3-matplotlib`, `py3-requests`, `py3-pytest` are removed; only `py3-pip`
    is baked in (§3/10, Step 2).
18. **IDLE provisioning (revised):** `idlelib` + `/usr/bin/idle3.14` are
    **extracted from the `python3-idle` package** (`apk fetch` + `tar`) to skip
    the 85 MiB `python3-tests` dependency; IDLE is launched on demand from the
    file manager (Step 2) — rootfs tests and acceptance use `idle3.14` and the
    `idle3.14-launcher`.
19. **Control-plane hostname (REVISED Round 10 + Round 11):** `CONTROL_HOST`
    (default `127.0.0.1`; `<LAN_IP>` — hardcoded LAN address — for LAN use)
    is the single BROWSER-facing value rendered into headscale
    `server_url`, the baked `controlUrl`/URL-hash `controlUrl`, the nginx
    CSP, and the cert SAN. **HOSTNAMES ARE BANNED** (no `host.docker.internal`,
    no `/etc/hosts` entries — Round 11, user mandate; enforced in CI by
    `tests/unit/test_scripts.py::test_control_host_defaults_consistent`).
    The `gateway` container reaches the control plane at the **server
    container's static compose-network IP** (`GATEWAY_CONTROL_IP` =
    `172.28.0.10`, cert SAN `IP:${SERVER_IP}`) and forwards the netmap's DERP
    host (`127.0.0.1` single machine / LAN IP) through a loopback socat relay
    on CONTROL_PORT (§5.2, networking-bug.md §16.10) — so the
    control plane and DERP relay are reachable from both the browser and the
    `gateway` container on Linux and Docker Desktop alike (§3/13, §5, Step 6,
    Step 7).
20. **Tailscale logtail (blocked):** the browser's Tailscale WASM client is
    compiled-in to log to logtail's default endpoint (`wasm_js.go` uses
    `logpolicy.NewConfig(logtail.CollectionNode)`; `logtail.Config.BaseURL`
    defaults to `https://log.tailscale.com`, collection
    `tailnode.log.tailscale.io` — **re-verify the exact compiled-in endpoint
    against the pinned cheerpx/tsconnect build at implementation**). Headscale
    cannot redirect client logging (its config comment says so); the only
    control-plane influence is the netmap field `Debug.DisableLogTail`
    (`logtail.enabled: false` default → compliant clients self-disable), so the
    guarantee "never attempted *or* blocked" holds either way. The plan
    **blocks** the fetch with a CSP `connect-src` header on the site (`'self'`
    + the control host/port only) rather than permitting it — so the "never
    public internet" rule is literally zero external hosts. Logtail failures
    are non-fatal, so the cost is only the loss of browser-side diagnostics
    (§5, §9.4).
21. **Implementation-time verification checklist (2026-08-09):** items that
    depend on the pinned webvm/CheerpX/Headscale/Tailscale versions and must be
    confirmed at implementation, not assumed: (a) the CheerpX networking API is
    still only the Tailscale `networkInterface` (no custom WebSocket proxy —
    re-verified against the CheerpX 1.2.8 docs, §2); (b) the headscale config
    keys for the embedded DERP (`derp.server.*`, STUN) and `dns.magic_dns`/
    `trusted_proxies` against the pinned version's schema (**verified against
    v0.29.3's config-example.yaml — the template uses the confirmed keys**);
    (c) the DERP-map relay URL headscale derives from `server_url` (expected
    `…/derp` at the root). **VERIFIED (v0.29.3): `server_url` must be
    PATH-LESS.** The tailscale client posts `/machine/register` over the Noise
    channel using the `server_url` path verbatim, and headscale's noise-internal
    router serves `/machine/register` at the root — a `/headscale` base path
    404s node registration. nginx therefore proxies ALL of `:CONTROL_PORT` to
    headscale (catch-all `location /` with WebSocket handling), and the
     controlUrl/login-server carry no path; the embedded-DERP relay URL is
     `https://${CONTROL_HOST}:${CONTROL_PORT}/derp` (confirmed: with the
     `127.0.0.1` default the DERP map
     entry lists `HostName: 127.0.0.1`, `DERPPort: 8443`); (d) the
    "no CORS needed" premise — `/derp/probe` is a **CORS-mode** fetch answered
    with `Access-Control-Allow-Origin: *` (**verified at runtime**), which
    should satisfy COEP; the 8443 listener also sets
    `Cross-Origin-Resource-Policy: cross-origin` as belt-and-braces; (e) the
    overlay device the pinned build uses (`IDBDevice` vs `OpfsDevice`) — drives
    the E2E persistence assertion (§2/§9.4); (f) the SvelteKit output path
    (**verified: `alpine.html`** — re-check only if the pinned commit changes
    `trailingSlash`) and the stock `app.html` external tags (Step 4);
    (g) logtail's compiled-in endpoint and whether the pinned client honours
    netmap `Debug.DisableLogTail`; (h) browser STUN — expected to be
    unavailable (no UDP bridge in the reference WebVM page), so relay-only is
    the steady state; (i) `DataDevice.writeFile` availability and the
    `/opt`-mount path semantics for `/opt/syncrc`; (j) pysmb behavior on
    Python 3.10 against the target Samba share; (k) the guest NIC comes up once
    the browser Tailscale client connects (bake `/etc/network/interfaces` or
    `udhcpc` if not — Step 2/Step 8); (l) headscale has no fixed-IP mechanism
    in v0.29.x — record the actual `GATEWAY_TAILNET_IP` from the gateway's
    first join and re-check the pinned version for a fixed-IP feature;
    (m) gateway control-plane reachability (**REVISED Round 11 — the
    `extra_hosts` hostname mapping was removed**): the gateway uses the
    server's static compose-network IP (`GATEWAY_CONTROL_IP` =
    `172.28.0.10`, cert SAN `IP:${SERVER_IP}`) directly, and a loopback
    socat relay on CONTROL_PORT forwards the netmap's DERP host (127.0.0.1
    single machine) to the server — re-verify the gateway reaches DERP with
    `CONTROL_HOST=127.0.0.1` after any runtime rebuild (§16.10);
    (n) `/derp` nginx proxy passes the WebSocket upgrade headers (426
    regression test); (o) `build.sh` preserves uid/gid through the
    untar/`mkfs.ext2 -d` pipeline (debugfs ownership assertions); and (p) the
    frontend build performs **no** external fetch and the built page contains
    no external `og:image`/asset URLs (blog posts, Claude tab and service
    worker removed, Step 4).
    **Additional findings at implementation (2026-08-09, v0.29.3):** the
    `headscale preauthkeys create --user` flag takes the **numeric user id**
    (from `headscale users list`, first user = 1), not the username;
    `headscale preauthkeys list` has no `--user` flag and **masks keys with
    `***`**, so the entrypoint's key verification matches on the listed
    unmasked prefix; and the `EXTRA_BIND_IP=127.0.0.2` default is **not
    bindable on macOS Docker Desktop** (EADDRNOTAVAIL), so ports are published
    on `LAN_IP` only by default and `EXTRA_BIND_IP` was dropped from the
    compose port list (documented as a no-longer-used option).
    **CheerpX runtime self-hosting (2026-08-09, verified at boot):** the pinned
    `@leaningtech/cheerpx` package is only a thin wrapper that dynamic-imports
    its core from `https://cxrtnc.leaningtech.com/<version>/` — an external
    request the page must never make. The pinned 1.3.7 runtime is therefore
    downloaded at pin time into `webvm/cheerpx/` (committed; regenerable via
    `scripts/fetch-cheerpx-runtime.sh`) and served same-origin from
    `/cheerpx/`; the frontend imports it through `src/lib/cheerpx.js`
     (vite alias), and the CSP allows `script-src 'self' 'unsafe-inline'
     'unsafe-eval' blob:` (CheerpX needs `unsafe-eval` for its x86→WASM JIT and
     `blob:` workers) with `connect-src` still strictly `'self'` + the control
     host. Verified: the booted desktop makes zero external requests.
     **Tk file viewer (2026-08-16, implemented + verified in-guest — see
     plans/future-feature-ideas.md):** `py3-pillow` **9.3.0-r0** and
     `py3-mistune` **2.0.4-r0** from the v3.17 community repo for x86 (i386).
     Verified: `PIL.ImageTk` + the compiled `_imagingtk` extension work under
     the patched `libtcl8.6.so` (exercised by the in-guest Xvfb image test);
     **this Tk 8.6 build's `wm` command has no `class` subcommand** (WM_CLASS
     is not settable — the explorer's watcher detects the viewer by its
     `<name> — Viewer` WM window title); `ImageOps.exif_transpose()` returns a
     **copy that loses `is_animated`/`n_frames`** (capture animation info from
     the opener before transposing, and skip transposing animated GIFs);
     mistune 2.0.4's streaming renderer API cannot emit directly (children are
     rendered to strings before the parent method runs — the viewer walks the
     `AstRenderer` AST instead); `after()` from the viewer's load thread
     requires a running `mainloop()` (tkinter raises "main thread is not in
     main loop" under bare `update()` pumping); Pillow `draft()`+`thumbnail()`
     keep multi-megapixel JPEGs displayable on the emulated i386. Rootfs smoke
     passes on `browser` and `none` backends (`==> rootfs smoke PASS`).
     **Mesa/LLVM GL stack removed (2026-08-21):** the ~246 MiB GL stack that
     `xorg-server`/`xvfb` pull in (`mesa-egl` → `mesa` → `llvm22-libs` 195 MiB
     + `libgallium` 45 MiB) is unused by this guest — Xorg runs with
     `ShadowFB`/`AccelMethod none` (pure software), Tk/xterm/openbox render
     via X11 core, and the only load-time GL references (Xvfb + the glx
     module, ~310 static `gl*`/`glX*` symbols into libGL) are never invoked.
     `diskimage/Dockerfile` now replaces `libGL.so.1` with a 176 KiB no-op stub
     generated at build time (same exported symbol names, `glXGetProcAddress*`
     returning NULL), deletes `libgallium`/`libLLVM`/`libEGL`/`libGLES`/
     `libelf`/`libdrm_*`/`libSPIRV-*` and the GLX + glamor modules, and keeps
     `libgbm` + `libdrm` core (the modesetting driver links libgbm). The apk
     database stays stale for the removed packages (nothing in the guest runs
     apk). Verified in-guest: `tests/rootfs/smoke.sh` PASS on `browser` and
     `webdav` (file-explorer + file-viewer suites `PASS ALL`, real viewer and
     IDLE launches, openbox client list, keep-alive relaunch). `/usr/lib` drops
     365.6 → 119.3 MiB; the webdav ext2 drops 514 → 185 MiB (~219 MiB logical).
     Re-check only if the guest ever needs GL (a GLX-using app).
     **Round-2 size trim (2026-08-21):** a second `RUN` (after the doc/man/apk
     trim) removes components verified unused at runtime: all Python
     `__pycache__`/`.pyc` (regenerated on demand — the guest FS is writable);
     `/usr/share/mime` (only libgio's `g_content_type_guess` reads it — no
     guest binary does, openbox menu has no icons and the explorer sniffs file
     types itself); `/usr/libexec/glycin-loaders` (gdk-pixbuf's dlopen'd
     loaders — nothing decodes images: openbox's Clearlooks theme is
     color-only); the DejaVu Condensed/ExtraLight/MathTeX faces (unreachable
     through `99-webvm-aliases.conf`, which maps only to Sans/Sans Mono/Serif
     regular+bold+italic — Tk font resolution re-verified in-guest);
     `/usr/share/hwdata` (libpciaccess device-name labels only);
     `/usr/lib/girepository-1.0` typelibs (no pygobject/gjs);
     `libepoxy` + `libwayland-client` (orphaned by the GL removal — no
     DT_NEEDED or dlopen users); Tcl extras `itcl`/`thread`/`tdbc*` (tkinter
     never loads them); imlib2 CLI tools; non-Clearlooks openbox themes;
     mesa leftovers (`drirc.d`) and build-time `aclocal`; the fontconfig cache.
     Rootfs 182 → 134.7 MiB; webdav ext2 185 → 137 MiB (~161 MiB logical);
     browser/webdav smoke PASS, 92 unit tests pass. NOT removed (openbox needs
     them): the librsvg/gdk-pixbuf/glycin/cairo/pango/glib image-decoding chain
     — `libobrender` statically NEEDs and calls it for titlebar/theme
     rendering, so unlike GL it cannot be stubbed safely.

22. **X desktop boot (implementation finding, 2026-08-09):** three things
    contradict the Step 2 assumptions; all are corrected in
    `desktop.start`/`config/xinitrc`:
    * `/usr/bin/Xorg` is the **Xorg.wrap security wrapper**, which refuses to
      run the real X server as a non-root, non-console user — and the guest
      has no console login session for `user`, so `su user -c startx` could
      never start X (it silently produced a console `login:` prompt instead).
      The server is therefore started **as root** (exactly how display
      managers — LightDM in the reference image — do it), and the user session
      then runs as `user` via `~/.xinitrc`. The plan's "Xorg refuses to run as
      root" note is wrong for this Xorg: root is the sanctioned launcher.
    * `startx` appends `vt<N> -keeptty` to its Xorg command line and Xorg's VT
      ioctls hang in the VT-less CheerpX guest. The plan's fallback
      (`Xorg :0 -nolisten tcp -noreset`) is now the **primary** launch, plus
      `-novtswitch`, with a socket-wait (`/tmp/.X11-unix/X0`) so the session
      only starts once X is up; X diagnostics go to `/var/log/xorg.log`.
    * Alpine's default `/etc/inittab` spawns six gettys, so the console showed
      a `login:` prompt instead of booting the desktop; gettys are disabled
      (single-user autologin desktop). Two secondary fixes: the system
      `/etc/X11/xinit/xinitrc` EXECs `~/.xinitrc` before it ever reaches
      `xinitrc.d`, so the screen-resize hook is sourced from `~/.xinitrc`;
      and the WM runs under `dbus-run-session` (a per-session D-Bus).

23. **File-manager-first desktop (implementation change, 2026-08-13) —
    SUPERSEDED (2026-08-14):** the autostarted desktop client was first
    switched from IDLE to **pcmanfm** (GTK3 + libfm, keep-alive daemon,
    mimeapps/`idle3.10.desktop`/Templates integration, `shared-mime-info`),
    then **replaced by the Tk file explorer (§12/25)**; the pcmanfm
    integration files and MIME machinery were removed from the image.

24. **Baked-in Python examples (added 2026-08-13; renamed `examples`
    2026-08-26):** the `examples/`
    directory (moved from the repo root into `diskimage/`, so it is in the
    Docker build context) is copied to `/home/user/examples/` at image
    build time and made **read-only in the image** (`chmod 0555` dir / `0444`
    files, owned by `user` — the guest FS refuses writes even through the
    IndexedDB overlay). They are reference material to copy, never to edit in
    place. `diskimage/examples` is added to `build.sh`'s content
    fingerprint, so example-content changes rebuild a fresh overlay. Updated:
    `diskimage/Dockerfile`, `build.sh`, `tests/rootfs/smoke.sh`.

25. **Tk file explorer replaces pcmanfm/spacefm (implementation change,
    2026-08-14):** the GTK file managers are gone from the image. The desktop
    client is now `diskimage/scripts/file-explorer.py` — a stdlib-only
    (`tkinter`) app with a touch pointer model (tap/long-press), search/sort,
    clipboard, zip, rename, move, delete and folder-size columns, written
    because pcmanfm/spacefm (GTK3 + libfm) still carried a startup
    deadlock/stall burden under CheerpX even after §2.9's shims. Installed as
    `/usr/local/bin/file-explorer.py` (+ sibling `file-explorer-tests.py`),
    autostarted by Openbox via the guarded single-instance launcher
    `/usr/local/bin/open-file-explorer.sh`; `keep-file-manager.sh` became
    `keep-file-explorer.sh` (same WM-client-list polling, plus a "relaunch only
    when no explorer process exists" guard so an inert explorer is never
    doubled). Removed from the image: the `pcmanfm`/`spacefm`/`shared-mime-info`
    packages, the pcmanfm/libfm instrumentation and the whole `/trace`
    diagnostic tree (the Tcl/Tk `libtcl8.6.so.patched` fix stays), the
    `mimeapps.list`/`idle3.10.desktop`/`open-with-idle.desktop`/`~/Templates`
    pcmanfm integration, and the `/proc/self/mountinfo` stub in
    `desktop.start`. A starter `/home/user/hello.py` is baked in so a new user
    can double-click into IDLE immediately.
    **Screen replacement (changed 2026-08-26):** "Open with IDLE" (or
    double-clicking a `.py`) launches `idle3.14-launcher` per file and then
    **disables the explorer's UI in-process** (`_set_ui_enabled(False)`) —
    every button, the file list and the menus go inert, and Openbox
    maximizes IDLE over the still-visible window. It no longer withdraws
    itself: the X withdraw round-trip is unreliable under the CheerpX
    runtime (the explorer was observed staying mapped and interactive over
    IDLE) and the re-map on return flickered, so the swap is now pure Tk
    widget state with no X traffic. Every widget also switches to the wait
    pointer (hourglass, `_set_widget_cursor`) while disabled, restored on
    re-enable. **Fix (2026-08-26):** the launched-app watchers
    (`_idle_window_open`/`_viewer_window_open`) now exclude the explorer's
    OWN window (`_other_windows`, matched by `winfo_id` and by title): its
    title "Python File Manager" matches IDLE's "Python …" rule, and since
    the explorer no longer withdraws its window stays in the client list —
    without the exclusion the watcher would wait forever for an IDLE window
    to disappear and the file manager would never re-enable after quitting
    IDLE. A watcher thread decides when IDLE
    is gone by watching the **window manager's client list** (not the
    process): the WM (Openbox) maintains the EWMH `_NET_CLIENT_LIST` root
    property, read in-process by the explorer (`_wm_client_windows`, via
    xprop — no per-poll interpreter spawn; the keep-alive counts the same
    property via the shell `wm-clients.sh`) — under CheerpX
    closing IDLE can leave the `idle3.14` process alive (it waits on its
    Python-shell subprocess, which a running program such as the snake game
    keeps busy), so waiting on the process would never return. The watcher
    waits for IDLE to map, then waits until its window disappears (IDLE
    windows report class `Toplevel` / a "Python … Shell" title; a program
    window like the game's plain `tk` root does not match) or the process
    exits, then **kills everything IDLE spawned** — the launcher, IDLE's shell
    subprocess (found in `/proc` by its `idlelib.run` command line), and any
    children via `pkill -P` (CheerpX does not implement `killpg()`; it is kept
    as a POSIX fallback) — so no stray program window outlives IDLE and blocks
    the re-enable of the file manager. The same disable/re-enable model
    covers the Tk file viewer (`_open_in_viewer`/`_viewer_finished`).
    Closing the explorer (WM close, or the
    Ctrl+W shortcut added for it) exits the process and the keep-alive
    relaunches it. **Touch model
    hardening (2026-08-14):** the release handler no longer drops clicks whose
    release arrives between the old tap and long-press thresholds (CheerpX can
    delay synthetic button releases), and the long-press hold was raised from
    600 ms to 1500 ms so a delayed release still registers as a tap instead of
    firing a spurious context menu. **Testing:** `file-explorer-tests.py` was
    extended to cover every function (sorts, wheel scroll, breadcrumbs, go
    up/to-path, text sniffing, rename/create/delete/batch-rename, zip/unzip,
    status bar, the real open_selected dir/.py/.txt paths, the late-release
    tap, the Ctrl+O/Ctrl+W shortcuts, and the disable→IDLE→re-enable flow
    incl. the own-window exclusion and the wait-pointer restore —
    121 checks) and now runs **inside the guest** as part of
    `tests/rootfs/smoke.sh` under an in-image Xvfb (`xvfb` package added for
     this); the same smoke block runs a REAL IDLE launch and boots Openbox
     under Xvfb to verify the keep-alive relaunches a killed explorer; `tests/unit`
    and CI shellcheck cover the new scripts. The E2E
    (`tests/e2e/tests/desktop.spec.js`) asserts the real-browser boot: no
    login prompt, no boot hang, and the explorer filling the canvas — synthetic
    input into the guest is not driven there because CheerpX's delayed
    release/Meta-key quirks make it non-deterministic (input behaviour is
    covered by the in-guest suites instead). Updated:
    `diskimage/Dockerfile`, `diskimage/config/openbox/`,
    `diskimage/config/xinitrc`, `diskimage/rootfs/etc/local.d/desktop.start`,
    `tests/rootfs/smoke.sh`, `tests/unit/test_scripts.py`,
    `.github/workflows/ci.yml`, `tests/e2e/tests/desktop.spec.js`.

26. **GitHub Pages project website — the VM itself (added 2026-08-14):** a
    second workflow (`.github/workflows/pages.yml`) builds the browser-mode
    guest, **splits the ext2 into 128 KiB chunks** (`<name>.c<hex6>.txt` +
    `<name>.meta` — the `GitHubDevice` protocol, verified by a full boot test
    on a Pages-like static host), builds the frontend with
    `diskImageType="github"` (`webvm/config_public_alpine_github.js`, selected
    via the `WEBVM_DISK_IMAGE` vite alias) and deploys to
    `https://ned14.github.io/webbrowser-python-idle/alpine.html`. Pages cannot
    set COOP/COEP, so a **service worker** (`webvm/static/sw.js`) re-serves
    navigations with the headers; the alpine page registers it and reloads
    once when `crossOriginIsolated` is false (never registered under the local
    nginx). Two later fixes: the **base-path fix** (the runtime import, `sw.js`
    registration and `/alpine.html` link were hardcoded root-absolute — a
    project site lives at `/webbrowser-python-idle/`; `cheerpx.js` now derives
    the site base from its own module URL, `sw.js` registers relatively, and
    the root `+page.svelte` redirects relatively); and the **font robustness
    fix** (`failed to allocate font due to internal system font engine
    problem` on cold chunked reads — the snake example uses an installed
    family, `99-webvm-aliases.conf` maps the common missing families to
    DejaVu, and `desktop.start` warms the font files into the page's block
    cache at boot).

27. **Browser-side tailnet blocked by an upstream CheerpX runtime crash
    (2026-08-15) — RESOLVED 2026-08-15 (see networking-bug.md §16).** The webdav sync E2E
    (`tests/e2e/tests/sync.spec.js`) consistently failed on CI with
    `webvm.lock` never appearing. The full diagnosis is in
    `plans/networking-bug.md` §15 (the crash narrative was largely wrong: the
    tailscale wasm was never even loaded; the core's network-init never
    starts the client) and the fix in §16 (tailscale wasm rebuilt from
    source, app-side driver, guest sync rework). The sync E2E now passes and
    is **no longer gated**. Updated:
    `webvm/cheerpx/tun/tailscale.wasm` (+matching `wasm_exec.js`),
    `webvm/src/lib/network.js`, `webvm/cheerpx/cxcore.js`(unchanged —
    upstream), `diskimage/sync/*`, `diskimage/rootfs/etc/local.d/desktop.start`,
    `server/nginx.conf.template` (CORS, 443 WSS), `server/Dockerfile`
    (headscale 0.28.0 pin — keeps accepting the rebuilt v1.102 client too),
    `tests/e2e/tests/sync.spec.js` (ungated), `tests/e2e/playwright.config.js`
    (host-resolver rule), `tests/e2e/repro-tailnet.mjs` + probe scripts.
    **Follow-up (2026-08-16, networking-bug.md §16.9):** guest `bind(2)`/
    `listen(2)` crashed the core with `TCPServerSocket is not a function`
    (any explicit bind — busybox `nc -z` always binds) because the page's
    `networkInterface` lacked the method; implemented it on
    `webvm/src/lib/network.js` (shared `connectedTcpSocket()` helper, tun
    accept loop) + E2E listen-twin probe in `tests/e2e/tests/network.spec.js`.

28. **Baked page config — visit-the-root networking (2026-08-16).** In
    tailnet modes the server entrypoint renders `authKey`/`controlUrl` (+
    `syncUrl`/`syncUser`/`syncPass` in webdav) into the same-origin
    `/webvm-config.js` at container start (JSON-escaped via the dedicated
    `server/render-webvm-config.py` — never raw envsubst, credentials may
    contain quotes/backslashes/`$`; `tests/unit/test_scripts.py` cross-checks
    its output against `scripts/print-url.sh` so the two renderings cannot
    drift apart), nginx serves it with `Cache-Control: no-store` **and
    `Cross-Origin-Resource-Policy: same-origin`** (without CORP any webpage
    could `<script src>`-read the credentials cross-origin — PNA does not
    cover private→private LAN loads and Safari does not enforce PNA; the
    location's own add_headers suppress the server-level ones), and the
    `app.html` inline script seeds `sessionStorage` from it **only when the
    URL carries no hash**; any explicit hash disables the baked config, so
    saved `make url` URLs and the E2E hash URLs behave exactly as before. The
    explicit state is **sticky per tab** (`webvm-explicit-session` marker in
    sessionStorage, set whenever a hash was present): a later hash-less
    navigation or reload in the same tab never re-arms the baked config —
    important for the E2E persistence reload and for never overwriting a tab's
    explicit sessionStorage params. Bootstrap mode and
    `browser`/`none` render `{}` (disconnected as before). Webdav mode now
    fail-closes on `GATEWAY_TAILNET_IP` too (the baked `syncUrl` needs it;
    skipped during bootstrap — and `HEADSCALE_BOOTSTRAP=1` must therefore be
    KEPT until the IP is recorded: `docker compose up` recreates the server
    whenever `.env` changes, and with BOOTSTRAP=0 + no IP it would crash-loop
    before the gateway ever joins). The webdav CI phase records
    `GATEWAY_TAILNET_IP` **and** flips `HEADSCALE_BOOTSTRAP=0` together, then
    restarts the server **after** the gateway joins, and its boot-spec browser
    case pins a dummy hash (`#e2e`) to stay a disconnected session.
    `server/integration.sh` asserts the config file (200, no-store, content
    per mode). Verified: same-origin script is exempt from COEP
    `require-corp` (no CORP needed for the page's own load — the CORP
    same-origin is the cross-origin-readout guard); the GitHub Pages service
    worker only intercepts navigations, so the missing `/webvm-config.js`
    there is a silent 404 no-op; the CSP `script-src 'self'` admits the
    same-origin script. Updated:
    `server/entrypoint.sh`, `server/render-webvm-config.py`,
    `server/nginx.conf.template`, `server/Dockerfile`,
    `webvm/src/app.html`, `scripts/print-url.sh` (HEADSCALE_ENABLED=1
    browser/none case), `tests/unit/test_scripts.py`, `tests/server/
    integration.sh`,     `tests/e2e` (network.spec.js + shared
    `tests/e2e/lib/desktop.js`), `.github/workflows/ci.yml`, `Makefile`,
    `README.md`, `.env.example`.

29. **Fatal-error overlay — never a silent load/stop (2026-08-16).** The VM
    boot path previously printed failures only into the hidden console
    xterm (behind the display canvas) and a rejecting `cx.run()` was an
    UNHANDLED promise rejection — reloads could "fail to load without any
    diagnostic". Now: `WebVM.svelte` shows a full-screen overlay
    (`role="alert"`, z-index above everything) with the exact error message
    + stack and a working Reload button (falls back to a plain
    `location.reload()` when the block cache never existed), and "Copy
    details". Failure points wired: any `initTerminal` error (imports,
    terminal setup, disk device, `CheerpX.Linux.create` — all propagate to
    an `onMount` catch with phase "boot") and a rejecting `cx.run()` (phase
    "runtime" when a run already completed, "boot" otherwise). The session
    lock in `alpine/+page.svelte` can no longer stall the page on
    "Acquiring session lock…" forever: a lock failure logs, boots an
    EPHEMERAL session (never writes the shared overlay) and shows the exact
    reason in the ephemeral banner. Test-only hook: `webvm-test-bootfail`
    sessionStorage flag forces a boot failure in `initCheerpX` so
    `tests/e2e/tests/error-overlay.spec.js` can assert the overlay content
    and the Reload recovery (7/7 boot-related specs pass locally; network
    spec still green). Removed the now-unused `errorMessage`/
    `unexpectedErrorMessage` from `messages.js` usage. Updated:
    `webvm/src/lib/WebVM.svelte`, `webvm/src/routes/alpine/+page.svelte`,
    `tests/e2e/tests/error-overlay.spec.js`, plans §9.4.

30. **Control-host verdict — 127.0.0.1 REVERTED, /etc/hosts required
    (2026-08-16, late) — SUPERSEDED by (31).** A browser-facing
    `CONTROL_HOST=127.0.0.1` default was implemented, verified against a full
    bisect (server_url flips, heal variants, DB purge, rebuilds) and REVERTED:
    with `server_url=https://127.0.0.1:8443` the page-side adapter probe works
    but the GUEST data path never comes up, while
    `https://host.docker.internal:8443` worked end-to-end — attributed at the
    time to the rebuilt wasm client's netmap/DERP handling of an IP-literal
    DERP host, with no page-side workaround found; single-machine use was
    made to REQUIRE the one-line `/etc/hosts` entry. **Both conclusions are
    superseded by (31)/networking-bug.md §16.10:** the mechanism was the
    GATEWAY's own-loopback DERP unreachability (fixed by the gateway's
    loopback CONTROL_PORT socat relay → `GATEWAY_CONTROL_IP`), and hostnames
    are banned. The two findings that DO stand: the **inbound accept path is
    dead** in the wasm client (guest servers bind+listen but never accept;
    the E2E listen twin asserts bind+listen only), and the "unresolved flake"
    (guest path broken across many runs after the flip-back, recovered after
    a full `make build`) — never reproduced with the relay in place; re-check
    if the wasm client is ever rebuilt.

31. **Hostnames banned — host.docker.internal removed (2026-08-16, user
    mandate).** The user requires, categorically, that NO hostnames ever
    appear: no `host.docker.internal`, no `/etc/hosts` entries, no custom
    DNS for LAN users — everything must work with `127.0.0.1` (zero-config
    single machine) and a hardcoded LAN address (e.g. `192.168.x.x`) alone.
    §12/21(30) is superseded in its conclusion: the guest-data-path break it
    observed under `server_url=127.0.0.1` is attributed to the gateway's
    DERP reachability, not to the wasm client's netmap handling — the
    netmap's DERP host (`127.0.0.1` with server_url=127.0.0.1) is the
    GATEWAY's own loopback, so the gateway must run a loopback socat relay
    on CONTROL_PORT forwarding to the server's static compose-network IP
    (`GATEWAY_CONTROL_IP`, `172.28.0.10`, cert SAN `IP:${SERVER_IP}`); with
    that relay the DERP host is reachable from both ends (single machine;
    on a LAN the DERP host is the LAN IP, reached through the host). The
    gateway joins via `--login-server https://${GATEWAY_CONTROL_IP}:8443`
    and `extra_hosts` was removed from compose.yaml. `CONTROL_HOST` defaults
    to `127.0.0.1` everywhere (entrypoint, print-url, gen-certs, acceptance,
    compose, `.env.example`); the cert SAN no longer carries
    `DNS:host.docker.internal`; CI no longer edits the runner's `/etc/hosts`
    and the E2E host-resolver-rules were removed; the join-test client uses
    `https://172.28.0.10:8443`. **Enforcement:** `tests/unit/test_scripts.py
    ::test_control_host_defaults_consistent` asserts the 127.0.0.1 defaults
    AND that the literal `host.docker.internal` appears in none of the
    runtime config/scripts/tests/CI files (the banned-file list lives in
    the test) — CI fails if it is ever reintroduced. AGENTS.md carries the
    rule. **Re-verification (open):** the §16.9 break was never reproduced
    with the gateway's CONTROL_PORT loopback relay in place; the E2E
    `network.spec.js` root-visit test is the gate (`CONTROL_HOST=127.0.0.1`
    defaults, no /etc/hosts). The inbound accept-path finding of (30) is
    unchanged (runtime limitation, not a hostname issue). Updated:
    `gateway/entrypoint.sh`, `compose.yaml`, `server/entrypoint.sh`,
    `scripts/gen-certs.sh`, `scripts/acceptance.sh`, `scripts/print-url.sh`,
    `.env.example`, `.github/workflows/ci.yml`, `tests/unit/test_scripts.py`,
    `tests/unit/test_templates.py`, `tests/server/integration.sh`,
    `tests/server/join-test-client.sh`, `tests/e2e/playwright.config.js`,
    `tests/e2e/tests/boot.spec.js`, `tests/e2e/*.mjs` probes, `README.md`,
    `AGENTS.md`, `plans/networking-bug.md` §16.10.

32. **Silent-halt surfacing — the CheerpX swallowed-trap path (2026-08-16).**
    **Symptom:** "sometimes the VM doesn't load and nothing appears"; with
    DevTools open: `Unexpected exit` + `RuntimeError: memory access out of
    bounds`. **Root cause (verified in the pinned self-hosted runtime):** the
    core catches guest-side WASM traps at its own thread trampolines
    (`catch(e){if(e!='CheerpJContinue'){debugger;console.log('Unexpected
    exit',e.stack)}}` + a variant that `e()`-CALLS the caught exception →
    `TypeError: e is not a function`, the historical crash record in
    plans/networking-bug.md §15.1). The swallow kills just that guest process
    and `cx.run()` NEVER settles — the §12/21(29) overlay could not fire and
    the screen stayed black. A `memory access out of bounds` is a host-level
    trap (memory-growth/layout race inside the emulator) — intermittent, and
    the same boot usually succeeds on retry. **Fixes:**
    - `WebVM.svelte` captures the engine's own `console.error("Unexpected
      exit")` report (`installTrapCapture`, before the CheerpX import) and
      routes uncaught engine errors + `unhandledrejection` + rejecting
      `cx.run()` RuntimeErrors to the overlay; the one-shot auto-reload is
      restricted to DEFINITIVE boot-death signals (rejecting `cx.run()` or
      the watchdog verdict; sessionStorage `webvm-trap-reload` counter);
      ambiguous trap reports surface the overlay immediately but never
      reload (the core may have killed only a disposable process).
    - A **boot watchdog** (2 s tick) declares the boot stuck when, visible
      tab only, there is no guest console output AND no non-black KMS pixel
      for 200 s (270 s floor) — above the E2E's 240 s first-pixel budget so a
      slow cold-cache boot is never falsely declared stuck; the last boot
      text is shown in the overlay and an "Estimated time remaining" pill
      makes a still-booting screen honest — the budget is CALIBRATED from
      live-site boots (cold ~52 s UK at a ~57 ms "Backend latency" disk read
      latency vs ~87 s at ~103 ms with a +90 ms extra RTT [North-America
      reader]: ~0.75 s of boot per extra ms, so the pill's 105 s baseline is
      scaled by the engine's measured per-block read latency — the same
      figure the Disk pane displays) —
      replacing the old "Booting the VM… Ns" counter; it counts to 00:00 and
      stays visible until the guest's file manager reports itself ready on
      the boot console ('webvm desktop ready' — a one-shot marker written by
      file-explorer.py once its first listing is on screen), so the pill
      covers the pixel→desktop window too. Terminal-only VMs are excluded
      from the
      pixel checks.
    - Vendored-runtime patch (applied by `scripts/fetch-cheerpx-runtime.sh`
      after every fetch): removes all `debugger;` statements (they froze the
      tab with DevTools open), drops the `e()` call, and reports EVERY
      swallowed trap via the same `console.error('Unexpected exit', …)`
      prefix; the fetch downloads to a STAGING tree (`--fail`) and only
      installs after the presence guards pass (exactly three sites, no
      `debugger`, no `e()`).
    - Test hook `webvm-test-trapreport` + error-overlay spec case.
    **Known limitation:** the trap is a CheerpX-core memory-layout bug, not a
    guest code bug — no guest fix exists; retry-once + exact-reason overlay is
    the mitigation. Updated: `webvm/src/lib/WebVM.svelte`,
    `webvm/cheerpx/cxcore.js`, `webvm/cheerpx/cxcore-no-return-call.js`,
    `scripts/fetch-cheerpx-runtime.sh`, `tests/e2e/tests/error-overlay.spec.js`.

33. **Update-to-latest Tier A (2026-08-18, plans/update-to-latest.md §3).**
    All runtime/infra pins were uplifted and re-validated; the guest base OS
    (Tier B) and the frontend framework majors (Tier C) are unchanged here.
    - `@leaningtech/cheerpx` + the self-hosted runtime moved **1.3.7 → 1.3.8**
      (exact pin in `webvm/package.json` + lock, `webvm/src/lib/cheerpx.js`,
      `scripts/fetch-cheerpx-runtime.sh`). The fetch re-ran clean: only
      `cxcore.js`, `cxcore-no-return-call.js`, `cxcore.wasm` differ from
      1.3.7 — `cx.esm.js`/`cx_esm.js` and the `tun/*` glue are byte-identical,
      so the rebuilt-wasm pairing surface is untouched. The §12/21(32) trap
      patch applied to the 1.3.8 cores with NO target adaptation (all three
      trampoline sites matched; presence guards pass: exactly 3
      `console.error('Unexpected exit'` sites per file, no `debugger`, no
      `e()` call). `webvm/WEBVM_COMMIT` refreshed to
      `8d68d2b18fa04d72ba49bc6c5b8c684a934fc268` (2026-08-13) — of the
      upstream range `e58fef0c9..8d68d2b18` only the CheerpX 1.3.8 bump was
      taken; the two `messages.js` promo-text hunks are N/A (this repo's
      `introMessage` is its own banner-free text; verified used by
      `WebVM.svelte`). Note: npm now also publishes **1.3.9** (2026-08-18);
      not taken — the plan pins 1.3.8 (upstream's bump); re-verify the patch
      + pairing if ever moving past 1.3.8.
    - **Headscale 0.28.0 → 0.29.3** (`server/Dockerfile`,
      `server/headscale/config.yaml.template`): the ephemeral reaping key
      moved to the nested `node.ephemeral.inactivity_timeout` (verified
      against the v0.29.3 `config-example.yaml`; every other template key
      exists unchanged). CLI surface re-verified from the v0.29.3 source:
      `preauthkeys create --user <numeric id>` unchanged;
      `preauthkeys list` takes no `--user` and MASKS keys with `***` in
      non-TTY output (entrypoint's prefix-stripping parse still correct);
      `users list` keeps numeric ID as the first column. **SQLite upgrade
      path verified in place**: the existing 0.28.0-created
      `headscale-data` volume (users + preauth keys + gateway node record)
      came up clean under 0.29.3 — schema migrated at first serve, the
      entrypoint key-check passed, the gateway rejoined with its recorded
      tailnet IP. §12/9 item (l): 0.29.3 still has **no fixed-IP
      reservation mechanism** (re-confirmed against the config schema and
      CLI — IPs are sequentially allocated and stabilized via the persisted
      node record only).
    - `pysmb` **1.2.10 → 1.2.15** (`diskimage/Dockerfile`, samba builds).
      `GO_IMAGE` in `scripts/rebuild-tailscale-wasm.sh` **1.26.5 → 1.26.6**
      and the wasm was REBUILT with it (tailscale source still v1.102.2;
      `wasm_exec.js` byte-identical across the Go patch — unchanged in the
      repo). Server base **python:3.11-alpine → python:3.14-alpine**
      (`server/Dockerfile`, `compose.yaml` `test-unit`; wsgidav 4.3.5 +
      cheroot 11.1.2 verified installing/importing on 3.14). CI **Node 20 →
      24** and actions majors uplifted (checkout@v7, setup-node@v7,
      setup-buildx/qemu@v4, upload-artifact@v7, download-artifact@v8,
      upload-pages-artifact@v5, deploy-pages@v5; verified latest majors on
      2026-08-18). e2fsprogs helper `ubuntu:24.04 → 26.04` (`build.sh`).
    **Re-verified gates (all green):** §12/21(c),(d),(e),(g),(i),(32) —
      boot/desktop E2E (real VM under chromium), error-overlay trap-capture
      spec (patched 1.3.8 core), no-egress spec, network.spec + sync.spec
      (rebuilt Go 1.26.6 wasm + 1.3.8 runtime + headscale 0.29.3 control
      plane, `CONTROL_HOST=127.0.0.1`), integration.sh (incl. the
      join-test-client against `https://172.28.0.10:8443`), the 92-test
      pytest unit suite (hostname ban intact), rootfs smoke for all four
      backends, shellcheck + yamllint.

34. **Update-to-latest Tier B — guest rebuild on Alpine 3.24 (2026-08-20,
    plans/update-to-latest.md §4/§9).**
    The guest base moved **i386/alpine:3.17 (EOL 2024-11) → 3.24.1**
    (supported to 2028-06), taking Python 3.10 → **3.14.7**, tcl/tk →
    **8.6.17** (patched lib rebuilt from 8.6.17 sources — the 8.6.18-built
    override conflicted with apk's exact-match `package require -exact Tcl
    8.6.17`), Pillow 9.3 → **12.2.0**, mistune 2.0.4 → **3.2.1** (viewer
    now walks both token shapes — mistune 3 has no `AstRenderer`), the WM
    i3 → **openbox 3.6.1** (draggable windows + close buttons), git 2.54.0,
    openssh-client-default 10.3_p1, py3-pip 26.1.2 (`--break-system-packages`
    — PEP 668 on 3.24), idle3.10 → **idle3.14** rename repo-wide
    (python3-idle still hard-depends on python3-tests → the apk-fetch+tar
    extraction trick stays), pysmb 1.2.15, third_party fork at
    tcl/tk-8.6.17 (only the notifier stale-fdset patch applied; the other
    aports patches are upstreamed in 8.6.17/18).
    **CheerpX boot blockers (all root-caused + fixed 2026-08-19/20); full
    numbered record (the five syscall-emulation defects, the image changes
    and the later 2026-08-22/24 hardening items) is in
    plans/update-to-latest.md §9.5.1:** the openrc 0.63.2 boot (new in 3.24)
    failed under CheerpX with five distinct syscall-emulation defects, all
    worked around in one LD_PRELOAD shim (`diskimage/faccessat-fix.c`, built
    in a `shimbuild` stage, loaded via `rc-preload` +
    `rc_env_allow="LD_PRELOAD"` in rc.conf): `faccessat(-1)` wild-calls,
    `sigprocmask(SIG_UNBLOCK)` wild-calls, `ppoll` always failing, an
    endless `setsockopt(SO_PASSCRED)` retry loop, and openrc's
    `env_filter()` scrubbing LD_PRELOAD from exec'd init scripts. Image
    changes: `/run/openrc` state dirs + `/run/{lock,secrets}` baked; patched
    `init.sh` (/run tmpfs mount failure is a warning, not an abort);
    udev-trigger/udev-settle + `networking` removed from the boot runlevel;
    static `/etc/X11/xorg.conf` evdev InputDevice sections; `build.sh`
    fingerprint includes `faccessat-fix.c`; baked deptree via
    `RUN /sbin/openrc sysinit; true` (the year-2695 skew mtime noise is
    cosmetic — the deptree-scan interpose removes its ~20 s boot cost).
    Tcl/Tk stays at apk 8.6.17 with ONLY the CheerpX notifier fix (the
    plan's 8.6.18 note is superseded). Sync agent audited on 3.14.
    **Re-verified gates (all green):** browser-phase Playwright **9/9** and
    the webdav-phase suite **12/12** on 1.3.8 + headscale 0.29.3; unit
    **92/92**; rootfs smoke ×4 backends; server integration PASS (incl.
    join-test-client); shellcheck + yamllint clean. NOTE on the 2026-08-20
    handoff's "webdav data path" failure: it was an artifact of running the
    webdav-phase specs against a BROWSER-mode guest (desktop.start never
    starts the sync agent) — with a correctly-backend-built guest the data
    path works end to end (verified 2026-08-21).

35. **Correctness/de-dup/perf round (2026-08-29).** One pass over the whole
    repo for correctness, duplication, low-effort test gaps and performance:
    - **Shared shell lib** `scripts/lib/webvm-common.sh` (COPY'd to
      `/etc/webvm/lib/` in both container images): the deployment defaults
      (`CONTROL_HOST`/`LAN_IP`/ports/`GATEWAY_CONTROL_IP`) now live in ONE
      place — server+gateway entrypoints, build.sh, gen-certs.sh, print-url.sh,
      acceptance.sh, integration.sh and join-test-client.sh source it (the
      drift test asserts every consumer sources it). Helpers:
      `webvm_wait_until`, `webvm_require_secret`, `webvm_supervise`,
      `webvm_load_dotenv` (environment > .env > defaults, quotes stripped —
      fixing the Makefile `STORAGE_BACKEND=webdav make up` mismatch and
      acceptance.sh's empty samba/webdav credentials). `build.sh`'s inline
      .env parser was deleted in favour of the loader; `make url` no longer
      sources .env itself (the script does, so `VAR=x make url` overrides
      survive). `SERVER_IP` unified into `GATEWAY_CONTROL_IP` (cert SAN,
      compose, gateway, tests — one value, documented in `.env.example`).
    - **Version lockstep:** `scripts/versions.env` is the single source for
      the tailscale (1.102.2) + Go (1.26.6) pins — gateway image,
      join-test-client and rebuild-tailscale-wasm.sh agree (unit-tested);
      the stale "1.3.7" nginx comment corrected to 1.3.8.
    - **Compose:** gateway now uses `depends_on: server: condition:
      service_healthy` (the healthcheck existed but was never wired — the
      un-retried `tailscale up` could race a cold headscale start).
    - **nginx:** the identical CSP header (3 copies) moved to
      `server/csp.conf.template` rendered to `/etc/nginx/csp.conf` and
      `include`d from the site block + `/cheerpx/` + `/_app/immutable/`;
      the ext2 location now sends `Cache-Control: public, max-age=31536000,
      immutable` — safe because the page loads the image with a
      `?v=<image-build>` fingerprint query (config_public_alpine.js), so
      repeat boots hit the browser HTTP cache with zero revalidation while
      an image upgrade changes the URL (a stale cached base can never pair
      with a new overlay).
    - **Frontend de-dup:** `$lib/cacheId.js` (shared/ephemeral derivation),
      `$lib/siteBase.js` (cheerpx.js + network.js share one base-path
      derivation; app.html's `/webvm-config.js` is now relative — the Pages
      deployment was 404-ing it), `clipboard.js` exports the paste cap +
      per-char delay (WebVM.svelte + PasteTab.svelte share them),
      `NETWORK_STATES` const (NetworkingTab no longer retypes the literals;
      the duplicate store export dropped), the wasm-client tun import uses
      siteBase (Pages-safe). Fixes: `handleConnect` guards a null `cx`
      (popup no longer stranded pre-boot), `startLogin` sets LOGINREADY
      *after* the URL resolves (the clickable login state was dead — the
      button stuck at "Starting Login…"), `pasteUntypableReason` emits the
      4-digit `U+%04X` format the guest typer uses (the E2E refusal text
      was mismatched). Boot watchdog pixel sampling 2 s → 5 s (halves the
      full-canvas readback churn during the heaviest phase); the KMS canvas
      is focusable (`tabindex="0"`).
    - **Guest:** `99-screen-resize.sh` poll is adaptive (2 s while the
      output changes, 10 s steady — no more perpetual `xrandr --auto`
      every 3 s); the Dockerfile's stdlib compileall+trim layer moved
      BEFORE the rootfs/config/scripts COPYs (a guest-file edit no longer
      re-runs the emulated-i386 compileall — minutes saved per local
      iteration on Apple Silicon) with a small post-COPY compileall for the
      few guest scripts; `paste-typer.sh`'s backend-respawn is now exact
      (a wrapper writes a status marker on backend exit — kill -0 lied on
      un-reaped zombies, silently dropping later pastes); `wm-clients.sh`
      gained `--count-line` (the keep-alive spy pipes into it — the
      hex-count contract lives in ONE file, case-insensitive "no such atom"
      guard) and the explorer single-instance guard moved to
      `webvm-pidfile.sh` shared by open-file-explorer.sh and
      keep-file-explorer.sh.
    - **Tests (+63):** vitest added to the frontend (23 tests: the
      networking button-state machine, the session guard with fake
      localStorage/BroadcastChannel/fake timers, the app.html seed script
      executed for real, cacheId); `tests/unit/test_entrypoint.py` executes
      BOTH entrypoints in a chroot sandbox with stub binaries (fail-closed
      per mode, `WEBVM_TAILNET=off` renders the empty baked config); the
      sync agent's orphan paths covered (load_config precedence, transport
      error paths, the auth-dropping redirect handler, wait_for_tailnet,
      the push loop's final push + lease release, cmd_daemon under a real
      SIGTERM, main() argparse, skip_existing extraction, symlink scan
      skip, SMB reconnect-on-failure); paste-typer backend-respawn test;
      drift guards (versions.env lockstep, app.html seed-key list vs the
      renderer, banned-hostname list extended to the new files, shellcheck
      now covers the shared lib + paste-typer + wm-clients); the keep-alive
      tests sandbox the shared lib + wm-clients. CI: browser phase now runs
      `tests/server/integration.sh` too (join test self-skips without
      headscale), and the webdav E2E URL comes from `scripts/print-url.sh`
      instead of a hand-rebuilt hash (one derivation, drift-tested).
    - **Still-open local finding:** the `paste.spec.js` "pasted text
      definitely appears…" E2E fails in THIS environment (headless macOS)
      because page clicks on the KMS canvas never move the page/guest focus
      — the page's console textarea keeps focus, so neither native typing
      nor the XTEST lane reaches the explorer's Search box (the failure
      predates this round; the in-guest Xvfb paste test passes:
      `ENTRY_CONTENT=hello paste`). The guest focus path is a CheerpX
      core/browser-input integration question, not a regression of this
      round.
    Verified: 155 pytest + 23 vitest green, shellcheck/yamllint/compose
    clean, the webdav stack rebuilt end-to-end (new guest fingerprint
    `97c67052be58`, immutable ext2 header live, gateway healthy-joined,
    integration.sh PASS incl. the join-test client), E2E idle-pointer +
    resize + untypable-refusal pass. The round's still-open E2E focus-path
    finding (paste delivery in this environment) is a CheerpX input-
    integration investigation, tracked in §12/35.

36. **Correctness/de-dup/perf round 2 (2026-08-29).** Follow-up pass on the
    same axes:
    - **Correctness fixes:** `gen-certs.sh` `is_ip_literal` now treats only
      dot/colon-bearing tokens as IPs (a hex-only hostname like `dead` was
      silently dropped from the DNS SAN — TLS mismatch); the server cert is
      REUSED when its SAN already covers the env (was: regenerated every
      `make up` while running containers serve the old mounted cert);
      `build.sh` fails fast outside the repo root (wrong-tree builds read a
      foreign `.env`); `make up` warns when the deployment backend is
      samba/webdav (it is a HARD-NETWORKLESS launch by design — the warning
      points at `make up-tailnet`); paste-typer answers `CXFAIL corrupt` for
      length-mismatched/undecodable frames (was: silent, page waited out the
      ack timeout).
    - **De-duplication (single sources):** the shared lib now owns
      `WEBDAV_BASE_PATH` (`/webdav/` — wsgidav mount, baked syncUrl, guest
      syncrc default), `ALPINE_PAGE`, `WEBVM_IMAGE_DIR`/`WEBVM_IMAGE_NAME`
      (build.sh + nginx envsubst + Makefile; the frontend literals are
      drift-pinned by unit tests), `CACHE_ID_PREFIX`, `GIT_SSH_PORT` (was
      2222 in two places) and the `webvm_require_mode_secrets` per-mode
      fail-closed matrix (server entrypoint + print-url.sh now enforce ONE
      matrix — the entrypoint adds `--gateway-key`). `render-webvm-config.py`
      switched from 8 positional args to named options and gained
      `--render-csp` — the CSP header's single home (the old
      `csp.conf.template` is deleted; the entrypoint renders
      `/etc/nginx/csp.conf` from the same Python that derives controlUrl, so
      the page's connect-src and the control-plane URLs cannot drift).
      `scripts/versions.env` gained `CHEERPX_VERSION=1.3.8` (fetch script
      consumes it; package.json + cheerpx.js pinned by lockstep test).
      The 8 identical "source webvm-common.sh" preambles were kept as-is:
      the existence check must run BEFORE the source, so the check itself
      cannot live in the lib (each consumer's fallback path differs).
    - **New unit tests (208 pytest + 43 vitest green, all in CI):**
      template-vars ↔ entrypoint-envsubst-list drift (a `${VAR}` added to a
      template but not its list ships a literal into the container config —
      the boot-time `nginx -t` failure class); compose.yaml inline defaults
      vs the lib (comment-excluded); `_href_to_rel` listing sanitizer
      (absolute URLs, base-path strip, `../`/`//` rejection,
      percent-decoding, foreign-host hrefs), `_parse_http_date`
      invalid/naive dates, `wait_for_tailnet` raising transport,
      `acquire_or_wait` backend-error branch, `cmd_pull` tailnet-down path,
      `compute_pull_plan` local-existing protection, `ensure_remote_parents`
      MKCOL chain; entrypoint fail-closed variants (samba preauth key,
      nginx `-t` rejection, webdav artifacts absent in networkless mode);
      gateway samba `SAMBA_LAN_IP` check moved BEFORE tailscaled starts
      (fail fast) + tested; gen-certs SAN/CA-reuse/coverage-skip (openssl
      added to the test-unit image); paste-typer corrupt-frame + exact
      MAX_PAYLOAD boundary (via `PASTE_MAX_PAYLOAD` override); keep-alive
      viewer-protection variant; supervise marker contract;
      `--url` no-authKey plain URL; build.sh unknown-backend rejection.
    - **Performance:** cold boot — a background low-priority 16 MiB range
      warm-fetch of the ext2's leading bytes overlaps the image download
      with engine init (the guest's first reads become HTTP-cache hits);
      KMS internal resolution capped at 1280×800 (uncapped 1920×1080 ≈ 2.6×
      the pixels — X blits, Tk and per-frame canvas transfer all scale;
      the CSS box scales the canvas up); tailnet builds probe `/health` and
      preload `tailscale.wasm` from the app.html inline script (the ~5 MB
      download no longer waits for hydration + the 3 s probe); the CheerpX
      runtime import starts before the session lock settles (`SETTLE_MS`
      200 → 50). Guest — `rc_parallel="YES"` in rc.conf (default-runlevel
      services start concurrently; `local` has no dep on dbus/
      udev-postmount), fontconfig cache re-baked after the trim (first Tk
      app no longer rebuilds it on the overlay), ssh-keygen backgrounded,
      X socket poll 250 ms, screen-resize steady cadence 10 s → 30 s with
      a cheaper geometry signature. Operation — paste typing delay 10 ms →
      5 ms (~200 chars/s; the ack timeout is a conservative bound either
      way — the paste E2E is the validation gate), idle accept poll 100 →
      250 ms, UDP receive buffers pooled, CPU-percentage recomputation
      coalesced to ≤1/500 ms. NOT changed: the CloudDevice gzip-chunk path
      (biggest structural cold-boot win, but the pinned 1.3.8 contract
      needs an E2E boot-budget validation before adopting — the warm fetch
      delivers most of it) and the paste delay below 5 ms (the emulated
      XSync round trip is the real pacing; further cuts need on-device
      validation).

37. **Correctness/de-dup/perf round 3 (2026-08-30).** Third pass on the
    same axes:
    - **Correctness fixes:** the 2026-08-29 paste-delay halving
      (5 ms/char) had left the E2E typing-estimate literals, PasteTab's
      comment, README and acceptance on the OLD 10 ms math — `paste.spec.js`
      would have failed CI on the next push; all four are now on the
      5 ms model (500 → ~2.5s, 900 → ~4.5s, 600 → ~3s) and
      `clipboard.js` exports `CX_CHARS_PER_SEC` as the derived rate.
      **`make_snapshot` leaked NESTED excluded names** (a project's
      `.ssh`/`.cache` under a synced subdirectory was uploaded in the
      first-sync tarball — `tarfile.add(recursive=True)` filtered only
      top-level names); the snapshot now walks the tree with the same
      per-part exclusion contract as `scan_local`/`extract_snapshot`
      (test added that fails on the old code). `compose.yaml` now
      interpolates the server's static IP from
      `GATEWAY_CONTROL_IP` (`ipv4_address: ${GATEWAY_CONTROL_IP:-172.28.0.10}` —
      a bare literal silently orphaned a remapped gateway) and forwards
      `GIT_SSH_PORT` to the gateway (an override previously did nothing).
      `.env.example` clarifies `CONTROL_WSS_PORT` (the wasm client always
      dials the scheme-default 443 via the gateway relay; the value only
      remaps the server listener + relay target — the relay path is what
      makes remapping work) and documents `GIT_SSH_PORT`. **Keep-alive
      pidfile zombie fix:** a SIGKILLed second-generation explorer is an
      un-reaped direct child of the keep-alive shell, `kill -0` succeeds
      on zombies, and the stale pidfile pinned the single-instance guard
      forever — `webvm-pidfile.sh` now treats `/proc/<pid>/stat` state Z
      as dead (kill -0 fallback where /proc is absent), the explorer and
      viewer remove their pidfiles at exit, the keep-alive removes the
      pidfile before every relaunch, and the rootfs smoke suite kills the
      RELAUNCHED (second-generation) explorer to prove the desktop heals.
      `join-test-client.sh` derives the compose network from `docker
      network ls` instead of a hand-written literal. `rc-preload` (boot
      path) added to shellcheck + the parse list. Stale `csp.conf.template`
      comment in nginx.conf.template corrected to
      `render-webvm-config.py --render-csp`.
    - **De-duplication (single sources):** `render-webvm-config.py` no
      longer re-defaults the lib constants — `--site-port`,
      `--webdav-base-path`, `--alpine-page` are REQUIRED from callers
      (entrypoint/print-url already pass them; the unit tests now pass the
      lib values, so a lib change can no longer hide behind a Python
      default); `GATEWAY_TAILNET_IP_DEFAULT` (100.64.0.1) moved into the
      shared lib (build.sh uses it; the Dockerfile ARG defaults are pinned
      by test); `tests/server/integration.sh` uses `$ALPINE_PAGE` /
      `$WEBVM_IMAGE_DIR/$WEBVM_IMAGE_NAME` / `$WEBDAV_BASE_PATH` instead
      of the hardcoded literals; new `.github/actions/build-frontend`
      composite action shared by ci.yml's frontend job and pages.yml (the
      fingerprint-read + npm ci + vitest + build derivation was a third
      inline copy); build.sh skips the (idempotent) e2fsprogs helper image
      build when the tag already exists.
    - **New unit tests (+40 pytest → 248, +2 vitest → 45, all in CI):**
      the transport edge contract against the existing fake server
      (mkdir 405/409, delete/get/listdir missing-file, ping-false,
      corrupt-lease recovery incl. refresh), `pull_home`'s snapshot-restore
      branch, `cmd_pull` crash-safe generic-exception branches, push-loop
      "push error"/"final push failed" paths, `cmd_daemon` signal-
      registration failure + mid-run takeover re-acquisition (fake clock),
      the nested-exclusion snapshot test, `webvm-pidfile.sh`
      missing/live/reaped/ZOMBIE pids (Linux-only zombie case), the
      single-instance launcher, the IDLE `-n` fallback, the screen-resize
      adaptive cadence (fake xrandr/sleep), `precompress-static.sh`
      (type/1 KiB filters, no-.gz invariant, brotli-missing noop), the
      SERVER entrypoint's full happy path (stub headscale serving a real
      socket + masked key listings → users/key checks pass, all services
      supervised) and the gateway's whole relay matrix incl. git-relay
      gating. New drift pins: paste delay/payload lockstep
      (DELAY_US == CX_TYPE_DELAY_MS·1000, PASTE_MAX_CHARS ≤ MAX_PAYLOAD),
      the guest-baked syncUrl vs the renderer (one formula), the vendored
      cxcore trap-patch guards (exactly 3 report sites, no debugger, no
      `e()`), the security-header trio across nginx/subresource/sw.js,
      compose `ipv4_address` interpolation, the E2E/CI URL literals vs the
      lib, gen-certs regenerating on a `GATEWAY_CONTROL_IP` change.
    - **Performance:** nginx `open_file_cache` (the boot's ~1100 range
      GETs stop paying per-request metadata syscalls); the ext2 warm-fetch
      window widened 16 → 32 MiB (the boot-critical read set measurably
      extends past 16 MiB); tailscale.wasm preload no longer waits for the
      `/health` probe (the wasm is same-origin — the probe only warms the
      control connection); the watchdog's pixel probe reuses one scratch
      canvas (no per-tick allocation); `cpuCallback` no longer
      clearInterval+setInterval per scheduling event (armed once);
      `latencyCallback` coalesced to ≤1/500 ms like the CPU percentage.
      NOT changed: per-char `cxReadFunc` batching and the keep-alive's
      `date +%s` fork (both bounded by the guest's own pacing/emulation
      costs — no measurable win), the CloudDevice gzip path (as §12/36).
    Verified: 248 pytest + 45 vitest green (incl. the two new sandbox
    suites — the entrypoint happy paths run real chrooted stubs), the
    frontend builds clean with the Svelte edits, `docker compose config -q`
    passes, shellcheck clean on the full list incl. rc-preload. The
    in-guest second-generation keep-alive cycle runs in the rootfs smoke
    suite (requires the emulated guest — CI).
38. **Live-site boot failures on GitHub Pages — stale cjFS data +
    post-publish liveness check (2026-09-01).** The live site
    (`https://ned14.github.io/webbrowser-python-idle/alpine.html`) failed to
    boot in a user browser with `Uncaught RuntimeError: table index is out of
    bounds` inside `cheerpOSOpenMain -> idbMakeFileData` after a clean
    Alpine init. Investigation (byte-hash of the live runtime/bundles vs the
    repo, live image chunk protocol check, fresh-profile Playwright boots):
    the deployment itself was healthy — cold and warm boots succeeded — and
    the crash read records from the CheerpX guest-persistent folder FS
    (IndexedDB DB `cjFS_/files/`, an upstream FIXED name, never versioned per
    deployment; upstream cheerpOS.js carries a literal "TODO: Verify IndexDB
    version"). The affected browser held `cjFS_*` records written by older
    deployments (the 1.3.7 -> 1.3.8 runtime bump of 2026-08-18 and the
    broken/cancelled 2026-08-31 Pages runs); a fresh profile never sees them
    and boots cleanly. Fix: `webvm/src/lib/cjfsVersion.js` —
    `resetCjfsIfImageChanged(imageBuild)` runs on BOTH session-lock paths in
    `+page.svelte` (the mount-fixed `cjFS_/files/` DB is opened by ephemeral
    sessions too) and deletes THIS app's cjFS IndexedDB family —
    `cjFS_/files/` + the runtime-prefixed overlay family
    `cjFS_/blocks_alpine_<build>/` — whenever the image-build fingerprint
    differs from the marker (first load under the migration wipes too —
    automatic repair for already-poisoned browsers); same policy as the
    `blocks_alpine_<image-build>` overlay (a rebuilt image starts a fresh
    base). The wipe is scoped to the app's own databases: all of an
    account's Pages projects share one `https://<user>.github.io` origin
    and IndexedDB is origin-scoped, so a bare `cjFS_*` prefix would destroy
    sibling projects' CheerpX stores. The localStorage migration marker
    records SUCCESS only — a deleted-while-blocked (another tab's live VM)
    or errored wipe leaves the marker unset and retries on the next boot
    (post-review hardening). Cosmetic: removed the `<link rel=preload
    as="worker">` for cxcore.js (Chromium rejects the destination and the
    runtime loads the file via `new Worker(url)` — no preload destination
    matches; three of the four runtime files remain preloaded) and staged an
    empty `webvm-config.js` in pages.yml (Pages previously 404ed the
    deliberate-no-op config script every load). NEW post-publish liveness
    check: `.github/workflows/liveness.yml` triggers on the Pages workflow's
    successful completion (`workflow_run` — the check CANNOT run before the
    publish) and runs `tests/e2e/live-site-check.mjs`, which waits for the
    deployed bundle to reference that run's disk image (`_<runId>.ext2`,
    cache-busted polling; Pages/Fastly can lag a run's completion), then
    FULLY boots the live site 5x in a row (fresh profile per boot, desktop
    pixels via canvasProbe + COI + zero `[WebVM] runtime failed`/page errors;
    the webvm-config.js 404 and preload warning of older deploys are
    reported, not failed on; `--retry-on-flake` retries one failed boot
    once with a fresh profile — the observed cold-boot crash never repeats
    across profiles, so a documented retry drops the false-red rate from
    ~21% to ~0.2% per run while a true regression still fails every boot;
    the workflow's concurrency group is per triggering run
    (`liveness-${{ workflow_run.id }}`), so a failed/cancelled interrupting
    Pages run can never cancel a check the published state is owed).
    Note: the first 5-boot run caught an
    intermittent cold-boot `null function or function signature mismatch`
    (~1/5, one of eleven boots) on an otherwise-healthy deployment — the
    checker's exact purpose. Verification: 45->55 vitest incl. the new
    cjfsVersion suite (10 tests), frontend builds clean, page no longer
    emits the preload warning, live liveness runs green. Updated:
    `webvm/src/lib/cjfsVersion.js` (+`cjfsVersion.test.js`),
    `webvm/src/routes/alpine/+page.svelte`, `webvm/src/app.html`,
    `.github/workflows/pages.yml`, `.github/workflows/liveness.yml` (NEW),
    `tests/e2e/live-site-check.mjs` (NEW), `tests/README.md`.
39. **Tailnet on a machine where host 443 is already occupied — the scheme-
    default WSS port is gone (2026-09-01).** The wasm Tailscale client's
    port-drop (it builds control-plane URLs portless: `wss://<host>/ts2021`,
    `wss://<host>/derp`, `https://<host>/derp/probe`) used to force the
    gateway to publish host 443 (CONTROL_WSS_PORT relay), which makes the
    tailnet unstartable wherever another service owns 443. Since the page
    glue ALREADY wraps `window.WebSocket` (the rejection watchdog) and every
    control socket/request is created through `window.WebSocket` /
    `XMLHttpRequest` / `fetch` in the page realm, the fix re-inserts the
    session control port into portless control-plane URLs
    (`reinsertControlPort` in `webvm/src/lib/network.js`; pinned to the
    control host + headscale's control paths only — `/ts2021`, `/derp`,
    `/key`, `/bootstrap-dns`, `/machine/register`; any other URL passes
    through byte-identical). ALL control traffic now dials CONTROL_PORT, so
    the whole 443 machinery was removed: gateway `/ts2021` socat relay +
    compose publish, nginx `CONTROL_WSS_PORT` listener, the CSP
    `https/wss://host:443` connect-src entries, `CONTROL_WSS_PORT` itself
    (shared libs, compose, .env.example, entrypoint envsubst list), the
    gateway's `control-443` health probe, the E2E no-egress 443 family and
    the CSP unit assertions. This also vacates host 443 entirely — a site
    on `SITE_PORT=443` no longer collides with the WSS relay (§10.9).
    Verification: 9 new vitest cases for `reinsertControlPort` (network suite
    18->27; frontend total 78 passed), `make test-unit` green (278 pytest)
    incl. the gateway relay-matrix assertions
    (no `TCP-LISTEN:443`), `docker compose config -q` clean. NOTE: the
    rewritten control-plane dials go to the SAME nginx CONTROL_PORT listener
    that served them via the relay before — only the port changes; the full
    boot flow (key → /ts2021 → netmap → DERP) must be re-verified by the
    tailnet E2E in CI for the webdav phase.

(pinned versions, guest NIC config, `extra_hosts` precedence, DataDevice path
semantics) is a lookup-and-record step, not a design decision — and the §12/21
checklist lists exactly which version-dependent claims to re-verify when the
versions are pinned.
