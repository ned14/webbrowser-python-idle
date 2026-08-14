# WebVM (Option A1) Implementation Plan — Personal Use, Minimal Alpine + IDLE, LAN-Only Networking, Configurable Storage (Browser | Samba | Container WebDAV)

Research date: 2026-08-08 · Revised 2026-08-09 (see note below).
See `plans/implementation_options.md` for the option comparison.

> **Revision note (2026-08-09, review rounds 5–7):** the plan has been through
> seven review rounds (round 5 = external cross-check against the CheerpX docs,
> the webvm source, headscale v0.29.x source, tailscale source, the Alpine 3.17
> APKINDEX, and empirical tests; round 6 = local review of the round-5 fixes;
> round 7 = the implementation-time revisions recorded in the note below and
> in §12/21).
> Round 5 fixed: the SvelteKit output path
> (`alpine.html`, not `alpine/index.html`), missing WebSocket upgrade headers
> on the nginx `/derp` locations, the headscale fixed-IP claim (no such
> mechanism exists in v0.29.x), the remaining runtime external requests
> (blog-post images, Claude/AI tab, service worker), the `nc`/`busybox-extras`
> claim (nc is in the base busybox), `derp.server.ipv4/ipv6` TEST-NET
> placeholders, the STUN rationale, and several previously "assumed" items now
> recorded as verification items in §12/21(k)–(p). Round 6 fixed the leftover
> STUN risk-row contradiction, the unreachable E2E Claude-tab instruction, the
> build.sh container-local-untar example, the nginx `root .` ambiguity for
> `/custom-disk-images/`, the headscale preauth-key first-run bootstrap,
> the CI artifact wiring for `WEBVM_IMAGE_BUILD`/the ext2, and the cacheId
> fingerprint (content-stable, not raw ext2 bytes). Round 7 (implementation-time
> revisions, 2026-08-09) records the deviations the built code makes from the
> letter of the design below — every one verified against the pinned versions
> and noted at the spot it touches plus in §12/21: **server_url is PATH-LESS**
> (v0.29.3's noise-internal router 404s a base path — §12/21(c)); the 8443
> listener is a **catch-all proxy** to headscale (no `/headscale/` prefix
> locations); the **CheerpX runtime is self-hosted** at `/cheerpx/` (the npm
> package CDN-loads its core by default — §12/21); the server container is
> built on **`python:3.11-alpine`** (the `nginx:alpine` python3/libexpat pair
> breaks pip); Alpine v3.17 repos point at **`dl-cdn.alpinelinux.org`** (the
> archive host does not resolve); `preauthkeys create` takes the **numeric user
> id** and `preauthkeys list` **masks keys**; the first-run key bootstrap uses
> **`HEADSCALE_BOOTSTRAP=1`**; `EXTRA_BIND_IP` was **dropped** (not bindable on
> macOS Docker Desktop); the guest SSH keypair is generated **at first boot**;
> the guest **sync agent walks subdirectories** (recursive listing + MKCOL of
> parent collections); and the CSP is the full `default-src 'self'` set
> (`script-src 'unsafe-inline' 'unsafe-eval' blob:`, `worker-src blob:`), not
> just `connect-src`.
> all decisions are in the body (§3, §4, §5, §12) and full rationale is in git
> history. Current standpoints in one paragraph: **HTTPS is the only access
> mode** — `https://127.0.0.1:<SITE_PORT>` single-machine (private CA trusted
> once) or `https://<LAN_IP>:<SITE_PORT>` on a LAN; `STORAGE_BACKEND=browser`
> is the default and works end-to-end in Phase 1 with no tailnet; networking
> (Phase 2/3) uses a self-hosted Headscale control plane with an **embedded
> DERP relay**, a `gateway` container (tailscaled userspace + socat relays to
> the LAN), the gateway tailnet IP **recorded after first join and kept stable
> by persistent node state**, and `derp.urls: []` (no public
> Tailscale DERP). The guest is a minimal i386 Alpine 3.17 with stdlib-only
> Python + IDLE (`idle3.10`); persistence is a browser IndexedDB overlay, or a
> guest sync agent against Samba (`pysmb`) or container WebDAV (wsgidav).

## 1. Summary

Build a Docker container that serves a website which boots a **graphical Linux
desktop inside the browser** using WebVM/CheerpX. The Linux runs entirely
**client-side** in WASM. The container also provides the services that give each
browser VM **LAN-only networking**; persistent files are stored on a
**configurable backend** — by default in the **browser's IndexedDB** (zero
infrastructure), with optional **Samba** or container **WebDAV** backends
(configurable served path).

Scope: **personal use only**; Alpine base; **Python + IDLE** (autostarted at
desktop boot); terminal + file manager; a **git client** (clone/pull/push to
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
the novel parts of this plan.

| Implementation | What it is | Key differences vs this plan |
|---|---|---|
| **webvm.io** (Leaning Technologies reference) | The original full-Linux (Debian, ~2 GB) terminal VM, hosted publicly; source of the whole frontend stack this plan pins. | Public HTTPS + **public Tailscale** interactive login (`login.tailscale.com`) — no Headscale/gateway/pinned IP, no LAN-only confinement; the large Debian image is streamed via a **reverse-proxy range+compression hack** on their origin (this plan serves a ~230 MB Alpine ext2 same-origin with plain nginx byte-range — no compression/range ambiguity); persistence is IndexedDB overlay only (no samba/webdav sync); logtail/plausible are **not** blocked; multi-user public service, not a single personal desktop. |
| **webvm.io/alpine.html** (Alpine Xorg/i3 desktop) | The reference for this plan's desktop: Alpine + Xorg + i3, KMS canvas, autologin via LightDM in the `leaningtech/alpine-image` build. | Image is far larger (adds gcc/nodejs/LightDM/rofi/polybar etc.) — this plan strips to stdlib-only Python + IDLE (~230 MB, no display manager); networking identical to webvm.io (public Tailscale), so no LAN-only path, no Headscale/gateway, no persistence backend, no single-session guard, no sessionStorage secrets handling. |
| **mini.webvm.io** (Mini.WebVM reference) | Serverless GitHub Pages deployment: Dockerfile→ext2 via a GH Actions workflow; the ext2 is pre-split into 128 KiB chunks (+`.meta`/`index.list`) and streamed via `diskImageType:"github"` (`GitHubDevice`); a **service worker injects COOP/COEP** because Pages cannot set headers. | Fully static — **no server-side component at all** (this plan runs its own nginx container regardless); chunking exists only to work around Pages' lack of byte-range/streaming-miss — this plan needs none (nginx Range is native); public Tailscale interactive login; terminal-only; no persistence backend; 1 GB Pages limit / 2 GB range-limit noted (this plan's image is ~230 MB, under both). |
| **GitHub Pages fork deployments** (the repo's "Deploy" workflow; third-party forks) | Any fork of `leaningtech/webvm` run through the Pages workflow — effectively mini.webvm.io per fork, at `<user>.github.io`. | Same as mini.webvm.io, plus: unversioned CheerpX per fork and no content control — this plan pins the exact webvm commit + `@leaningtech/cheerpx` version and serves everything from its own private HTTPS origin. |
| *(adjacent, not WebVM)* **PythonFiddle** (leaningtech) | CheerpX-based in-browser Python REPL, not a full OS. | Not a comparable WebVM; noted only because it informs the CheerpX free-for-personal-use/licensing framing (§1, §12) and in-browser CheerpX UX patterns. |

**What the comparisons change for this plan:** the reference implementations
all assume public internet + public Tailscale, which this plan explicitly
rejects (LAN-only, no exit node, `derp.urls: []`, CSP-blocked logtail). Worth
copying: the frontend mechanics (KMS canvas resize, the Dockerfile→ext2
pipeline) — though webvm's URL-hash handling is *not* copied as-is, since it
leaves `authKey`/`controlUrl` in the URL while this plan moves them to
`sessionStorage` and strips the hash. Worth avoiding: their workarounds
(origin range-hack, GitHub-Pages chunking, service-worker header injection)
that this plan's self-hosted nginx makes unnecessary.

## 3. Decisions (resolved)

1. **License:** personal use → CheerpX free (package README). No commercial
   license; do not distribute the site organizationally.
2. **Base image:** `i386/alpine:3.17` (32-bit, matching the reference Alpine
   image). EOL fallback: `/etc/apk/repositories` →
   `https://archive.alpinelinux.org/alpine/v3.17/{main,community}`.
   **REVISED at implementation (2026-08-09):** the archive host does not
   resolve (NXDOMAIN even at public resolvers) and `dl-cdn.alpinelinux.org`
   still serves v3.17, so the repositories point at the CDN
   (`https://dl-cdn.alpinelinux.org/alpine/v3.17/{main,community}`) — switch
   to the archive host if the CDN ever drops EOL versions.
   **The Dockerfile must enable the `community` repo** (the official Alpine
   image ships `main` only): `python3-tkinter` and `python3-idle` live in
   `community`. Note: tkinter/idle build against Python 3.10.11 while `main`
   ships `python3` 3.10.15 — a benign patch-level mismatch (same 3.10 ABI).
 3. **Apps:** `python3`, `python3-tkinter`, **IDLE** (the Alpine `python3-idle`
    package ships `/usr/bin/idle3.10` + `idlelib` — there is no `idle3`
    binary — and it hard-depends on `python3-tests`, an ~85 MiB install of the
    CPython test suite; the Dockerfile therefore **extracts `idlelib` and
    `idle3.10` from the package with `apk fetch` + `tar` instead of
    `apk add python3-idle`** so the guest stays minimal, Step 2), `xterm`,
    and **the file explorer** — a stdlib-only Tk app
    (`diskimage/scripts/file-explorer.py`, installed as
    `/usr/local/bin/file-explorer.py`). **No display manager** (direct
    `su user -c startx` → i3; fallback LightDM autologin). **The file explorer
    autostarts on the user's home directory** via i3
    (`open-file-explorer.sh`, a guarded single-instance launcher); it replaces
    the earlier pcmanfm/spacefm GTK file managers (which deadlocked under
    CheerpX, §12/23, plans/display-bug.md §2.9). `.py` files open in IDLE via
    `idle3.10-launcher` — "Open with IDLE" / double-click **withdraws the
    explorer** (the whole screen is handed to IDLE) and only re-shows it once
    IDLE exits, with the folder listing reloaded (§12/25). A keep-alive daemon
    (`/usr/local/bin/keep-file-explorer.sh`, autostarted by i3) relaunches the
    explorer whenever the last window closes, so the desktop never sits empty.
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
13. **Control-plane host (decided):** all control-plane/DERP URLs are built
    from a single **`CONTROL_HOST`** value — `https://${CONTROL_HOST}:${CONTROL_PORT}`
    — rendered into `server_url`, the URL hash `controlUrl`, and the gateway's
    `--login-server`. Default is **`host.docker.internal`**; the browser
    resolves it through a one-line `/etc/hosts` entry
    (`127.0.0.1 host.docker.internal`), and the `gateway` container resolves it
    to the **server container's static compose-network IP** via `extra_hosts`
    (works on Linux and Docker Desktop — §5.2). LAN use sets
    `CONTROL_HOST=<LAN_IP>` (§5). This guarantees the DERP relay URL derived by
    headscale from `server_url` is reachable from **both** the browser and the
    gateway container.

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
- **DataDevice injection spike (Phase 2, before Step 9):** the CheerpX API to
  *populate* a `DataDevice` — `dataDevice.writeFile(filename, contents)` — is
  **documented** (CheerpX 1.2.x docs; note the docs' filesystem table still
  lists DataDevice as "Write: no", an inconsistency with `writeFile`). The
  reference WebVM only mounts `DataDevice.create()` at `/data` without writing
  to it, so confirm `writeFile` against the **pinned** CheerpX version with a
  minimal page test. **Path semantics:** `writeFile` paths are relative to the
  device root, so the page mounts the device at `/opt` and calls
  `writeFile("/syncrc", …)` — the guest then sees the file at `/opt/syncrc`
  (mounting at `/opt/syncrc` instead would produce `/opt/syncrc/syncrc`).
  If `writeFile` is unavailable in that version, use the fallback:
  **baked `/root/.syncrc` only** — and because the baked file is built from
  real build args (Mode B/Step 2), it works without the injection; port
  remapping then requires an image rebuild (the baked fallback already targets
  the recorded gateway tailnet IP, so it only changes when ports/creds change) —
  or serve the sync config via the `/web` mount (read-only; the browser-side
  WebDevice fetch does not depend on guest networking, but the file is baked
  into the served site, so it has the same rebuild limitation as the baked
  fallback).
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
lease or the manifest); i3 autostarts the file manager only. **The boot-pull is
best-effort: it runs as `user`, and X starts after the timeout regardless** (a
misconfigured tailnet must not delay the desktop indefinitely). **X itself is
also started as `user`** (`su user -c startx` in `desktop.start` — never as
root: Xorg refuses to run as root, and i3's autostarts must land in
`/home/user`). `browser`/`none` need neither a sync agent nor a storage
endpoint.

## 5. Networking (LAN-only)

**Mechanism (required by CheerpX):** the guest's Tailscale client connects to a
**self-hosted Headscale** control server in the container, which also runs the
**embedded DERP relay**. All browser→LAN traffic stays on-LAN (WebSocket to the
container). The guest joins automatically via the URL hash
(`#authKey=…&controlUrl=…`).

**TLS (mandatory, not "production-only"):**
- The **site itself is served over HTTPS** on `SITE_PORT` (private CA). CheerpX
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
  (`https://${CONTROL_HOST}:${CONTROL_PORT}`): default **`host.docker.internal`**
  for single-machine — the browser resolves it through a one-line `/etc/hosts`
  entry (`127.0.0.1 host.docker.internal` on the machine running the browser),
  and the `gateway` container resolves it to the **server container's static
  compose-network IP** via `extra_hosts` (§5.2) so no host-published port ever
  needs to be reachable from inside Docker; LAN/multi-device use sets
  `CONTROL_HOST=<LAN_IP>` (no `/etc/hosts` needed). See §12/13 and Step 6.
- **CORS (verify-at-implementation):** the browser Tailscale client receives
  the DERP map **inside the control-protocol netmap over the WSS control
  channel** (headscale v0.29.x serves no `/derpmap` HTTP endpoint and never
  uses the request `Host` header to build URLs), so no cross-origin request
  fetches the DERP map. However, the **current webvm README recommends adding
  CORS headers in front of Headscale**, and the one cross-origin HTTP request
  the WASM client makes — `/derp/probe` (relay latency probe) — must also be
  compatible with COEP `require-corp`. Source-checked: the probe is issued in
  **CORS mode** (Go's js/wasm `net/http` transport; the no-cors transport is
  wired only to logtail), so headscale's own `Access-Control-Allow-Origin: *`
  on `/derp/probe` should satisfy COEP with no nginx change — but **re-verify
  against the pinned build at Phase 2 (Step 8)**; if the probe is ever issued
  no-cors, `Access-Control-Allow-Origin` alone is **not** CORP-compatible and
  the 8443 listener must add **`Cross-Origin-Resource-Policy: cross-origin`**
  (add this, plus the README's ACAO block, if the probe is blocked; relay-only
  mode still works). Headscale has **no `cors` config option**; the fallback
  is nginx `add_header` rules.

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
   (confirmed at runtime: the DERP map lists `HostName: host.docker.internal`,
   `DERPPort: 8443`). nginx therefore proxies the **entire** 8443 listener to
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
    `--login-server https://${CONTROL_HOST}:${CONTROL_PORT}`
    (path-less — matches `server_url`; §12/21(c))
    and `--authkey $GATEWAY_AUTHKEY` (reusable — see §12/12 — so a recreated
    container can rejoin) and persists its tailscaled state on a named volume.
   **Reachability (fixed — works on Linux and Docker Desktop):** because
   published ports bind only the host's loopback (`127.0.0.1`/`127.0.0.2`), a
   container reaching the host via `host-gateway` (the bridge IP, e.g.
   `172.17.0.1`) **cannot** reach them. Instead the `server` container gets a
   **static IP on a fixed-subnet compose network** (e.g. `172.28.0.10` on
   `172.28.0.0/16`) and the gateway maps `host.docker.internal` to it via
   `extra_hosts: ["host.docker.internal:172.28.0.10"]` — so `--login-server`
   and the DERP-map URL (`https://host.docker.internal:8443/…`) resolve to the
   server container over the compose network, and the loopback-published host
    ports stay host-only. **Verify at implementation** that the `extra_hosts`
    entry wins over Docker Desktop's engine-provided `host.docker.internal`
    alias (glibc uses the first `/etc/hosts` match; the engine's alias is
    served via its embedded DNS, which `/etc/hosts` overrides — but confirm
    it); **fallback:** add the server container's static compose-network IP
    (e.g. `172.28.0.10`) to the cert SAN and point the gateway at
    `https://172.28.0.10:8443` directly. The gateway also **trusts
    the private CA** for control/DERP TLS: mount `./certs:/certs:ro` and set
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
  use sets `LAN_IP=<lan-ip>`.
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
  **compiled-in** to log to logtail's default endpoint — `wasm_js.go` builds
  `logtail.Config{Collection: logpolicy.NewConfig(logtail.CollectionNode)…}`,
  and `logtail.Config.BaseURL` defaults to `https://log.tailscale.com`
  (collection `tailnode.log.tailscale.io`; re-check the exact endpoint in the
  pinned build) — so the nginx site header
  `Content-Security-Policy: connect-src 'self' https://${CONTROL_HOST}:${CONTROL_PORT} wss://${CONTROL_HOST}:${CONTROL_PORT}`
  rejects the logtail fetch (CSP applies to no-cors fetches too). Note that
  headscale **can** influence client logging in one way: its netmap carries
  `Debug.DisableLogTail` (`true` when `logtail.enabled: false`, the default),
  so a compliant client may disable logtail without any CSP block — either way
  the guarantee holds (never attempted *or* blocked), and the CSP header is the
  belt-and-braces that does not depend on client behavior. Logtail
  failures are **non-fatal** to the Tailscale client, so blocking it costs only
  browser-side diagnostics (which would have gone to a third party anyway).
  Result: the page and the WASM client make **zero external requests** — DevTools
  and the E2E no-egress test assert this (§9.4); the host firewall drops egress
  beyond RFC1918 as a second layer. (The one caveat: logtail is only reachable
  when the LAN itself has public internet; without internet the attempts fail
  silently either way.)
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
│  ├─ python-examples/      # curriculum scripts baked READ-ONLY into ~/python-examples
│  ├─ scripts/99-screen-resize.sh
│  ├─ config/               # xinitrc, i3 config (file-manager autostart), .Xresources
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
│                           # UDP ${STUN_PORT:-3478}; gateway extra_hosts
│                           # host.docker.internal:<server-ip> + state volume;
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
`FROM docker.io/i386/alpine:3.17`; build-only DNS; point **both** repositories
at v3.17 (**REVISED at implementation: the CDN, not the archive** — the
archive host does not resolve and the CDN still serves v3.17; the base image
ships `dl-cdn.alpinelinux.org` URLs for `main` — **rewrite, don't append**):
```
cat > /etc/apk/repositories <<'EOF'
https://dl-cdn.alpinelinux.org/alpine/v3.17/main
https://dl-cdn.alpinelinux.org/alpine/v3.17/community
EOF
```
then:
```
apk add --no-cache alpine-base udev-init-scripts udev-init-scripts-openrc eudev \
  xorg-server xinit xf86-input-libinput xrandr i3wm font-dejavu \
  python3 python3-tkinter xterm pcmanfm git openssh-client-default \
  busybox-extras dbus
```
(**dbus** added to the package list — the reference's `rc-update add dbus`
needs the package installed.)
(Verified against the v3.17 x86 index: the package names are **`xinit`**
(not `xorg-xinit`) and **`openssh-client-default`** (not `openssh-client`).)
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
    usr/bin/idle3.10 usr/lib/python3.10/idlelib && rm -f python3-idle-*.apk
  ```
  This installs the **`idle3.10` binary + `idlelib`** (the package provides
  only `/usr/bin/idle3.10` — **there is no `idle3`**), skipping the test-suite
  dependency. (Do **not** add the `python3-tkinter-tests` package either.)
  The rootfs smoke tests assert `idle3.10` (display-free: binary presence +
  `python3 -c "import tkinter, idlelib"`).
- Sync agent (selected by `ARG STORAGE_BACKEND`, installed only for
  `samba`/`webdav`): **samba** → the **`pysmb`** pure-Python agent by default
  (~0.5 MB installed, pip-installed at build; **pin its version** for
  reproducible builds and smoke-test it against Python 3.10 and a real Samba
  share early — it is unmaintained; `smbprotocol` at ~5–6 MB only if SMB3 is
  needed; `samba-client` at ~25 MB only as a compatibility fallback — see §4
  Mode B); **webdav** → no extra package (Python stdlib `urllib`;
  `curl` optional); **browser**/**none** → nothing extra. **`gvfs-smb` is
  excluded** (no pcmanfm `smb://` browsing — gio-only, does not help IDLE/Tk
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
   `/home/user/.xinitrc` (`exec i3`); `config/i3` → `/home/user/.config/i3`
   (autostart `open-file-explorer.sh`, `$mod+Return`→xterm,
   `$mod+Shift+f`→`open-file-explorer.sh`); optional `.Xresources`; keyboard
   layout via
   `setxkbmap` in `.xinitrc`. (The sync agent is a **single process started by
   `desktop.start`** — not an i3 autostart — so the boot pull and the push loop
   cannot race, §4.)
- X bootstrap without a seat manager: rely on udev + group membership
  (`video`/`input`/`tty`, added above) for the emulated DRM/input devices;
  create `XDG_RUNTIME_DIR=/run/user/1000` (owned by `user`, plus optional dbus
  session) in `/etc/local.d/desktop.start` so the Tk apps (file explorer, IDLE)
  behave. **Enable the openrc `local` service**
  (`rc-update add local default`) so `/etc/local.d/*.start` actually runs.
- Boot to X: `/etc/local.d/desktop.start` starts X **as the `user` account**
  (`su user -c startx` — never as root: Xorg refuses root, and i3's autostarts
  must land in `/home/user`); if `startx`'s VT handling misbehaves in the WASM
  guest (no real TTYs), fall back to launching `Xorg :0 -nolisten tcp
  -noreset` as `user` and then i3. Ultimate fallback: the LightDM-autologin
  reference setup (add `lightdm` then). **In `samba`/`webdav` modes,
  `desktop.start` runs the boot pull FIRST (wait-for-tailnet retry loop, up to
  ~90 s, every 5 s) and only then starts X as `user`** (see Step 9).
- **Guest NIC config (open spike, Phase 2 Step 8):** how the guest's emulated
  NIC comes up is **not established by the reference**: the `alpine-image`
  Dockerfile enables no networking service (`rc-update` adds only bootmisc/
  udev*/dbus/lightdm) and bakes no `/etc/network/interfaces`, and the CheerpX
  docs do not document the guest side of the emulated NIC. Treat this as a
  **blocking spike**: verify `eth0` comes up once the browser Tailscale client
  connects; if it needs configuration, **bake it in at image build time** —
  `/etc/network/interfaces` (static address + default route via the CheerpX
  gateway) or enable `udhcpc` for `eth0` — and make the sync agent's
  wait-for-tailnet loop tolerate the extra boot time.
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
`/sbin/init`, `/home/user/.config/i3/config`, `/usr/bin/idle3.10`, the sync
agent, **and ownership** (`/home/user` = `1000:1000`, busybox applet symlinks
intact, setuid bits preserved).

**Expected size (grounded — computed 2026-08-09 from the v3.17 x86
`APKINDEX` installed sizes plus the base image's own `/lib/apk/db/installed`;
the dependency closure was resolved over main + community, including
`so:`/`cmd:`/`pc:` providers):**

| Component | MiB |
|---|---|
| Base `i386/alpine:3.17` | 7.5 |
| Step 2 `apk add` dependency closure (166 packages) | ≈ 190 |
| idlelib extraction (`python3-idle` contents, no `python3-tests`) | +3.3 |
| guest overlays/configs (`syncrc`, i3/xinitrc, agent scripts) | +0.1 |
| `pysmb` (samba mode only) | +0.5 |
| Step 2 trimming (strip `doc/man/info`, trim locale — measured at build) | −≈10 |
| **Rootfs** | **≈ 190** |
| **ext2** (rootfs + ~20% headroom, 4 KiB blocks) | **≈ 230** (rounds to ~240) |

(**Measured at implementation, 2026-08-09:** browser-mode rootfs ≈ 197 MiB and
the ext2 built at 209–246 MiB depending on the backend/trimming — consistent
with the estimate. The SSH keypair is **not** part of the image anymore: it is
generated at first boot by `desktop.start`, so the image has no baked key.)

Per-package breakdown of the apk closure (installed MiB; the 109 packages
below 0.5 MiB total ≈ 12.0):

| Package | MiB | Package | MiB |
|---|---|---|---|
| `python3` | 47.8 | `font-dejavu` | 17.9 |
| `py3-pip` | 13.8 | `gtk+3.0` | 10.9 |
| `py3-setuptools` | 6.6 | `git` | 6.4 |
| `tcl` | 5.2 | `font-misc-misc` | 5.1 |
| `xkeyboard-config` | 4.0 | `glib` | 3.8 |
| `xorg-server` | 3.3 | `libx11` | 3.1 |
| `tzdata` | 3.0 | `openssh-client-common` | 2.7 |
| `shared-mime-info` | 2.4 | `libstdc++` | 2.3 |
| `tk` | 2.2 | `openrc` | 2.1 |
| `gnutls` | 2.0 | `harfbuzz` | 1.8 |
| `libunistring` | 1.7 | `hicolor-icon-theme` | 1.5 |
| `python3-tkinter` | 1.4 | `hwdata-pci` | 1.3 |
| `eudev` | 1.3 | `p11-kit` | 1.2 |
| `libxml2` | 1.2 | `sqlite-libs` | 1.1 |
| `libjpeg-turbo` | 1.1 | `libfm` | 1.1 |
| `cairo` | 1.1 | `libxcb` | 1.0 |
| `libepoxy` | 1.0 | `xterm` | 0.9 |
| `openssh-client-default` | 0.9 | `i3wm` | 0.9 |
| `py3-parsing` | 0.8 | `freetype` | 0.8 |
| `encodings` | 0.8 | `brotli-libs` | 0.8 |
| `pcre2` | 0.7 | `pango` | 0.7 |
| `libcurl` | 0.7 | `fontconfig` | 0.7 |
| `ca-certificates` | 0.7 | `pixman` | 0.6 |
| `nettle` | 0.6 | `mesa-gl` | 0.6 |
| `libwebp` | 0.6 | `cups-libs` | 0.6 |
| `zstd-libs` | 0.5 | `tiff` | 0.5 |
| `openssh-keygen` | 0.5 | `ncurses-libs` | 0.5 |
| `libinput-libs` | 0.5 | `libdrm` | 0.5 |
| `at-spi2-core` | 0.5 | *(109 smaller packages)* | *12.0* |

This is far under the ≤ 2 GB cap and about a third of the reference
`alpine-image` (which adds gcc/nodejs/LightDM/gvim/rofi/polybar etc.). The two
discretionary chunks are `py3-pip` (~20 MiB incl. `py3-setuptools`, kept so
`pip` exists) and the X font set (`font-dejavu` + `font-misc-misc` +
`encodings` ≈ 24 MiB; `font-dejavu` is required for Tk/IDLE).

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
- **URL-hash handling (all secrets out of the hash):** on page load read
  `authKey`, `controlUrl`, and (webdav mode) `syncUrl`/`syncUser`/`syncPass`
  **from the URL hash only** (no auto-derived default — §4 Mode C), move them
  to `sessionStorage`, then strip the hash via `history.replaceState` so **no
  secrets (including the preauth key) persist in browser history**. **Ordering
  requirement:** `network.js` (stock webvm reads the hash at module-import
  time, before any component code runs) is adapted to read the values from
  `sessionStorage`, and the hash→sessionStorage move must run in a small inline
  script at the top of `app.html` **before** the app bundle is evaluated, so
  the strip cannot race the read. Write the sync config into a `DataDevice`
  before `CheerpX.Linux.create` (read-only from the guest's perspective) via
  the documented `dataDevice.writeFile(path, contents)` API — **mount the
  device at `/opt` and call `writeFile("/syncrc", …)` so the guest sees the
  file at `/opt/syncrc`** (`writeFile` paths are relative to the device root;
  mounting at `/opt/syncrc` would yield `/opt/syncrc/syncrc`) — **spike
  first** against the pinned CheerpX version (§4 Mode C); if unavailable, the
  agent uses the baked `/root/.syncrc` fallback. **UX caveat:** after the
  strip, the only copy of the network params lives in that tab's
  `sessionStorage`; reopening the stripped URL in a new tab silently boots a
  *disconnected* session, so the full hash URL must be saved by the user (the
  server entrypoint prints it — the full hash URL carries the credentials, so
  treat it like a password in terminal scrollback).
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
    `gateway` maps `host.docker.internal` to it (§5.2) — no host interface
    involved.
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
- **CORS (verified at implementation, §5/§12/21(d)):** `/derp/probe` IS a
  CORS-mode fetch answered by headscale with `Access-Control-Allow-Origin: *`
  (verified at runtime) and the 8443 listener also sets
  **`Cross-Origin-Resource-Policy: cross-origin`** as belt-and-braces — no
  further CORS headers are added (unit test asserts none are expected).
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
  nginx → (webdav mode) wsgidav.

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
  `--login-server https://${CONTROL_HOST}:${CONTROL_PORT} --authkey $GATEWAY_AUTHKEY`
  (a **separate node key** from the browser `HEADSCALE_PREAUTHKEY`; **both keys
  are reusable** so a recreated gateway can rejoin; **no `--advertise-routes`**,
  no exit node). The service mounts a **named volume for tailscaled state**
  (`/var/lib/tailscale`) so the node key survives container recreation. The
  gateway reaches the control plane/DERP via
  `extra_hosts: ["host.docker.internal:<server-static-ip>"]` (e.g. `172.28.0.10`)
  — this works on Linux and Docker Desktop alike and does **not** depend on
  the host-published loopback ports (§5.2). **Verify at implementation** that
  the `extra_hosts` entry wins over Docker Desktop's engine-provided
  `host.docker.internal` alias (glibc uses the first `/etc/hosts` match; the
  engine's alias is served via its embedded DNS, which `/etc/hosts` overrides —
  but confirm it); **fallback:** add the server container's static compose-
  network IP (e.g. `172.28.0.10`) to the cert SAN and point the gateway's
  `--login-server`/DERP at `https://172.28.0.10:8443` directly, removing the
  alias dependency — and **trusts the private CA** via
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

#### Step 8 — TLS spike + route re-verification
1. TLS spike: install the private CA in the browser (single-machine uses
   `https://127.0.0.1:<SITE_PORT>` — same CA, same one-time step); confirm the
   **site loads over HTTPS with cross-origin isolation intact** and the guest's
   network panel shows CONNECTED with a tailnet IP over WSS.
2. CORS/COEP re-check (the §5 "no CORS needed" premise): confirm `/derp/probe`
   returns `Access-Control-Allow-Origin: *` **and** is COEP-compatible from
   the page (the pinned wasm client issues it in CORS mode, which should
   suffice); if blocked, add **`Cross-Origin-Resource-Policy: cross-origin`**
   (plus the webvm-README ACAO block) to the 8443 listener (relay-only mode
   still works).
3. Route check (cheap re-verification of the source findings), **backend-aware**:
   from the guest, the relayed port for the running backend must succeed —
   `nc -z <gateway-tailnet-IP> ${WEBDAV_PORT}` in `webdav` mode (default 8082),
   `nc -z <gateway-tailnet-IP> 445` in `samba` mode, `nc -z
   <gateway-tailnet-IP> 2222` only when a git SSH relay is configured — and
   `nc -z <raw-LAN-IP> 445` must fail (no subnet routes — expected). (`nc` is
   already provided by the base busybox in the guest — no extra package needed.)

### Phase 3 — Guest sync agents + full validation

#### Step 9 — Guest sync agent (per `STORAGE_BACKEND`)
Read the endpoint config (runtime-injected `/opt/syncrc` if present, else the
baked `/root/.syncrc` — functional out of the box via build args, Step 2).
Cadence: **pull on boot before the desktop starts** (Step 2:
`/etc/local.d/desktop.start`, wait-for-tailnet retry ~90 s, best-effort — X
starts regardless; runs as `user`), then **push right after writes** (scan
`~/` every ~5 s; push a debounced ~2 s per-file delta on any change), plus a
**final push on shutdown** (best-effort `beforeunload`/`SIGTERM`; unreliable
in a WASM guest, so the write-triggered push is the effective recovery point).
**Push uses the same per-file mtime manifest as pull** (PUT only the changed
files, keyed by the manifest; a full `~/` tarball is uploaded only when no
manifest exists yet) — keeps round-trips low over the WebSocket tunnel and
avoids re-uploading an entire large home. Pull decisions compare backend mtimes
against the local **last-push** record (not wall-clock "now") to stay correct
under clock skew (§4).
Concurrency: the browser session guard (§4) serializes live tabs on the shared
overlay; the agent additionally acquires a **backend lease** (heartbeat ~15 s,
expiry ~90 s) before enabling sync — refuse to sync if another live session
holds it; pull only files whose backend mtime is newer (per-file manifest via
**PROPFIND** in webdav mode / **SMB metadata** in samba mode — PROPFIND is the
WebDAV mechanism only, wsgidav supports it); push clears/releases the lease on
shutdown.
**REVISED at implementation (nested files + safety, §12/21):** the per-file
manifest covers **subdirectory files** — the WebDAV listing is recursive
(`Depth: infinity`) and the SMB listing walks the share tree; before each
nested PUT the agent **MKCOLs the parent collections** (WebDAV/SMB return
`409 Conflict` for a PUT whose parent does not exist). Pull is
**non-clobbering**: the first-sync snapshot restore skips members that already
exist locally, and per-file pulls never overwrite pre-existing unrecorded
files, so a crash after local edits is never clobbered by an older snapshot.
Remote listings are treated as untrusted input: `..`/absolute/empty-segment
paths are rejected and `EXCLUDE_NAMES` (`.ssh`, `.cache`, `.syncrc`, …) are
not pulled.
- **samba mode:** target `//<gateway-tailnet-IP>/<share>` (port 445 relayed to
  the LAN Samba server) via the **`pysmb`** agent (default; `smbprotocol` or
  `smbclient` only if the share requires SMB3/other features — §4 Mode B). The
  per-file mtime manifest uses SMB file timestamps (pysmb
  `listPath`/`getAttributes`), not WebDAV PROPFIND.
- **webdav mode:** target `http://<gateway-tailnet-IP>:<WEBDAV_PORT>/webdav/` via
  Python stdlib (`urllib` PUT/GET/PROPFIND; basic auth) or `curl`; the URL comes
  from the URL-hash `syncUrl` when injection is available, else the baked
  `/root/.syncrc` (remapped ports then need an image rebuild).
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
`webdav` build and the `tailnet` profile up** (`make up-tailnet`); open
`https://${CONTROL_HOST}:<SITE_PORT>/alpine.html#authKey=…&controlUrl=https://${CONTROL_HOST}:<CONTROL_PORT>&syncUrl=http://<gateway-tailnet-IP>:<WEBDAV_PORT>/webdav/…`
— with `CONTROL_HOST=host.docker.internal` for single-machine (needs the
`/etc/hosts` entry `127.0.0.1 host.docker.internal` on the browser machine) or
`CONTROL_HOST=<LAN_IP>` on a LAN; `browser`/`none` sessions use
`https://127.0.0.1:<SITE_PORT>/alpine.html`. The full hash URL (printed by
`make url`) carries the preauth and WebDAV credentials — treat it like a
password in terminal scrollback/logs. Use `docker compose logs -f` and
`make down`.

## 8. CI testing (GitHub Actions)

The repo is public, so CI runs on every push/PR. **CI has no secrets or LAN
config**: the guest Dockerfile and scripts must build with placeholders (no
`/root/.syncrc`, no real SSH key — CI generates a throwaway key or skips baking
those; defaults come from build args). `.github/workflows/ci.yml`:

**Jobs (all `runs-on: ubuntu-latest`):**
1. **guest-image** — build the guest and its ext2 image across backends:
   - `docker/setup-buildx-action` with layer caching. `docker/setup-qemu-action`
     is **optional belt-and-braces**: i386 binaries run natively on the amd64
     runner (x86-64 hardware compatibility mode + kernel compat ABI; the i386
     image carries its own 32-bit libraries), so QEMU is not required for
     `linux/i386`. Some BuildKit/builder configurations route non-amd64
     platforms through binfmt, so keeping the QEMU action is harmless.
   - Matrix `STORAGE_BACKEND: [browser, samba, webdav, none]`:
     `docker build --platform=linux/i386 --build-arg STORAGE_BACKEND=${{ matrix.backend }} diskimage`.
    - Install `e2fsprogs`, run `build.sh`, then verify the ext2 with `debugfs`:
      `/sbin/init`, `/usr/bin/idle3.10`, `/home/user/.config/i3/config`, sync
      agent, **and ownership** (`/home/user` = `1000:1000`, setuid bits intact —
      the untar must run as root on a container-local path, §6 Step 3).
    - Upload the ext2 **and its content fingerprint** (`sha256sum`, computed by
      `build.sh` — §12/10) as a workflow artifact (`actions/upload-artifact`):
      the `frontend` job needs the fingerprint for `WEBVM_IMAGE_BUILD` and the
      `server` job needs the image itself (Step 4/Step 6).
2. **frontend** — `actions/setup-node` (Node 20, npm cache) + rewrite the
   `labs` dependency to HTTPS **before** `npm ci` (the committed lockfile must
   match the rewritten `package.json` — Step 5) + `npm ci` (committed lockfile
   with the pinned cheerpx version) + download the guest-image artifact
   (`actions/download-artifact`) and export its fingerprint as
   `WEBVM_IMAGE_BUILD` so the built cacheId matches the served image
   (Step 4) + `npm run build` in `webvm/`; upload the `webvm/build` output.
   The build must perform **no external fetch** beyond npm — after the Step 4
   edits the prerender no longer loads `labs.leaningtech.com` blog posts, so
   the job is deterministic.
3. **server** — `docker compose config -q` (validates the compose file and its
   inline defaults; secrets are `${VAR:-}` so this passes with none set);
   **generate a throwaway private CA + server cert into `certs/` first** (SAN:
   `host.docker.internal`, `127.0.0.1`, `localhost` — required before `docker
   compose up`, since nginx serves HTTPS from `/certs`); **download the
   `frontend` build artifact and the guest-image ext2 artifact and place them
   where the `server/Dockerfile` consumes them** (declared build contexts for
   `build/` and `custom-disk-images/` — the served `alpine.html` and the ext2
   must be inside the server image);    **export throwaway
   secret values** (`WEBDAV_USER`/`WEBDAV_PASS`, `HEADSCALE_PREAUTHKEY`,
   `GATEWAY_AUTHKEY`) for the webdav-mode stack; set `CONTROL_HOST=host.docker.internal`
   and `LAN_IP=127.0.0.1` via a temporary `.env`
   override (or exported shell vars) since CI is not on a LAN — the defaults
   already make this loopback-safe (§5); the gateway (and the §9.3 test client)
   reach the control plane via the **static server IP** (`extra_hosts:
   host.docker.internal:172.28.0.10`, §5.2) and trust the CA via
   `SSL_CERT_FILE=/certs/ca.crt`; add `127.0.0.1 host.docker.internal` to the
   runner's `/etc/hosts` (for the browser-side paths); `docker compose build`;
   **bootstrap the preauth keys in the fresh headscale DB before the real
   `up`** — start the server once with `HEADSCALE_BOOTSTRAP=1` (the
   implementation's first-run override), run
   `docker compose exec server headscale preauthkeys create --user 1
   --reusable --expiration 100y` twice (v0.29.x takes the **numeric** user id;
   the entrypoint creates the `headscale` namespace first, id 1), and export
   the **printed** values as
   `HEADSCALE_PREAUTHKEY`/`GATEWAY_AUTHKEY` (headscale generates the key
   values and the entrypoint fails closed if the `.env` keys are absent from
   the DB — §6 entrypoint);
   smoke test: bring the stack up with **`STORAGE_BACKEND=webdav`** (or
   `HEADSCALE_ENABLED=1` + `make up-tailnet`) so the control plane runs, then
   `curl -k` — COOP/COEP/CORP headers on `/alpine.html` over HTTPS (`/` → 302
   `/alpine.html`), `GET /custom-disk-images/webvm-custom-disk.ext2` → 200 with
   a Range request → `206 Partial Content`, and a WebDAV **PROPFIND/PUT/GET
   round-trip with basic auth against wsgidav**; the Headscale join test (§9.3)
   runs against this stack; `docker compose down`.
4. **lint** — `shellcheck` on `build.sh`, `server/entrypoint.sh`, and the guest
   sync scripts; `yamllint` on `compose.yaml` and the workflow. Locally these run
   via Docker images (`koalaman/shellcheck`, `cytopia/yamllint`) — no host
   installs.

**Notes:**
- The i386 guest build runs **natively** on the amd64 runner (no QEMU needed —
  x86-64 runs 32-bit code in hardware compat mode); the main CI costs are the
  four-backend matrix and image size, mitigated with buildx layer caching.
- **Host-dependent:** on an **Apple Silicon (arm64)** dev machine (e.g. this
  one — Docker Desktop 4.85, engine 29.6.2, Compose v5.3.1, Buildx v0.35),
  `linux/i386` runs under Docker Desktop's **bundled QEMU/binfmt** (verified:
  `docker build --platform=linux/i386` + `docker run` succeed, `uname -m` →
  `i686`), so local guest builds are emulated and slower than CI. Everything
  else (server image, compose, ext2 pipeline via a throwaway Ubuntu container,
  Playwright on native arm64 Chromium) is unaffected.
- CI builds the *artifacts* and runs the **test suite** (§9): the private-CA/
  TLS and control-plane spike is covered by the Headscale-join integration test
  and the E2E control-plane check; subnet-route acceptance is source-resolved
  (`RouteAll=false`), and only the socat-relay path is validated (manual in §10).
- **Tailnet tests need a host both ends can resolve:** the browser (runner
  `/etc/hosts`) resolves `CONTROL_HOST=host.docker.internal` to `127.0.0.1`,
  and the `gateway`/client containers map it to the **server container's
  static compose-network IP** via `extra_hosts` (§5.2); `server_url` renders
  the same value, so headscale's DERP-map relay URL is reachable from both
  sides.
- Optionally upload the ext2 image as a workflow artifact (`actions/upload-artifact`)
  for manual download; do **not** publish it to a package registry.

## 9. Test suite (unit → integration → E2E)

Goal: prove the system "definitely works". Because the VM only truly runs in a
browser, the suite is layered — fast deterministic checks first, a real browser
E2E as the gate — plus a LAN acceptance script for what CI cannot reach. CI jobs
live in `tests/` (wired into §8); run everything locally with `make test` and
`make acceptance`.

### 9.1 Unit tests (CI, fast) — `tests/unit/`
- **Sync agent** (`sync.py`): snapshot create/extract; non-destructive pull
  (per-file mtime manifest compared against the **last-push record** — only
  files whose backend copy is newer are overwritten); change detection +
  debounced push (the ~5 s poll → ~2 s push-on-write behavior); lease
  acquire/refresh/expiry and single-session refusal; endpoint config parsing
  (`/opt/syncrc` vs baked fallback); WebDAV client PUT/GET/PROPFIND against a
  local fake server fixture; SMB client behind an interface mock.
- **Templates/entrypoint**: `envsubst` renders the expected nginx/Headscale/
  wsgidav configs from given env — assert COOP/COEP/CORP, the HTTPS site block,
  the **CSP `connect-src` header containing only `'self'` + the control host**,
   the `/webdav/` block with `WEBDAV_ROOT`, LAN-bound published ports, the
   control-plane nginx routing (**path-less catch-all `location /` proxy to
   headscale with WebSocket upgrade headers** — `Upgrade`/`Connection:
   $connection_upgrade`, `proxy_buffering off`, timeouts), the site redirects
   (`/` → `/alpine.html`, `/alpine` → `/alpine.html`), the (path-less)
   `server_url`/DERP relay addresses derived from
   `CONTROL_HOST`, and the per-mode secret checks (fail-closed in
   webdav/tailnet modes, no-op in browser/none). (No CORS headers are expected
  by default — re-verified at Phase 2, §5/Step 8.)
- **Frontend session guard** (optional, vitest): lock acquire/contention/
  heartbeat/expiry and ephemeral-overlay fallback logic.
- **Script hygiene**: `sh -n` on `build.sh`/`entrypoint.sh`/guest scripts;
  `python3 -m py_compile` on the sync agent.
- `pytest` via a compose `test-unit` service (e.g. `python:3-alpine` with
  pytest installed in the image) — no host Python packages needed; no network,
  seconds.

### 9.2 Rootfs smoke tests (CI) — `tests/rootfs/`
Run against the built guest image and ext2 (i386 runs natively on the amd64
runner):
- `docker run --rm --platform=linux/i386 webvm-guest …` asserts:
  - `python3 -c "import tkinter, idlelib"` succeeds; **`/usr/bin/idle3.10`
    exists** (there is no `idle3`; skip a display-dependent `idle3.10 --help`
    check — the E2E covers a real IDLE launch).
  - `command -v xterm i3 git ssh nc` present and **`pcmanfm`/`spacefm` absent**
    (replaced by the Tk file explorer, §12/25; `nc` comes from the base
    busybox, not `busybox-extras`).
  - **curriculum packages are absent:** `python3 -c "import numpy"` and
    `import requests` and `import pytest` each fail (guards against
    re-adding the removed packages); `python3 -c "import pip"` succeeds.
  - i3 config autostarts `open-file-explorer.sh` (the file explorer) and
    `keep-file-explorer.sh` (the keep-alive); `~user/.xinitrc` execs i3; the
    sync agent is a single process started by `desktop.start` (samba/webdav
    builds).
  - **In-guest GUI suite:** `tests/rootfs/smoke.sh` runs the full
    `file-explorer-tests.py` suite (every explorer function, including the
    withdraw→IDLE→reappear flow) under an in-image `Xvfb` on `DISPLAY=:99` and
    requires a `PASS ALL` result.
  - `/sbin/init`, the openrc `local` service (enabled via `rc-update add
    local default`), and `/etc/local.d/desktop.start` (`sh -n` valid; starts X
    via `su user -c startx`); `/etc/X11/xinit/xinitrc.d/99-screen-resize.sh`
    present; in `samba`/`webdav` builds `desktop.start` runs the sync boot-pull
    before X.
  - git config, SSH key, `/root/.syncrc` present (CI placeholders via build-arg
    defaults).
- Ext2: `e2fsck -f` clean; `debugfs` shows `/sbin/init`, `/usr/bin/idle3.10`,
  the i3 config, and the sync agent.

### 9.3 Server integration tests (CI) — `tests/server/`
- `docker compose config -q`; bring the stack up; assert nginx serves COOP/COEP/
  CORP headers over HTTPS (`curl -k`), `/alpine.html` → 200 and `/` → 302 to
  `/alpine.html`, `GET /custom-disk-images/webvm-custom-disk.ext2` → 200 with a
  Range request → `206`, the control listener
  answers `/derp/probe` with `Access-Control-Allow-Origin: *`, and (webdav
  mode) a WebDAV **PROPFIND/PUT/GET round-trip with basic auth against
  wsgidav**.
- **Headscale join test** (validates the control plane + private-CA TLS without
  a browser): run a `tailscaled` client container that trusts the test CA
  (`SSL_CERT_FILE=/certs/ca.crt`, `./certs` mounted) and
  `--login-server https://${CONTROL_HOST}:${CONTROL_PORT}`
  (`CONTROL_HOST=host.docker.internal` in CI, with `extra_hosts:
  host.docker.internal:<server-static-ip>` so the container reaches the control
  plane over the compose network — **not** the loopback-published host port)
  and assert it registers (`headscale nodes list`) and that tailnet
  connectivity to a second node works (via the embedded DERP, whose relay URL
  headscale derives from `server_url`).
- Assert no exit node is advertised anywhere.

### 9.4 E2E tests (Playwright; CI + local) — `tests/e2e/`
The definitive check — boot the real VM in headless Chromium against the
running server (`@playwright/test`, `npx playwright install --with-deps chromium`,
`ignoreHTTPSErrors: true` for the private CA since the site is HTTPS):
- **boot**: page loads over HTTPS with cross-origin isolation intact; the
  display canvas becomes non-blank within a generous timeout (desktop
  rendered); no console errors (**allowlist the CSP `connect-src` violation
  warning from the blocked logtail fetch, if it appears** — a compliant pinned
  client may self-disable logtail via headscale's netmap `Debug.DisableLogTail`,
  in which case no warning fires; either way the assertion is "no *successful*
  external request", not "a warning occurs"). The
  `browser`-mode matrix case is opened with **no `authKey`/`controlUrl`**
  (disconnected session — asserting no auto-login attempt occurs); network
  params are only present in the `webdav` case.
- **no-egress**: intercept all requests **and WebSockets** (`page.route` for
  HTTP(S) plus `page.on('websocket')` — the control connection is a WebSocket
  that `page.route` does not see) and assert every URL is **same-origin (the
  site) or the control host/port** (`${CONTROL_HOST}:${CONTROL_PORT}` — the
  control WSS, `/derp`, `/derp/probe`). **There are no external hosts at all** —
  in particular the browser must make **no** successful request to the logtail
  endpoint (assert the logtail fetch is blocked by the CSP `connect-src`), the
  plausible/fonts/**serviceWorker** script tags are stripped, the **blog-post
  images are gone and the Claude/AI sidebar entry is absent** (open the Posts
  sidebar entry and re-assert zero external requests; assert no Claude sidebar
  entry or `anthropic.js` import exists), and the `/` route is
  redirected to `/alpine.html` (§4/Step 4/Step 6).
- **control plane**: in network modes, `headscale nodes list` gains a new
  ephemeral node (proves browser Tailscale + WSS control + DERP + certs).
- **sync (webdav mode)**: within ~2 min the lease file and a `~/` snapshot
  appear on the WebDAV backend; reload the page → pull runs (snapshot timestamps
  advance / content round-trips).
- **persistence**: `browser` mode — the overlay's persistent browser store is
  non-empty after boot and after a reload (assert `indexedDB.databases()` if
  the pinned build uses `IDBDevice`, or the OPFS store if it uses
  `OpfsDevice` — §2); `none` mode — fresh session does not reuse prior data.
- **single-session guard**: opening a second tab while the first is live shows
  the ephemeral-mode notice and does not write to the shared overlay.
- Matrix over `[browser, webdav]`; samba/none logic is covered by unit + rootfs
  (Samba E2E needs a live Samba server → LAN acceptance only).
- CI: allow retries and use long timeouts (WASM boot is slow); this job is the
  gate that must pass.

### 9.5 Local / LAN acceptance — `scripts/acceptance.sh`
Semi-automated, run on the LAN host after `make up`; covers what CI can't:
private-CA browser trust for the **site and control plane**, socat relay
reachability from the guest, Samba share connect via the relay, the no-internet
proofs (public IP unreachable, raw LAN IP unreachable, no exit node), port-remap
round-trip, host firewall checks — plus a printed checklist for the visual
items (desktop renders, IDLE usable, canvas resize).

## 10. Manual & LAN acceptance

Manual complements to the automated suite in §9, for the checks that need a
real LAN, a private-CA-trusting browser, or human eyes (run via
`scripts/acceptance.sh`):

1. Site access paths: **`https://127.0.0.1:<SITE_PORT>/alpine.html`** (single
   machine; CA installed once) and **`https://<LAN_IP>:<SITE_PORT>/alpine.html`**
   (CA installed on the device) both serve COOP/COEP/CORP headers; `/` →
   302 `/alpine.html`; `.ext2` Range → 206 on both.
2. **No-internet:** DevTools shows only LAN/container requests — **zero
   external hosts**, including the blocked logtail fetch
   (CSP `connect-src`); from the
   guest, a public IP is unreachable while relayed services (Samba/git/WebDAV
   via the gateway's tailnet IP) answer; a raw LAN IP is unreachable (no
   subnet routes); no exit node exists in the headnet. Confirm the guest URL
   never carries `#authKey` without a matching `controlUrl` (that would
   auto-register with public Tailscale).
3. TLS/control: guest shows CONNECTED with a tailnet IP over WSS.
4. Desktop: the file manager auto-opens on `~/`; xterm + pcmanfm launch; a
   new `.py` can be created (File ▸ Create New) and opened in IDLE; closing
   the last window relaunches the file manager; canvas resize works.
5. **Storage sync (per backend):**
   - Samba: from the guest connect to `//<gateway-tailnet-IP>/<share>`; push a
     file and verify it on the server (also browsable from the host).
   - WebDAV: push a file and verify it in the container volume at `$WEBDAV_ROOT`
     (and via a host-side WebDAV client against
     `http://<LAN_IP>:${WEBDAV_PORT}/webdav/`, PROPFIND included); confirm a
     different `WEBDAV_ROOT`/volume is honoured when configured.
   - Both: reboot the VM/tab → pull restores `~/`; a save in IDLE is pushed to
     the backend **within a few seconds** (write-triggered push), not only on a
     periodic tick.
6. **Browser/none modes:** `browser` — a file written in `~/` survives a tab
   reload (IndexedDB); `none` — it does not (ephemeral). No storage endpoint
   needed in either case. Two concurrent tabs: second tab shows the
   single-session notice and boots ephemeral.
7. **Git:** **host-side first** — set `GIT_SSH_LAN_IP` in `.env` and recreate
   the gateway to add the `2222` relay; then from inside the guest add the
   remote with the explicit-port URL
   `ssh://git@<gateway-tailnet-IP>:2222/<path>` (or an `~/.ssh/config` `Host`
   alias with `Port 2222`), then `git clone`, `git pull`, and `git push`
   against the LAN remote succeed.
8. Image size + first-load time recorded; still ≤ 2 GB.
9. **Port remapping:** change `SITE_PORT`/`CONTROL_PORT`/`WEBDAV_PORT`/`STUN_PORT`
   in `compose.yaml` (or override via `.env`) and `docker compose up -d` (no
   image rebuild); confirm the page, Headscale control, and webdav sync work
   end-to-end with the new ports (URL-hash + `syncrc` ports must match the
   gateway relays, which read the same env vars; the DERP-map relay URL follows
   `CONTROL_PORT` automatically via `server_url`; where DataDevice injection is
   unavailable, the baked `syncrc` must be rebuilt — see §4 Mode C).

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Browser Tailscale client ignores LAN subnet routes (source-verified: `RouteAll=false`) | socat TCP relays on the gateway (tailnet IP → LAN ports); join a machine to the tailnet if a port can't be relayed |
| Gateway inbound delivery in userspace mode | Source-verified: tailscaled forwards tailnet-IP:port → `127.0.0.1:port` (`netstack.go`); socat binds `127.0.0.1`; Step 8 re-verifies with `nc`; host-tailscale fallback documented |
| Browser/CA trust: private-CA site + control endpoint rejected | TLS spike first; import CA in the browser once — required for **both** the single-machine path `https://127.0.0.1:<SITE_PORT>` and LAN use (no plain-HTTP path exists) |
| **Site over plain HTTP on a LAN IP → no SharedArrayBuffer → VM won't boot** | Site always served over HTTPS with the private CA (SITE_PORT) — the only access mode; verified by E2E boot over HTTPS and §10.1 |
| nginx `dav_module` lacks PROPFIND (sync agent needs it) | Use **wsgidav** (PROPFIND/LOCK/auth); nginx dav module avoided; integration test covers PROPFIND/PUT/GET |
| IDLE packaging: `python3-idle` depends on `python3-tests` (~85 MiB) and ships only `/usr/bin/idle3.10` | Enable the **community** repo; **extract `idlelib`/`idle3.10` from the package** instead of `apk add python3-idle` (Step 2); autostart/tests use `idle3.10`; benign 3.10.11-vs-3.10.15 patch mismatch |
| X fails to bootstrap without a display manager (DRM/input perms, VT handling) | udev + `video`/`input`/`tty` groups; `XDG_RUNTIME_DIR` + dbus session; **start X as `user` (`su user -c startx`)**; direct `Xorg :0` fallback; ultimately LightDM autologin |
| Alpine 3.17 EOL mirrors | Use archive.alpinelinux.org repositories |
| SMB over the WebSocket tunnel is slow/chatty | Tarball snapshot sync (few round-trips); acceptable for IDLE-scale files |
| Guest disk fills | ext2 headroom ~20% (min 100 MB), ≤ 2 GB |
| Samba agent image size | Default is the **`pysmb`** pure-Python agent (~0.5 MB, no compiled deps); `smbprotocol` (~5–6 MB) only if SMB3 is required; `samba-client` (~25 MB closure) only as a compatibility fallback; `gvfs-smb` excluded |
| SSH host-key verification in a headless guest | `StrictHostKeyChecking=accept-new`; the host key verified is the **LAN git server's** key (the gateway only relays TCP) — if a pre-seeded `known_hosts` is desired it must hold that key, indexed by `<gateway-tailnet-IP>:2222` |
| Gateway tailnet IP changes on rejoin (breaks URLs/remotes/known_hosts) | headscale v0.29.x has **no fixed-IP mechanism** (verified); rely on persistence — headscale's SQLite DB + the gateway's tailscaled state on named volumes keep the node record (and its allocated IP) stable — and record the **actual** assigned IP as `GATEWAY_TAILNET_IP` after the first join; document the recovery path if the DB is wiped (§5.4/§6 Steps 6–7/§12/9) |
| Git over the WebSocket tunnel is slower | Acceptable for personal LAN use; use smart-HTTP if a LAN git server exists |
| Two VM tabs sync concurrently → last-writer-wins data loss / shared IndexedDB corruption | **Browser-level single-session guard** (localStorage+BroadcastChannel, all modes) + backend lease for sync agents + non-destructive pull (mtime manifest); one-session-at-a-time usage documented |
| Hidden-tab timer throttling makes the session guard stale → two writers | `BroadcastChannel` liveness (ping before takeover); heartbeat tuned below worst-case throttling |
| Rebuilt ext2 leaves stale IndexedDB overlays (deltas against an old base → corrupt FS) | **CacheId versioned to the image build** (`blocks_alpine_<image-build>`); upgrades start fresh overlays (§4/Step 4) |
| `#authKey` in the URL without a `controlUrl` auto-registers with **public Tailscale** | Never ship/use `#authKey` alone; disconnected sessions use no network params; E2E + acceptance assert this (§9.4, §10.2) |
| CheerpX `DataDevice` population API unavailable → runtime `syncUrl` injection impossible | `dataDevice.writeFile` is documented (1.2.x) — confirm against the pinned version early (§4 Mode C/Step 4); fall back to baked `/root/.syncrc` (port remapping then needs a rebuild) or a `/web`-mounted config file |
| Unpinned CheerpX/WebVM/Headscale/Tailscale drift breaks the integration | Pin webvm commit, exact `@leaningtech/cheerpx`, committed lockfile, Headscale + tailscale image tags; `labs` dep rewritten to HTTPS for CI |
| Playwright E2E is slow/flaky (WASM boot, first-run downloads, HTTPS/certs) | Retries, generous timeouts, `ignoreHTTPSErrors` for the test CA; unit/rootfs layers catch most failures first |
| Tailscale logtail telemetry from the browser (logtail default endpoint `log.tailscale.com`) | **Blocked, not permitted**: compiled-in default log policy (`logtail.CollectionNode`; headscale can only *disable* client logging via netmap `Debug.DisableLogTail`, not redirect it) → nginx CSP `connect-src` allows only `'self'` + the control host, plus the host firewall drops egress beyond RFC1918; logtail failures are non-fatal to the Tailscale client; E2E asserts **zero** external hosts (§5, §9.4, §12/20) |
| Accidental internet exposure | No exit node; **`derp.urls: []` so no public DERP**; ports published to LAN IP + loopback only (never all interfaces); host pf firewall; DevTools egress check (§10.2); stock frontend external refs (plausible/fonts/`/` route) stripped or redirected **and the blog-post images, Claude/AI tab and service worker removed** (Step 4) |
| `none` mode loses all changes on reload | By design; only use where ephemeral/read-only sessions are acceptable |
| i386 guest build in CI is slow (native compat mode, no QEMU needed) | buildx layer cache; matrix limited to the four backends |
| Cross-origin isolation misconfig | Validate headers first (§10.1); E2E boot asserts isolation |
| Duplicate host port binds when `LAN_IP` is loopback (CI/dev) | Default is loopback-safe: `LAN_IP=127.0.0.1` only (the planned `EXTRA_BIND_IP=127.0.0.2` second binding was dropped — not bindable on macOS Docker Desktop; §12/21) — no collision; CI uses the same default |
| Gateway container cannot reach the control plane/DERP over the host's loopback | **Fixed:** the `server` container gets a static IP on a fixed-subnet compose network and the gateway maps `host.docker.internal` to it via `extra_hosts` (works on Linux and Docker Desktop, independent of the loopback-published ports) + trusts the private CA via `SSL_CERT_FILE` (§5.2/Step 7) |
| STUN discovery is gateway-only (the browser client has no UDP socket) | The gateway already reaches STUN at `172.28.0.10:3478` over the compose network without any host publish; the UDP host publish is a harmless extra; relay-only is the steady state (§6 Step 6, §12/21(h)) |
| Non-reusable gateway auth key consumed on first join → recreated gateway can't rejoin | Make `GATEWAY_AUTHKEY` reusable and mount a named volume for the gateway's tailscaled state (§6/Step 7/§12/12) |
| Default headscale config points clients at Tailscale's public DERP map | `derp.urls: []` + the embedded DERP region enabled per the pinned version's schema (verify the key names — e.g. `derp.server.*`, STUN — against the pinned headscale); `disable_check_updates: true` |
| CORS misconception: "browser fetches the DERP map cross-origin" | The DERP map arrives **over the control WebSocket**; the only cross-origin HTTP request (`/derp/probe`) is issued in **CORS mode** by the pinned wasm client and answered with `Access-Control-Allow-Origin: *`, which satisfies COEP — **verify against the pinned versions at Phase 2**; if the probe is ever issued no-cors, add **`Cross-Origin-Resource-Policy: cross-origin`** (plus the README's ACAO block) to the 8443 listener (§5/Step 8) |
| nginx `/derp`/`/ts2021` missing WebSocket upgrade headers → `426 Upgrade Required` / `500` (relay + registration fail) | The control listener is a **catch-all `location /`** proxy carrying the upgrade headers + `proxy_buffering off` + timeouts; unit test asserts them; §9.3 probes `/derp/probe` (§5/§6 Step 6/§9.1) |
| SvelteKit static output path mismatch (404 on the alpine page) | Output is **`build/alpine.html`** (verified upstream); nginx serves it and redirects `/` and `/alpine` to `/alpine.html`; §9.3 asserts `/alpine.html` → 200 (Step 4/Step 6) |
| `authKey`/`controlUrl` stay in browser history (URL fragment) | Move **all** hash params (`authKey`, `controlUrl`, `syncUrl`/`syncUser`/`syncPass`) to `sessionStorage` and strip the hash via `history.replaceState` (Step 4) |
| Image upgrades orphan the old IndexedDB overlay DB | cacheId versioning starts a fresh overlay automatically; old `blocks_alpine_<oldsha>` DBs are simply orphaned (optional manual cleanup / existing Reset button) |
| Per-file mtime sync across un-synchronized clocks (guest/browser vs backend) | Pull compares backend mtimes against the local **last-push** record, not wall-clock "now"; lease heartbeat (15 s) ≪ expiry (90 s) tolerates personal-LAN clock skew (§4/Step 9) |
| Two-way sync without tombstones → deletions not propagated | Documented limitation: locally deleted files stay orphaned on the backend (never resurrected); backend deletions don't remove local files (§4) |
| Push-on-write in a WASM guest (no guaranteed inotify) | Agent polls `~/` every ~5 s and pushes a debounced ~2 s delta after changes — effectively right after writes; effective RPO ≈ seconds, not the old 60 s tick (§4/Step 9) |
| Headscale DB loss invalidates preauth keys and the gateway IP assignment | headscale's SQLite DB (`/var/lib/headscale`) on a named volume (§5.4/§6 Step 6); on DB loss, re-register the gateway, read its new IP, and update `GATEWAY_TAILNET_IP` + baked `syncrc`/remotes/`known_hosts` |
| Preauth key expiry silently breaks the saved session URL | Keys are **long-lived** (e.g. `100y`) — a 180 d expiry is unnecessary for a personal LAN key (§6 Step 6/§12/12) |
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
   runtime URL hash (§10.9).
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
18. **IDLE provisioning (revised):** `idlelib` + `/usr/bin/idle3.10` are
    **extracted from the `python3-idle` package** (`apk fetch` + `tar`) to skip
    the 85 MiB `python3-tests` dependency; IDLE is launched on demand from the
    file manager (Step 2) — rootfs tests and acceptance use `idle3.10` and the
    `idle3.10-launcher`.
19. **Control-plane hostname:** `CONTROL_HOST` (default `host.docker.internal`;
    `<LAN_IP>` for LAN use) is the single value rendered into headscale
    `server_url`, the URL-hash `controlUrl`, the gateway's `--login-server`,
    the cert SAN, and the E2E whitelist. The browser resolves it via a one-line
    `/etc/hosts` entry; the `gateway` container resolves it to the **server
    container's static compose-network IP** via `extra_hosts` (§5.2) — so the
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
    `https://${CONTROL_HOST}:${CONTROL_PORT}/derp` (confirmed: the DERP map
    entry lists `HostName: host.docker.internal`, `DERPPort: 8443`); (d) the
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
    (m) `extra_hosts` `host.docker.internal:<server-ip>` precedence over Docker
    Desktop's engine-provided alias (fallback: server static IP in the cert
    SAN + gateway pointed at `https://172.28.0.10:8443` directly);
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
      and i3 runs under `dbus-run-session` so GTK apps (IDLE, pcmanfm) get a
      per-session D-Bus.

23. **File-manager-first desktop (implementation change, 2026-08-13):** the
    autostarted desktop client is now **pcmanfm** (`exec --no-startup-id
    pcmanfm /home/user` in `config/i3/config`) instead of IDLE; IDLE is
    launched on demand from the file manager. Wire-up: new `.py` files are
    created via *File ▸ Create New* from a `~/Templates/Python Script.py`
    template; `.py` files open in IDLE on double-click via
    `~/.config/mimeapps.list` → `~/.local/share/applications/idle3.10.desktop`
    (both exec the `idle3.10-launcher`), and via the right-click *Open with
    IDLE* custom action (`~/.local/share/file-manager/actions/
    open-with-idle.desktop`, `MimeTypes=text/x-python`). `shared-mime-info` is
    added to the guest so `.py` MIME detection works. IDLE's
    `-n`-conditional launcher is unchanged and is the entry point for every
    IDLE launch. Updated: `diskimage/config/i3/config`, `diskimage/Dockerfile`,
    new files under `diskimage/rootfs/home/user/`, `tests/rootfs/smoke.sh`,
    `tests/e2e/tests/desktop.spec.js`.
    **Keep-alive (added 2026-08-13):** a pcmanfm keep-alive daemon
    (`diskimage/rootfs/usr/local/bin/keep-file-manager.sh`, autostarted by i3)
    polls the i3 layout tree (`i3-msg -t get_tree`) and relaunches
    `pcmanfm /home/user` whenever the number of real windows drops to zero, so
    the desktop never ends up with nothing open. Only leaf `con` nodes with a
    window id count (containers/bar are ignored); i3-msg failures are
    fail-safe (no relaunch).
    **SUPERSEDED (2026-08-14):** pcmanfm is replaced by the Tk file explorer
    (§12/25); the keep-alive became `keep-file-explorer.sh` and the pcmanfm
    integration files/mimeapps/template were removed.

24. **Baked-in Python examples (added 2026-08-13):** the `python-examples/`
    directory (moved from the repo root into `diskimage/`, so it is in the
    Docker build context) is copied to `/home/user/python-examples/` at image
    build time and made **read-only in the image** (`chmod 0555` dir / `0444`
    files, owned by `user` — the guest FS refuses writes even through the
    IndexedDB overlay). They are reference material to copy, never to edit in
    place. `diskimage/python-examples` is added to `build.sh`'s content
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
    autostarted by i3 via the guarded single-instance launcher
    `/usr/local/bin/open-file-explorer.sh`; `keep-file-manager.sh` became
    `keep-file-explorer.sh` (same i3-tree polling, plus a "relaunch only when
    no explorer process exists" guard so a withdrawn explorer is never
    doubled). Removed from the image: the `pcmanfm`/`spacefm`/`shared-mime-info`
    packages, the pcmanfm/libfm instrumentation and the whole `/trace`
    diagnostic tree (the Tcl/Tk `libtcl8.6.so.patched` fix stays), the
    `mimeapps.list`/`idle3.10.desktop`/`open-with-idle.desktop`/`~/Templates`
    pcmanfm integration, and the `/proc/self/mountinfo` stub in
    `desktop.start`. A starter `/home/user/hello.py` is baked in so a new user
    (and the E2E suite) can double-click into IDLE immediately.
    **Screen replacement:** "Open with IDLE" (or double-clicking a `.py`)
    launches `idle3.10-launcher` per file and then **withdraws the explorer
    window** — the whole screen is IDLE's. A watcher thread waits on the IDLE
    processes; when they all exit it reloads the current folder and
    `deiconify()`s the explorer, so anything IDLE created/edited/saved shows
    up. Closing the explorer (WM close, or the Ctrl+W shortcut added for it)
    exits the process and the keep-alive relaunches it. **Touch model
    hardening (2026-08-14):** the release handler no longer drops clicks whose
    release arrives between the old tap and long-press thresholds (CheerpX can
    delay synthetic button releases), and the long-press hold was raised from
    600 ms to 1500 ms so a delayed release still registers as a tap instead of
    firing a spurious context menu. **Testing:** `file-explorer-tests.py` was
    extended to cover every function (sorts, wheel scroll, breadcrumbs, go
    up/to-path, text sniffing, rename/create/delete/batch-rename, zip/unzip,
    status bar, the real open_selected dir/.py/.txt paths, the late-release
    tap, the Ctrl+O/Ctrl+W shortcuts, and the withdraw→IDLE→reappear flow —
    98 checks) and now runs **inside the guest** as part of
    `tests/rootfs/smoke.sh` under an in-image Xvfb (`xvfb` package added for
    this); the same smoke block runs a REAL IDLE launch and boots i3 under
    Xvfb to verify the keep-alive relaunches a killed explorer; `tests/unit`
    and CI shellcheck cover the new scripts. The E2E
    (`tests/e2e/tests/desktop.spec.js`) asserts the real-browser boot: no
    login prompt, no boot hang, and the explorer filling the canvas — synthetic
    input into the guest is not driven there because CheerpX's delayed
    release/Meta-key quirks make it non-deterministic (input behaviour is
    covered by the in-guest suites instead). Updated:
    `diskimage/Dockerfile`, `diskimage/config/i3/config`,
    `diskimage/config/xinitrc`, `diskimage/rootfs/etc/local.d/desktop.start`,
    `tests/rootfs/smoke.sh`, `tests/unit/test_scripts.py`,
    `.github/workflows/ci.yml`, `tests/e2e/tests/desktop.spec.js`.

No open questions remain. Anything still marked "at implementation time"
(pinned versions, guest NIC config, `extra_hosts` precedence, DataDevice path
semantics) is a lookup-and-record step, not a design decision — and the §12/21
checklist lists exactly which version-dependent claims to re-verify when the
versions are pinned.
