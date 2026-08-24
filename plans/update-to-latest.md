# Update-to-Latest Plan — bringing all components to their latest public releases

Investigation date: 2026-08-16. Sources: npm registry `/latest`, PyPI JSON,
Docker Hub tag APIs, GitHub releases APIs, the CheerpX CDN
(`cxrtnc.leaningtech.com/1.3.8/`), Alpine `APKINDEX.tar.gz` for x86, Node/Go
release feeds. Versions below were verified live on 2026-08-16; re-verify
anything marked [verify at implementation] when the pin changes.

> **Implementation status (2026-08-21):** **Tier A COMPLETE AND Tier B
> COMPLETE.** All A1–A7 shipped and re-validated; Tier B (guest on Alpine
> 3.24, Python 3.14, Tcl/Tk 8.6.17, Pillow 12, mistune 3, openbox) is DONE:
> the openrc 0.63/CheerpX boot blockers were root-caused to five
> syscall-emulation defects and fixed in one LD_PRELOAD shim +
> image changes (see §9.5.1), and the final gate ran green on 2026-08-21 —
> unit ×92, rootfs smoke ×4 backends, server integration (incl.
> join-test-client), Playwright browser phase **9/9** + webdav phase
> **12/12** (the handoff's "webdav data path" failure was an artifact of
> running webdav-phase specs against a browser-mode guest — §12/21(34) in
> `plans/webvm_implementation.md` records the resolution), shellcheck +
> yamllint. Debug artifacts removed. Tier C remains outstanding
> (deferred by design; frontend framework majors).

> **SESSION HANDOFF — 2026-08-19 20:06 (Tier B in progress, E2E blocked)**
> Work state is saved here so a fresh session can resume without re-deriving
> the diagnosis. See §5 "Tier B implementation status + openrc/CheerpX
> diagnosis" at the end of this file for the full record, and §5.4 for the
> exact resume checklist.

> **SESSION HANDOFF — 2026-08-20 20:25 (Tier B boot BLOCKER SOLVED; browser
> E2E 9/9 GREEN; webdav-phase data path REMAINS the only failure)**
> The openrc/CheerpX boot blocker from the 2026-08-19 handoff is FIXED —
> root-caused to FIVE distinct CheerpX syscall-emulation defects, all
> worked around in one LD_PRELOAD shim (diskimage/faccessat-fix.c) + three
> image changes. The browser-phase Playwright suite passes 9/9; the webdav
> phase (network.spec + sync.spec) still fails because the guest data path
> does not reach the gateway despite the page-side tailnet client reaching
> Running. §9.5 is the full resumption record: what was fixed and how,
> what is still open, the exact rebuild/test loop, and every debug artifact
> that must be reverted before the Tier B gate.
>
> **RESOLUTION — 2026-08-21 (Tier B COMPLETE):** §9.5.3's "webdav data
> path" failure is RESOLVED and the whole Tier B gate is green. The
> failure was an artifact of the debug loop: the webdav-phase specs were
> being run against a BROWSER-mode guest build, whose `/etc/webvm-backend`
> is `browser` — `desktop.start` then never starts the sync agent, so
> `webvm.lock` can never land on the backend, no matter how healthy the
> tailnet is. Rebuilding the guest with `./build.sh webdav` (matching the
> .env deployment) makes network.spec + sync.spec pass end to end. Final
> results: browser phase 9/9, webdav phase 12/12, unit 92/92, rootfs smoke
> ×4, integration.sh PASS, shellcheck/yamllint clean, all §9.5.5 debug
> artifacts removed. Full record in `plans/webvm_implementation.md`
> §12/21(34).

Non-negotiable repo rules still apply: HTTPS only; HOSTNAMES ARE BANNED
(`tests/unit/test_scripts.py::test_control_host_defaults_consistent` must stay
green); no secrets committed; no `#authKey` without a matching `controlUrl`;
CheerpX runtime stays self-hosted (zero external requests); every
version-dependent claim re-verified against the pinned build (§12/21 style).

## 1. Component inventory — current pin vs latest

### Tier A — runtime/infra components (safe bumps)

| Component | Current pin | Where pinned | Latest (2026-08-16) | Notes |
|---|---|---|---|---|
| `@leaningtech/cheerpx` (npm) | `1.3.7` (exact) | `webvm/package.json`, lock | **`1.3.8`** | minor; upstream webvm already bumped |
| CheerpX self-hosted runtime | `1.3.7` | `webvm/cheerpx/` via `scripts/fetch-cheerpx-runtime.sh` | **`1.3.8`** | CDN verified serving 1.3.8 |
| `leaningtech/webvm` frontend | `e58fef0c9a1c815617e57c6704eaaf7c79c3de1c` (2026-07-30) | vendored `webvm/`, `webvm/WEBVM_COMMIT`, README | **`8d68d2b18fa04d72ba49bc6c5b8c684a934fc268`** (2026-08-13) | 3 commits ahead: CheerpX 1.3.8 bump + 2 promo-text edits to `messages.js` |
| Headscale | `0.28.0` | `server/Dockerfile`; `server/headscale/config.yaml.template`; entrypoint/CI CLI | **`v0.29.3`** (2026-07-29) | 0.29.2 fixed `/ts2021` WebSocket-GET rejection relevant to wasm clients; 0.29.3 fixed ephemeral-node lingering; min client v1.80.0 (ours is v1.102.2 / capver 142) |
| `tailscale/tailscale` gateway image | `v1.102.2` | `gateway/Dockerfile` | **v1.102.2 = latest stable** | no gap |
| Browser Tailscale wasm rebuild | tailscale source `v1.102.2`; Go image `golang:1.26.5` | `scripts/rebuild-tailscale-wasm.sh`; committed `webvm/cheerpx/tun/*` | tailscale `v1.102.2` (unchanged); Go **`1.26.6`** | Go patch only; wasm already current |
| `pysmb` (samba-mode guest agent) | `1.2.10` | `diskimage/Dockerfile` pip | **`1.2.15`** | pure-python, low risk |
| `wsgidav` / `cheroot` | `4.3.5` / `11.1.2` | `server/Dockerfile` pip | **`4.3.5` / `11.1.2`** | no gap |
| Server base image | `python:3.11-alpine` | `server/Dockerfile`, `compose.yaml` (`test-unit`) | **`python:3.14-alpine`** | 3.11 maintained to 2027-10 → optional |
| CI Node | `20` | both workflows' `actions/setup-node` | **`24`** LTS (24.19.0) | 20 (Iron) past LTS |
| Playwright | `1.62.1` | `tests/e2e/package*.json` | **`1.62.1`** | no gap |
| e2fsprogs helper | `ubuntu:24.04` | `build.sh` | `ubuntu:26.04` | optional; 24.04 is fine |
| GitHub Actions | checkout@v4, setup-node@v4, buildx/qemu@v3, upload-artifact@v4, pages@v3/v4 | both workflows | newer majors [verify at implementation] | mechanical |

### Tier B — guest base OS (large; removes an EOL OS)

| Component | Current pin | Where | Latest (2026-08-16) |
|---|---|---|---|
| Guest base | `i386/alpine:3.17` (EOL 2024-11) | `diskimage/Dockerfile` | **`i386/alpine:3.24.1`** (supported to 2028-06; x86 verified: 5,951 + 21,057 pkgs) |
| Guest Python/IDLE | 3.10.15 / `idle3.10` | apk | **3.14.7** (`python3` 3.14.7-r1, `python3-tkinter` 3.14.7-r0, `python3-idle` 3.14.7-r0 → `/usr/bin/idle3.14`) |
| Guest Tcl/Tk | 8.6.12 + patched `libtcl8.6.so` (`third_party/tcl-8.6.12`) | Dockerfile, `third_party/` | **8.6.17-r1** in v3.24 (upstream final 8.6.18) |
| Guest Pillow/mistune (viewer) | `py3-pillow 9.3.0-r0`, `py3-mistune 2.0.4-r0` | apk | **`py3-pillow 12.2.0-r0`**, **`py3-mistune 3.2.1-r0`** |
| Guest i3/Xorg/xvfb | i3 4.x, Xorg 21.1.x | apk | **i3wm 4.24-r0**, **xorg-server 21.1.24-r0** |
| Guest git/openssh | legacy v3.17 builds | apk | **git 2.54.0-r0**, **openssh-client-default 10.3_p1-r0** |

### Tier C — frontend framework majors (deferred; deviates from upstream webvm)

| Component | Current (lock) | Latest | Type |
|---|---|---|---|
| `svelte` | 4.2.20 | **5.56.9** | major |
| `vite` | 5.4.21 | **8.2.1** | major (rolldown; Node ^20.19\|\|>=22.12) |
| `@sveltejs/vite-plugin-svelte` | 3.1.2 | **7.3.0** | major (peers vite ^8, svelte ^5.46) |
| `@sveltejs/kit` | 2.48.5 | **2.70.2** | minor (stays v2) |
| `tailwindcss` | 3.4.18 | **4.3.3** | major (CSS-first; config rewrite) |
| `@xterm/xterm` (+addons) | 5.5.0 | **6.0.0** | major (hidden console only) |
| `vite-plugin-static-copy` | 1.0.6 | **4.1.1** | major (node ^22\|\|>=24; vite ^6/7/8) |
| `html2canvas-pro` | 1.5.13 | **2.3.8** | major |
| `node-html-parser` | 6.1.13 | **9.0.1** | major |
| `@fortawesome/fontawesome-free` | 6.7.2 | **7.3.1** | major |
| `@oddbird/popover-polyfill` | 0.4.4 | **0.7.1** | minor |
| `@sveltejs/adapter-static` | 3.0.10 | 3.0.10 | no gap |
| `@sveltejs/adapter-auto` | 3.3.1 (unused) | 7.0.1 | drop or bump |

## 2. Customization surface — what the update must not break

This repo is NOT "upstream webvm + thin config": every directory except the
vendored `webvm/` frontend is custom-built, and the vendored frontend itself is
heavily modified (§12/21 items 24–32). "Updating to latest" must therefore
NEVER re-clone, re-vendor, or wholesale-replace any of the following; the only
allowed changes are in-place version constants, image tags, config schemas, and
the documented package-drift handling below. To make this guarantee
executable, the customization surface is enumerated here (from the §12/21
"Updated:" records and the repo tree) with the tier that touches it and the
regression gate that proves it still works.

| Customized artifact | Touched by | Must change / must survive | Regression gate |
|---|---|---|---|
| `webvm/src/lib/WebVM.svelte` (error overlay, trap capture, boot watchdog, KMS canvas, session lock, `webvm-test-bootfail`/`webvm-test-trapreport` hooks) | A1, C | survive byte-identical unless a version bump forces an interface tweak; never re-derived from upstream | error-overlay.spec, boot/desktop.spec |
| `webvm/src/lib/network.js` (newIPN driver for the rebuilt wasm, `connectedTcpSocket`, tun accept loop) | A1 | survive; only re-validated against new tun glue | network.spec, sync.spec |
| `webvm/src/lib/cheerpx.js` (self-hosted runtime import, base-path derivation) | A1, C | survive | boot.spec, Pages workflow |
| `webvm/src/routes/alpine/+page.svelte`, `+page.svelte`, `webvm/src/app.html` (session guard, baked `/webvm-config.js` seeding, sticky explicit session) | A1, C | survive | boot.spec root-visit, persistence, integration.sh config assertions |
| `webvm/static/sw.js` (Pages COOP/COEP worker), `config_public_alpine(_github).js`, `vite.config.js` (aliases + static-copy of `cheerpx/`) | A1, C | survive | Pages workflow, no-egress E2E |
| `webvm/cheerpx/*` (self-hosted runtime, vendored cxcore trap patch, rebuilt `tailscale.wasm` + `wasm_exec.js`) | A1 | CHANGES BY DESIGN — only via `fetch-cheerpx-runtime.sh` / `rebuild-tailscale-wasm.sh`, never hand-edited; rebuilt wasm must pair with the NEW runtime's tun glue | error-overlay.spec, network/sync E2E |
| `server/*` (entrypoint fail-closed checks, nginx template with CSP + `/derp` WS catch-all, headscale/wsgidav templates, `render-webvm-config.py`, Dockerfile) | A1, A2, A5 | entrypoint/nginx/render-webvm-config.py survive byte-identical; headscale template + Dockerfile change in A2 | `make test-unit`, integration.sh, webdav E2E |
| `gateway/*` (tailscaled up, loopback socat relays incl. the CONTROL_PORT DERP relay, CA trust) | A1 | survive byte-identical (base image tag `v1.102.2` unchanged) | network/sync E2E, join-test-client.sh |
| `scripts/fetch-cheerpx-runtime.sh` (trap patch) + `scripts/rebuild-tailscale-wasm.sh` + `scripts/tailscale-wasm-entry/wasm_js.go` (custom glue entry) | A1, A4 | only their VERSION/GO_IMAGE constants change | fetch guards + error-overlay.spec; network E2E |
| `scripts/gen-certs.sh`, `print-url.sh`, `acceptance.sh` | — | survive | unit tests, acceptance |
| `diskimage/scripts/*` (file-explorer + 98-check tests, file-viewer + tests, `file_types.py`, `99-screen-resize.sh`) | B | change only where Py3.14/Tk8.6.17/Pillow12/mistune3 require it; behavior identical otherwise | rootfs smoke per backend |
| `diskimage/rootfs/*` (`desktop.start` wait-for-tailnet + boot-pull, `keep-file-explorer.sh` i3-tree watcher, `open-file-explorer.sh`, `idle3.10-launcher`, hosts/inittab/network/fontconfig/Xorg confs, hello.py, Readme.md) | B | change only the idle3.x rename + any 3.24-driven config | rootfs smoke (real-IDLE launch + keep-alive relaunch) |
| `diskimage/config/*` (xinitrc → `dbus-run-session i3`, i3 config, .Xresources) and `diskimage/sync/*` (sync.py, sync-home.sh — lease, debounced push) | B | change only where i3 4.24 / Py3.14 force it | rootfs smoke; webdav sync E2E |
| `diskimage/trace/libtcl8.6.so.patched` + `third_party/tcl-*` + the alpine patch build recipe | B | REBUILT by design against tcl 8.6.18, same CheerpX notifier fix; the `third_party/` recipe is the source of truth | in-guest Xvfb tkinter tests |
| `build.sh` (fingerprint cats the diskimage tree + patched lib), `Makefile`, `compose.yaml`, `.env.example` | A5, B | survive; fingerprint is self-adjusting | unit tests |
| `tests/**` (hostname-ban unit test, templates test, rootfs smoke, integration.sh, join-test-client.sh, all e2e specs + mjs probes) and `.github/workflows/{ci,pages}.yml` | all | change only for renamed paths (idle3.14), CI node/actions versions, and headscale-CLI parsing (A2); the hostname-ban + headscale assertions are the tripwires | CI itself |
| `webvm/WEBVM_COMMIT`, README pinned-version blocks, plan §12/8 + §12/21 | A/B/C | updated documentation (expected) | — |

**Protection rules (hard):**
- Never `git clone`/re-vendor any upstream project into the tree. The webvm
  frontend stays the vendored tree at the recorded commit; the ONLY upstream
  change taken is the CheerpX 1.3.8 bump (A1).
- Never let a lockfile regeneration move the CUSTOM pins (`cheerpx` exact,
  `wsgidav==4.3.5`, `cheroot==11.1.2`, `pysmb==1.2.15`) — always edit the
  manifest first, then regenerate.
- Never hand-edit `webvm/cheerpx/*` or the wasm: runtime files change only
  through `fetch-cheerpx-runtime.sh` (with its guards), the wasm only through
  `rebuild-tailscale-wasm.sh`.
- A bump is not done until its named regression gate runs green in CI.

### Rollback pins (every bump is a one-line revert + revalidate)

| Uplift | Rollback pin | Mechanism |
|---|---|---|
| A1 CheerpX | `1.3.7` | revert `package.json`/lock + re-run fetch script (committed runtime tree is in git) |
| A1 webvm pin | `e58fef0…` | documentation-only change |
| A2 headscale | `0.28.0` + old `config.yaml.template` | `git revert` of the two file commits (DB volume is forward-compatible) |
| A4 Go | `golang:1.26.5` | revert the script constant |
| A5 server base | `python:3.11-alpine` | revert Dockerfile + compose |
| B guest Alpine | `i386/alpine:3.17` + old repos block + old package args | single `git revert` of `diskimage/Dockerfile` (+ `third_party/`, `trace/`) |

## 3. Tier A — execution (ordered; CI must stay green after each step)

### A1. CheerpX 1.3.7 → 1.3.8 + webvm pin refresh
1. `webvm/package.json`: cheerpx `1.3.7` → `1.3.8`; regenerate+commit
   `package-lock.json`.
2. `scripts/fetch-cheerpx-runtime.sh`: `VERSION="1.3.8"`, re-run. **The §12/21(32)
   vendored trap patch (`patch_cxcore`) may not apply to the 1.3.8 cxcore
   files** — its presence guards (3× `console.error('Unexpected exit'`, no
   `debugger;`, no `e()`) will catch drift; adapt the patch targets to the 1.3.8
   core, then commit the refreshed `webvm/cheerpx/` tree.
3. Do NOT re-clone upstream. Refresh `webvm/WEBVM_COMMIT` to `8d68d2b18fa0…`
   with a note that only the CheerpX bump was taken (this repo's frontend is
   deeply diverged). The upstream diff `e58fef0…8d68d2b` is exactly the
   cheerpx lockfile bump + `messages.js` promo text (this repo removed the
   banner content — verify `introMessage` is unused or skip the hunk).
4. Re-run the pinned-runtime verifications that matter here: no-egress E2E,
   error-overlay spec (trap capture), boot/desktop spec; re-check §12/21 items
   (c),(d),(e),(g),(i),(32) against 1.3.8.
5. Update pinned-version docs: README, `WEBVM_COMMIT`, plan §12/8 + §12/21.
6. **Tun/glue pairing gate (A1's blocker):** the rebuilt `tailscale.wasm` +
   `wasm_exec.js` were validated against the 1.3.7 `tun/*` glue and `cx.esm.js`
   (networking-bug.md §16 — mixing the CDN's Leaning-fork pair with the rebuilt
   wasm breaks instantiation). After fetching the 1.3.8 runtime, prove the
   pairing end-to-end (`network.spec` + `sync.spec` green with
   `CONTROL_HOST=127.0.0.1`, guest data path works). If 1.3.8's tun glue proves
   incompatible, ROLL BACK to 1.3.7 rather than patching the glue.
7. **Reconcile the fetch list with the 1.3.8 CDN file set:** confirm every
   `FILES` entry still exists (fetch uses `--fail`; a minor bump can rename
   files) and every `EMPTY_FILES` entry (`fail.wasm`, `dump.wasm`, `t.wasm`,
   `tailscale_tun.js`) is still a 204/empty placeholder in 1.3.8; update the
   lists if the set changed.
8. Keep the runtime self-hosted: confirm the 1.3.8 npm wrapper still CDN-imports
   its core by default (the vite alias to `src/lib/cheerpx.js` is what keeps the
   page at zero external requests — no source change expected).

### A2. Headscale 0.28.0 → 0.29.3
1. `server/Dockerfile`: `FROM headscale/headscale:0.29.3`; rewrite the rationale
   comment (the 0.28.0 pin existed for the old capver-109 bundled client; the
   rebuilt v1.102.2 client — capver 142, min supported v1.80.0 — is accepted).
2. `server/headscale/config.yaml.template`: known schema drift —
   `ephemeral_node_inactivity_timeout` is top-level in 0.28.0 and nested
   `node.ephemeral.inactivity_timeout` in 0.29.x (0.28.0 ignores the nested
   spelling). Move the key and re-verify EVERY template key against 0.29.3's
   `config-example.yaml` (`server_url`, `derp.server.*`/STUN/`verify_clients`,
   `prefixes.node.allocation`, `trusted_proxies`, `dns.magic_dns`,
   `logtail.enabled`, `taildrop`, `auto_update`).
3. Re-verify the 0.29.x CLI surface used by `server/entrypoint.sh` and CI
   `make_key`: `preauthkeys create --user <numeric id>`, `preauthkeys list`
   masking, `users list` columns (entrypoint parses them).
4. Check the 0.28→0.29 SQLite upgrade path (named volume; schema is versioned —
   verify before swapping the image; document a recovery if not in-place).
5. Re-confirm §12/21(l): 0.29.3 still has no fixed-IP mechanism.
6. Validate with the full webdav tailnet phase: `network.spec.js`,
   `sync.spec.js`, boot root-visit with `CONTROL_HOST=127.0.0.1`,
   `join-test-client.sh` against `https://172.28.0.10:8443`,
   `tests/server/integration.sh`.

### A3. pysmb 1.2.10 → 1.2.15
- `diskimage/Dockerfile` `pysmb==1.2.15` (samba mode). Validate: `./build.sh
  samba`, rootfs smoke samba, CI samba matrix (note: no live Samba runtime in
  CI).

### A4. tailscale wasm build toolchain Go 1.26.5 → 1.26.6
- `scripts/rebuild-tailscale-wasm.sh`: `GO_IMAGE="golang:1.26.6"`. Tailscale
  source stays `v1.102.2` (it is latest). Rebuilding is optional (toolchain
  micro-bump); if rebuilt, commit the wasm and re-run the tailnet E2E.

### A5. Server base python:3.11-alpine → python:3.14-alpine (optional)
- `server/Dockerfile` + `compose.yaml` `test-unit`. Verify wsgidav/cheroot pip
  install, entrypoint, unit suite. 3.11 is maintained to 2027-10, so this can
  be deferred or skipped with a comment update.

### A6. CI Node 20 → 24 + actions majors
- `node-version: 24` in both workflows; bump GitHub/Docker actions to latest
  majors [verify at implementation]; keep pin-by-major style.

### A7. (Optional) e2fsprogs helper ubuntu:24.04 → 26.04 in `build.sh`.

## 4. Tier B — guest rebuild on Alpine 3.24 (large; do after Tier A)

### B1. Base + repos
- `diskimage/Dockerfile` `FROM docker.io/i386/alpine:3.24`; replace the EOL
  repository rewrite block with `v3.24` main+community
  (`https://dl-cdn.alpinelinux.org/alpine/v3.24/{main,community}`, keeping the
  rewrite-not-append pattern). No EOL caveat remains.

### B2. Package deltas (verified present on v3.24 x86)
- Keep the same package list; versions move to: python3 3.14.7-r1,
  python3-tkinter 3.14.7-r0, python3-idle 3.14.7-r0, py3-pip 26.1.2-r0,
  i3wm 4.24-r0, xorg-server/xvfb 21.1.24-r0, xterm 410-r0, git 2.54.0-r0,
  openssh-client-default 10.3_p1-r0, busybox-extras 1.37.0-r31, dbus 1.16.2-r2,
  eudev 3.2.14-r6, font-dejavu 2.37-r6, py3-pillow 12.2.0-r0,
  py3-mistune 3.2.1-r0.

### B3. Version-specific rework
- **IDLE**: re-verify the `apk fetch python3-idle` + tar extraction trick on the
  3.14.7 package (does it still hard-depend on python3-tests? does it ship
  `/usr/bin/idle3.14` + `idlelib`?). Rename `idle3.10` → `idle3.14` repo-wide:
  grep for `idle3.10`/`idle3.10-launcher` in `diskimage/`, `tests/`,
  workflows (CI debugfs `stat /usr/bin/idle3.10`), README.
- **Tcl/Tk patch**: bump `third_party/` to tcl 8.6.18 sources and rebuild
  `libtcl8.6.so.patched` via the existing `third_party/` alpine patch recipe
  (display-bug.md §2.8 CheerpX notifier fix, unchanged), re-verify the
  Dockerfile override lands and the in-guest Xvfb tkinter tests pass
  (Alpine 3.24 ships tcl/tk 8.6.17).
- **File explorer + viewer app suites**: run the FULL in-guest suites under
  Python 3.14 + Tk 8.6.17 — `file-explorer-tests.py` (98 checks: sorts, wheel
  scroll, breadcrumbs, rename/create/delete, zip, late-release-tap/1500 ms
  long-press touch model), `file-viewer-tests.py`, the real-IDLE launch, the
  withdraw→IDLE→reappear flow, and the i3 keep-alive relaunch. Re-verify the
  version-sensitive behaviors the §12/21 notes pinned: ImageTk/`_imagingtk`,
  Pillow `exif_transpose` animation handling + `draft()`/`thumbnail()`,
  the `wm`-class gap (window-title detection `<name> — Viewer` / `Toplevel` /
  `Python … Shell`), mistune 2.0.4→3.2.1 `AstRenderer` walking, and the
  `after()`-needs-running-`mainloop()` rule. Adjust code only where the new
  versions changed the behavior.
- **Desktop boot**: re-validate the §12/22-25 stack — Xorg root launch +
  `-novtswitch`, `inittab` (no gettys), `~/.xinitrc` → `dbus-run-session i3`,
  i3 4.24 config (explorer autostart via `open-file-explorer.sh`, keep-alive
  i3-tree watcher that relaunches only when no explorer process exists),
  `99-screen-resize.sh`, fontconfig aliases (`99-webvm-aliases.conf`) AND the
  `desktop.start` font warm-up (`cat /usr/share/fonts/{dejavu,misc}/*` —
  used to avoid cold chunked font reads; re-verify it still works on 3.24),
  and `/.dockerenv` removal. For samba/webdav builds also re-verify
  `desktop.start`'s wait-for-tailnet retry loop + boot-pull lease path on the
  new Python.
- **Sync agent**: audit `sync.py` for Python 3.10→3.14 removals; re-verify
  pysmb 1.2.15 and the urllib WebDAV path.
- **Fingerprint/cache**: no `build.sh` change needed (fingerprint derives from
  the Dockerfile + tree), but the ext2 size and cacheId suffix change — the
  browser overlay auto-rotates to a fresh `blocks_alpine_<new>` (the intended
  upgrade path). Confirm E2E persistence expects a fresh overlay, not stale
  deltas.
- **Fallback**: if anything is missing/broken on v3.24 x86, v3.23 is a
  supported fallback (x86 verified populated; Python 3.13.x). Do not dip below
  v3.22 without re-checking EOL dates.

### B4. Validation
- `./build.sh` all four backends + CI debugfs checks (corrected idle3.14 path).
- `tests/rootfs/smoke.sh` per backend (explorer/viewer under Xvfb, real IDLE
  launch, i3 boot, keep-alive relaunch).
- Full E2E: boot, desktop, error-overlay, persistence, no-egress; webdav:
  network, sync.

## 5. Tier C — frontend framework majors (deferred, separate effort)

Upstream `leaningtech/webvm` still ships Svelte 4 / Vite 5 / Tailwind 3, so
Tier C diverges this repo further from the reference and is the riskiest,
lowest-urgency tier. Do NOT mix with Tier A/B. If/when executed:
1. One commit per major bump, regenerating `package-lock.json` each time and
   running the full E2E suite + the GitHub Pages workflow (service-worker
   headers must still provide cross-origin isolation).
2. Suggested order: `@sveltejs/kit` 2.70.2 (same major; then Node 24 fully
   justifies it) → `vite` 6/7/8 with `@sveltejs/vite-plugin-svelte` → Svelte 5
   migration of the vendored components (runes/legacy) → Tailwind 4 CSS-first
   rewrite (tailwind.config.js/postcss.config.js replaced) → remaining majors
   (`@xterm/xterm` 6, `vite-plugin-static-copy` 4.1.1, html2canvas-pro,
   node-html-parser, fontawesome 7) → drop unused `adapter-auto`.
3. Each major must keep: the `alpine.html` output path, the self-hosted
   cheerpx import through `src/lib/cheerpx.js` (vite alias), the CSP headers,
   zero external requests, the Pages chunked-device build, AND the customized
   behaviors listed in §2 (trap capture/boot-watchdog hooks, the network.js
   newIPN driver, the session guard) intact through the refactor.

## 6. Cross-cutting rules (all tiers)

- Never break `test_control_host_defaults_consistent` (no hostnames anywhere);
  never commit secrets; keep `connect-src`/CSP strict; keep the runtime
  self-hosted; keep `diskImageType="bytes"` and the same-origin byte-range
  serving.
- Adhere to §2's protection rules: no re-vendoring, no lockfile-driven moves of
  the custom pins, no hand-edits of `webvm/cheerpx/*` or the wasm; a bump is
  "done" only when its §2 regression gate is green.
- Keep bump commits small and revertible — never squash an uplift until its
  tier is CI-green (rollback pins in §2 must remain one-line reverts).
- Update README "Pinned versions", `webvm/WEBVM_COMMIT`, and the plan's §12/8 +
  §12/21 checklist as each pin changes; §12/21 items whose behavior can shift
  with a version bump must be re-run, not assumed (the checklist exists for
  exactly this).
- The cacheId `blocks_alpine_<fingerprint>` will churn on every Tier A/B change
  that touches `diskimage/` (fingerprint covers the Dockerfile + tree); this is
  correct and expected — old overlays are orphaned, not corrupted.

## 7. Validation harness (the gate for every tier)

- `make test-unit` (pytest; includes the CONTROL_HOST/template tests).
- `./build.sh <backend>` + debugfs checks; `tests/rootfs/smoke.sh <backend>`.
- `make up`/`up-tailnet` + `tests/server/integration.sh`.
- Playwright E2E (`tests/e2e`): boot.spec, desktop.spec, error-overlay.spec,
  network.spec, sync.spec, persistence/no-egress.
- GitHub Pages workflow deployment still boots the VM (Tier C).
- `scripts/acceptance.sh` LAN checklist.
- CI itself (guest matrix ×4, frontend ×4, unit, server+E2E, lint) is the final
  gate; the lint job must stay green (shellcheck/yamllint lists already cover
  every touched script).

## 8. Risks & open items

- **CheerpX 1.3.8 trap behavior**: the §12/21(32) swallow/trap may persist;
  the vendored patch's guards are the tripwire — plan for a patch-target edit.
- **CheerpX 1.3.8 tun-glue pairing** is A1's hard gate: if the rebuilt
  tailscale.wasm does not pair with the 1.3.8 `tun/*`/`cx.esm.js` glue, roll
  back to 1.3.7 (do not patch the glue) — this was exactly the §16 failure
  class.
- **Headscale 0.29.x drift**: config schema (built-in key change) + CLI output
  parsing in `entrypoint.sh`/CI are the two spots most likely to surprise.
- **Tier B size**: Python 3.10→3.14, Tcl/Tk, Pillow 9→12 and mistune 2→3 each
  carry version-sensitive code; budget re-validation, not just rebuild.
- **Tier C vs upstream**: accepting the divergence means upstream syncs become
  manual; record this trade-off in the plan/README when starting Tier C.
- Unverified-at-write-time (verify when changing): GitHub Actions latest
  majors; headscale 0.29.3 `config-example.yaml` exact keys; `python3-idle`
  3.14.7 package layout; tcl 8.6.18 patch application on Alpine 3.24's 8.6.17.
## 9. Tier B implementation status + openrc/CheerpX diagnosis (SESSION HANDOFF 2026-08-19 20:06)

Work was paused mid-Tier-B with the browser E2E still blocked on a guest boot
hang. Everything below is the current truth of the working tree (git status:
all Tier A files + the Tier B files listed in §9.1; debug artifacts listed in
§9.3 must be reverted before finishing).

### 9.1 Completed Tier B work — VERIFIED GREEN (docker-side)

- **B1**: `diskimage/Dockerfile` base `i386/alpine:3.17` → `i386/alpine:3.24`
  (3.24.1), repos block rewritten to v3.24 main+community.
- **B2**: package versions auto-moved (verified 2026-08-18 on v3.24 x86):
  python3 3.14.7-r1, python3-tkinter 3.14.7-r0, python3-idle 3.14.7-r0,
  py3-pillow 12.2.0-r0, py3-mistune 3.2.1-r0, py3-pip 26.1.2-r0,
  tcl/tk 8.6.17-r1, xorg-server/xvfb 21.1.24-r0, openbox 3.6.1-r8,
  git 2.54.0-r0, openssh-client-default 10.3_p1-r0, busybox-extras
  1.37.0-r31, dbus 1.16.2-r2, eudev 3.2.14-r6, font-dejavu 2.37-r6,
  xterm 410-r0. `python3-idle` still hard-depends on `python3-tests` → the
  apk-fetch+tar trick stays (package ships only `/usr/bin/idle3.14` + `idlelib`).
- **B3a**: `idle3.10` → `idle3.14` repo-wide (launcher renamed, file-explorer.py,
  keep-file-explorer.sh, smoke.sh, CI debugfs path, idle-pointer.spec, README).
- **B3b**: third_party fork moved to **tcl-8.6.17 + tk-8.6.17** (NOT 8.6.18 —
  see §9.2 item 1), `libtcl8.6.so.patched` rebuilt from 8.6.17 sources with
  ONLY the `tcl-notifier-stale-fdset.patch` applied (the other three aports
  patches are upstreamed in 8.6.17/8.6.18 — verified in source), committed to
  `diskimage/trace/libtcl8.6.so.patched`; APKBUILDs updated to 3.24-stable +
  8.6.17 sha512sums; `third_party/README.md` rewritten.
- **B3c**: `diskimage/scripts/file-viewer.py` mistune 2→3 adapter — mistune
  3.2.1 has NO `AstRenderer`; the walker now reads both token shapes
  (`raw` vs `text`, `attrs` dicts, `blank_line`/`linebreak`, image alt in
  first child). Viewer suite PASS ALL under Xvfb in the guest.
- **B3d**: pysmb install now `pip3 install --break-system-packages` (PEP 668 on
  Alpine 3.24).
- **B3e/f**: `rootfs smoke PASS` for ALL FOUR backends (browser, samba, webdav,
  none) on the 3.24 guest, incl. real-IDLE launch + keep-alive relaunch; unit
  suite 92/92 on python:3.14-alpine; webdav server integration PASS.
- Guest-side fixes baked into `diskimage/` for the 3.24 boot (see §9.2):
  `/run` state dirs + patched `init.sh`.

### 9.2 The openrc 0.63.2/CheerpX boot diagnosis (chronological findings)

The Alpine 3.24 guest boots fine under docker but the CheerpX E2E boot
(browser + webdav phases) fails. Three distinct blockers were found and fixed;
one remains:

1. **Tcl fork must be 8.6.17, not 8.6.18**: the first attempt built the
   patched lib from 8.6.18 sources; the guest aborts tkinter with
   `package require -exact Tcl 8.6.17` version conflict (the apk init.tcl
   demands an exact match). The override library must be built from the
   exact apk version → 8.6.17. (Plan's "8.6.18" note is superseded.)
2. **openrc 0.63 crashes when `/run/openrc` is missing**: 0.60s moved the
   svcdir from /var/run/openrc to /run/openrc; `rc_dirfd()` caches dirfds and
   the missing dir left `dirfds[] = -1`, then `faccessat(-1)` **hangs the
   CheerpX emulator forever**. FIX: `diskimage/Dockerfile` bakes
   `/run/openrc/{starting,started,stopping,inactive,wasinactive,failed,
   hotplugged,daemons,options,exclusive,scheduled,init.d,tmp}` + `/run/lock`
   + `/run/secrets` (ALL state dirs — see item 4).
3. **openrc 0.63's `init.sh` aborts sysinit** when it cannot mount a tmpfs on
   /run (`mount(2)` = ENOSYS under CheerpX; "Can't continue." + exit 1).
   FIX: `diskimage/rootfs/usr/libexec/rc/sh/init.sh` (NEW file) patches the
   abort to a warning + `eend 0`; the Dockerfile COPYs it over the apk file
   and chmods 0755 (the file header documents the diff).
 4. **SUPERSEDED — was "openrc boot spins in librc `rm_dir()`"** (see §9.5:
    the 2026-08-20 session SOLVED this — the true cause was five CheerpX
    syscall-emulation defects, root-caused with the rebuilt trace shim:
    faccessat(-1) wild-call, sigprocmask(SIG_UNBLOCK) wild-call,
    ppoll always-fails, setsockopt(SO_PASSCRED) busy-loop, and openrc's
    env_filter() scrubbing LD_PRELOAD. All fixed in
    `diskimage/faccessat-fix.c` + rc_env_allow + rc-preload/inittab. The
    history below is kept for reference; do NOT re-derive it.)
   (librc.c:139) right after "Caching service dependencies ... [ ok ]":
   - Symptom: with udev removed from sysinit, `openrc sysinit` COMPLETES
     (S=0); `openrc boot` hangs even with an EMPTY boot runlevel (bootmisc +
     networking removed too). The trap reports
     `Fault addr affdf000, ip afffee10, proc /sbin/openrc`
     (both spontaneous crashes and after `kill -SEGV` of the spinning pid —
     the reported ip is the spin location). addr2line of the candidate
     offsets resolves the spin to **`rm_dir` (librc.c:139, recursive dir
     remover)**; the wild read at base-0x1000 and the recursion pattern
     point at a `readdir`/getdents loop (CheerpX returning a stale/looping
     dirent on one of the /run/openrc state dirs) or unbounded recursion.
     The last traced syscall before the spin was
     `openat(svcdirfd, "started", O_RDONLY|O_DIRECTORY|O_CLOEXEC)`.
   - Diagnostic tooling that WORKED in the guest shell (boot cmd=/bin/sh):
     LD_PRELOAD syscall-trace shims (open/openat/faccessat/mount/poll/
     read/fstat/mkdirat/close wrappers + dl_iterate_phdr module dump +
     `_Unwind_Backtrace` on matching paths — `diskimage/segv-shim.c`, built
     in a scratch i386 container via `/tmp/shim.Dockerfile`, COPY'd into the
     debug image); `kill -SEGV` on the spinning process to extract its eip
     via the trap report; dl_iterate_phdr to get module bases;
     openrc-dbg/musl-dbg packages + addr2line (bases: openrc 0x55555000,
     musl 0x55560000, librc 0xaffee000, libeinfo 0xaffe8000).
   - Tooling that does NOT work under CheerpX: gdb (ptrace unimplemented),
     strace (same), busybox `timeout` kill (setitimer/SIGALRM never fires —
     a timed-out child is never killed; the wrapper only returns when the
     child exits on its own), `setitimer` ticks in the shim (never fire),
     guest SIGSEGV handlers (CheerpX converts the fault to the wasm trap and
     never delivers the signal), `/proc/self/maps` (only `[stack]`),
     `/proc/<pid>/wchan`, `/proc/<pid>/stat` details.
   - Also observed (benign, not boot-relevant): python3 fails at startup with
     "Fatal Python error: error evaluating path" when the process's initial
     cwd is the CheerpX bootstrap cwd (unresolvable — `pwd` prints nothing);
     `cd /` fixes it. Every real desktop process starts from a valid cwd, so
     the boot is unaffected — but the page's first process (the boot cmd)
     sees the broken cwd. NOTE: the LAST shim experiment (readdir/fstatat/
     unlinkat wrappers) produced an EMPTY `/tmp/shim.log` — the shim did not
     load or its constructor failed (needs a check before trusting it; the
     earlier open/openat/faccessat wrapper version DID produce logs).
   - Untested leads for the remaining blocker (in order of cost):
     a. Wrap `readdir`/`fdopendir`/`closedir` (musl exports real functions)
        again but verify the shim actually loads (check `/tmp/shim.log`
        exists + `[SHIM] started` line, run `LD_PRELOAD=... ls /run/openrc`
        as a self-test first). If readdir loops on a specific dir, pin which
        (started? scheduled? options?) and either pre-create a marker entry
        or remove the dir from the image.
     b. If it is a recursion issue in rm_dir: check whether `rm_dir` spins on
        `/run/openrc/options/<svc>` or `daemons/<svc>` (librc.c:965-966,
        called from `rc_service_mark(STOPPED)`) — those dirs are created at
        runtime; consider baking them too.
     c. Pragmatic fallback (plan-sanctioned spirit): pin openrc from an older
        Alpine branch (e.g. v3.22 openrc 0.59.x) into the 3.24 image via an
        extra repo + `apk add openrc=0.59...` (component pin, base stays
        3.24). v3.23 ALSO ships openrc 0.63.2, so dropping the base to 3.23
        does NOT help (verified).
     d. If openrc 0.63 is unfixable under CheerpX, fall back to pinning
        openrc 0.44.x-era behavior is NOT viable via the base OS; the
        component pin (c) is the right lever.

### 9.3 CURRENT WORKING-TREE STATE (debug artifacts — revert before finishing)

- `webvm/config_public_alpine.js` — currently `cmd = "/sbin/init"`
  (CORRECT; the debug overrides `/bin/sh` / `boot-diag.sh` were reverted
  as part of the 2026-08-20 fixes — if it is ever seen otherwise, restore
  it before any E2E run).
- `diskimage/Dockerfile` — contains a `# DEBUG-ONLY shim (temporary)`
  `COPY segv-shim.so /usr/local/lib/segv-shim.so` block (remove) and
  `strace` was removed already (verify with `grep -n "segv\|strace"`).
- `diskimage/segv-shim.c` + `diskimage/segv-shim.so` — untracked debug
  artifacts (delete; source of truth for the shim is in git history if
  needed).
- `tests/e2e/tests/diag.spec.js` (untracked) — debug Playwright spec
  (delete before finishing).
- Server stack currently serves the DEBUG build (cmd=/bin/sh guest). The
  `.env` is the webdav deployment with the old headscale keys (volume
  migrated to 0.29.3 in Tier A — still healthy).
- The rebuild loop used (each edit → this):
  `./build.sh browser && cd webvm && WEBVM_MODE=browser
  WEBVM_IMAGE_BUILD=$(cat ../webvm/custom-disk-images/image-build.txt)
  npm run build && cd .. && docker compose --profile tailnet build server &&
  docker compose up -d server --wait --wait-timeout 120`

### 9.4 RESUME CHECKLIST

1. Rebuild the readdir-trace shim self-test first (§9.2 item 5a) OR jump to
   the openrc component pin (c) — time-box the readdir hunt to ~30 min.
2. Once `openrc boot`/`default` complete under CheerpX (the terminal should
   show `* Starting udev ...`, `* Starting dbus ...`, `* Starting local ...`),
   revert the debug state: `git checkout webvm/config_public_alpine.js`,
   remove the Dockerfile shim block + `diskimage/segv-shim.{c,so}` +
   `tests/e2e/tests/diag.spec.js`, rebuild guest+frontend+server.
3. Re-run the FULL gate: `make test-unit`, rootfs smoke ×4,
   `tests/server/integration.sh` (webdav), Playwright E2E browser phase
   (boot/desktop/error-overlay/idle-pointer/no-egress — expect 9) then webdav
   phase (network.spec + sync.spec — expect 11).
4. Update docs: README "Pinned versions" (add Alpine 3.24 + the guest
   package set), `plans/webvm_implementation.md` §12/21 add the Tier B
   entry (mirror §12/21(33) style), this file's status banner.
5. Tier B remaining scope from the original plan §3/B3 that is NOT yet done:
   verify the desktop boot re-validation items once the boot works (Xorg
   root launch, inittab, xinitrc, openbox autostart, desktop.start
   wait-for-tailnet, the §12/22-25 stack) — the smoke tests cover the
   docker side; the E2E covers the CheerpX side. Tier C (frontend majors)
   is untouched.

### 9.5 RESUMPTION HANDOFF — 2026-08-20 20:25 (boot fixed; webdav data path open)

**Status banner: the openrc boot blocker is SOLVED and the browser-phase E2E
is fully green (9/9). The only remaining Tier B failure is the webdav-phase
guest data path (network.spec + sync.spec).** Everything below is current
truth (working tree at this timestamp; uncommitted changes include all of
Tier A + Tier B work + the debug artifacts listed in §9.3/§9.5.5).

#### 9.5.1 ROOT CAUSE — five CheerpX syscall-emulation defects (all FIXED)

The 2026-08-19 handoff's "rm_dir readdir spin" was a red herring: the trace
shim (rebuilt as `segv-shim.c` v4 — constructor-free, lazy raw-syscall
`/tmp/shim.log`, mirrored to `/dev/console` so the page xterm shows it even
when the guest wedges) revealed the boot actually CRASHED at
`faccessat(-1, "devfs", F_OK, 0)` — a wild call in the child. The full causal
chain, each fixed:

1. **`faccessat(-1)` traps CheerpX** (wasm "function signature mismatch",
   `Fault addr==ip==0xffff9fa7`). openrc 0.60+ calls `faccessat(-1, ...)` BY
   DESIGN: the service state table (`src/shared/misc.h`) maps
   RC_SERVICE_STOPPED and RC_SERVICE_CRASHED to RC_DIR_INVALID, and
   `rc_dirfd(RC_DIR_INVALID)` returns -1; `rc_service_state()` then calls
   `faccessat(-1, <svc>, F_OK, 0)` for EVERY service on every runlevel
   change. The old 3.17 guest's openrc 0.55.1 used path-based `exists()`
   (no faccessat) — which is why this only appeared with the 3.24 upgrade.
   FIX: shim short-circuits the whole `*at()` family to errno=EBADF for
   `dfd < 0 && dfd != AT_FDCWD` (exactly the kernel's answer).
2. **`sigprocmask(SIG_UNBLOCK)` traps CheerpX** (same wild-call signature,
   in openrc's `exec_service()` child right before exec: it unblocks the
   full mask with `SIG_UNBLOCK` after the fork). FIX: implement SIG_UNBLOCK
   via the working SIG_SETMASK branch — read the current mask with
   `sigprocmask(SIG_SETMASK, NULL, &cur)`, clear the requested bits, write
   back. (A naive "SIG_UNBLOCK → no-op" left all signals blocked in the
   child and the exec'd init scripts misbehaved; the faithful conversion is
   what works.)
3. **`ppoll` returns -1/errno=0 (never waits, never reports ready)** while
   `poll` works. GLib's `g_poll` uses ppoll, so openbox/dbus main loops
   spun with an endless "poll(2) failed due to: Function not implemented"
   flood and windows never mapped. FIX: shim converts `ppoll` → `poll`
   (timespec → ms, infinite when NULL, sigmask ignored).
4. **`setsockopt(SOL_SOCKET, SO_PASSCRED)` returns EPROTONOSUPPORT** and
   CheerpX logs an endless "TODO: SYS_SETSOCKOPT" retry loop — udevd's
   netlink setup busy-spun and wedged the whole emulator (the shell froze
   seconds after starting udevd). FIX: shim fakes success (returns 0) for
   exactly `SOL_SOCKET/SO_PASSCRED`; nothing in the guest depends on real
   credential passing.
5. **openrc's `env_filter()` scrubs LD_PRELOAD** from exec'd init scripts'
   environments — so even with the shim set for `/sbin/openrc`, the
   exec'd `/etc/init.d/*` (openrc-run) ran WITHOUT it and crashed on
   faccessat(-1) again (this was the last "mystery": the child died with
   no traced syscalls because the shim wasn't loaded). FIX: `/etc/rc.conf`
   gains `rc_env_allow="LD_PRELOAD"` so the shim survives into the init
   scripts. (Tried `/etc/ld.so.preload` first — it loaded into EVERY
   process and broke the openrc parent itself under CheerpX; reverted.)

**Image/infra changes that were also required (each independently verified):**

6. **udev-trigger/udev-settle removed from the boot, `networking` removed
   from the boot runlevel.** Under CheerpX the device nodes already exist
   (the runtime creates `/dev/input/event*` itself); `udevadm trigger`
   re-processing makes udevd churn an endless "Validate module index" loop
   and `udevadm settle` hangs the boot at "Waiting for uevents to be
   processed" forever. `networking` WANTs dev-settle (pulling the hang
   back in) and its ioctl-based ifup can't work under CheerpX anyway — the
   guest NIC is configured by `/etc/local.d/desktop.start`'s eth0 retry
   loop + udhcpc (the established mechanism). udevd itself stays up
   (sysinit) for Xorg's udev monitor. Dockerfile rc-update block is now:
   `bootmisc boot; udev sysinit; udev-postmount default; dbus default;
   local default`.
7. **Xorg's udev input backend finds NO devices under CheerpX** (shallow
   emulated sysfs — no `/sys/devices/...` paths, no `device/name`
   attributes), so no pointer/keyboard attached and the explorer's
   double-click never dispatched. FIX: `xf86-input-evdev` added +
   `diskimage/rootfs/etc/X11/xorg.conf` (NEW, static): `AutoAddDevices
   false` + explicit `InputDevice` sections — event0 = CheerpXMouse
   (emulated i8042 PS/2 mouse), event1 = CheerpXKeyboard, both `Driver
   "evdev"` with raw device paths, wired via ServerLayout. (First attempt
   referenced a nonexistent Screen section → "Data incomplete in file
   /etc/X11/xorg.conf"; the layout now has only the two InputDevice
   entries.) Verified: Xorg log shows both devices attached; canvas
   pointer moves; desktop + idle-pointer specs pass.
8. **`build.sh` fingerprint did NOT include `diskimage/faccessat-fix.c`**
   — a changed shim with an unchanged Dockerfile produced the same cacheId
   and stale IndexedDB overlays served the OLD guest (this made the
   setsockopt fix "not take" until the fingerprint was fixed). FIX:
   `cat diskimage/faccessat-fix.c` added to FINGERPRINT_INPUT in build.sh.
9. **Baked deptree**: `RUN /sbin/openrc sysinit; true` added in the
   Dockerfile AFTER the rc-update block so `/run/openrc/deptree` ships in
   the image (sysinit only — NOT boot, which would pre-mark networking as
   started/failed). This is now mostly cosmetic: the guest still
   regenerates due to the clock-skew path (`Adjusting mtime of
   '/run/openrc/deptree' to ... 2695` — CheerpX `clock_gettime(REALTIME)`
   returns year 2695; `date` is correct, so it's a gettimeofday-vs-clock
   quirk; benign, boot proceeds). Keep the bake (saves a regen) but don't
   chase the 2695 — it is cosmetic.
10. **The 2695 deptree skew is a REAL ~20 s boot cost, and the interpose
    that removes it is SHIPPED (2026-08-22).** Root cause of the skew
    (in-guest probes): CheerpX's `fstatat` returns a GARBAGE st_mtime (year
    2695) for DIRECTORY inodes, so openrc's `rc_deptree_update_needed()`
    (which compares the deptree against init.d/conf.d files AND the init.d
    DIRECTORY) always sees the dir as "newer" and re-runs "Caching service
    dependencies" on every openrc invocation (~2 s each → ~20 s of boot).
    The `faccessat-fix.c` interpose of `rc_deptree_update_needed()` (skip
    the scan; the image ships a baked deptree) removes the loop entirely —
    verified in-guest: no re-cache, X starts ~6 s in, first pixels ~11 s
    (was ~26 s/45 s). The X-server wedge observed in testing is a
    PRE-EXISTING, host-load-dependent CheerpX defect, NOT caused by the
    interpose: a control build with the ORIGINAL deptree loop (no interpose)
    wedged identically (2/3, 1/3 stalls) under the same webdav+tailnet host
    load (load avg 2.7-6.9 from VS Code/WindowServer). The wedge is the
    same CheerpX WaitForSomething mainloop freeze documented in
    plans/display-bug.md (stale-fdset/select class), surfaced more by any
    fast/early X start and by the tailnet netcheck spin (browser-side
    client netchecks every ~24 s throughout boot in webdav mode). The
    wedged boot also surfaces a benign-but-loud CheerpX fault in `pgrep`
    (`Fault addr c0100000, ip 555f230d, proc /usr/bin/pgrep` — reading
    /proc through the busybox inode). Sleep/burn/churn before X did NOT
    replicate the deptree loop's stabilizing effect reliably (tested 8 s,
    20 s, 4 M burn, 300 spawns) — and since the control wedges too, the
    deptree loop was never actually load-bearing for reliability. The
    interpose ships; the residual X-wedge is a separate CheerpX-level issue
    to track (item 13).
11. **Python `__pycache__` prebake via compileall — SHIPPED 2026-08-21.**
    The round-2 trim originally deleted `__pycache__` (the interpreter
    "regenerates on demand"), but that made the FIRST import on a fresh boot
    recompile every stdlib module onto the SLOW overlay (measured: first
    `import tkinter` 1.12 s with no pyc vs 0.30 s with baked pyc; idlelib
    and the file explorer import far more and paid even more). FIX (landed
    in the Dockerfile round-2 trim): replace the `find ... -name
    __pycache__ -exec rm` with `python3 -m compileall -q -j 4
    /usr/lib/python3.14` — prebakes bytecode matching the exact installed
    3.14 interpreter (+~5.5 MiB, 1328 pyc incl. .opt-1/.opt-2 variants).
    Verified on the shipped webdav image: cold `import tkinter` 0.26 s,
    boot-to-desktop ~42 s (was ~57 s on the previous webdav build). NOTE
    the uniform ~12% interpreter startup regression (Python 3.10 → 3.14) is
    the interpreter itself (bigger libpython3.14.so 5.25 MiB vs 3.40 MiB +
    3.14 startup work), paid by every Python process — the pyc issue
    AMPLIFIES it on first boot but is not the base cause.
12. **keep-file-explorer.sh self-heal hardening (KEPT).** The stuck-explorer
    force-kill was disabled whenever `wm-clients.sh --count` failed
    (returned "" — a wedged X server leaves _NET_CLIENT_LIST unreadable).
    It now treats a WM-list failure as "no windows known"; the idle/viewer
    process guards still protect the IDLE/viewer swap, so the self-heal
    can recover a windowless explorer instead of being permanently
    disabled by the very wedge it exists to fix. (The helper it calls is
    now the shell `wm-clients.sh` — see item 15.)
13. **Desktop-app prewarm — SHIPPED 2026-08-22, REMOVED 2026-08-24.**
    `/usr/local/sbin/prewarm-apps.sh` (run by desktop.start AFTER Xorg is
    up, BEFORE the user session, bounded + non-fatal) imported the
    explorer/viewer module set (tkinter, file_types, PIL, mistune), the
    idlelib set (pyshell, editor, remote), and exercised a withdrawn Tk
    root against X, pre-fetching the .pyc blocks and warming the exec/Tk→X
    path. The .pyc blocks are now prebaked into the image at build time
    (item 11), so the import pre-fetch bought little — the two extra
    Python starts + the Tk→X probe at boot were pure boot latency, so the
    whole prewarm was removed: faster boot wins.
14. **The pgrep boot fault (`Fault addr c0100000, ip 555f230d, proc
    /usr/bin/pgrep` / `Fault from Inode 18`) is FIXED — SHIPPED
    2026-08-22.** Root cause (in-guest trace + core wasm analysis): the
    CheerpX core's `/proc/<pid>/cmdline` generation reads the process's
    argv from guest memory, and for a process still being set up (the
    desktop session spawns while the keep-alive's `pgrep -f` scans every
    pid every 3 s) it reads a bogus pointer into the i386 kernel linear
    map base (0xc0100000) — a deterministic guest-mode fault. Benign (the
    scanned pgrep dies, the next poll succeeds) but loud on every boot.
    FIX (in `faccessat-fix.c`): interpose `read(2)` and return EOF for any
    `/proc/<pid>/cmdline` read, so pgrep/ps fall back to the safe comm
    field and never trigger the core's cmdline generator. The
    single-instance/keep-alive detection that relied on `pgrep -f
    "file-explorer.py"` etc. now uses PID files written by the explorer
    (`/tmp/explorer.pid`), the viewer (`/tmp/viewer.pid`) and the IDLE
    launcher (`/tmp/idle.pid`; keep-file-explorer.sh / open-file-explorer.sh
    check them with `kill -0`); the explorer's idlelib shell-subprocess
    discovery treats empty cmdlines as matches (the ppid filter scopes it
    to the launcher's children). Verified: 3/3 clean boot repro runs,
    browser E2E 9/9 (incl. IDLE swap), rootfs smoke PASS, webdav E2E
    network+sync PASS.
15. **wm-clients.py → shell + fold the poll into the explorer — SHIPPED
    2026-08-24.** Every window poll started a fresh Python interpreter:
    the keep-alive daemon's 3 s count (`wm-clients.py --count`) and the
    explorer's IDLE/viewer watchers (`wm-clients.py --json`, 0.5–3 s
    cadence while the app is swapped in). FIX: the helper is now the pure
    busybox-ash `wm-clients.sh --count` (one `xprop -root
    _NET_CLIENT_LIST` read; same "" = failure contract), and the explorer
    reads the property itself, in-process (`file-explorer.py`
    `_wm_client_windows`, porting wm-clients.py's xprop parsing) so its
    watchers spawn no interpreter per poll — just the tiny xprop binary.
    `wm-clients.py`, the Dockerfile COPY and the smoke-test reference are
    gone; the explorer-tests' FakePopen filters now ignore `xprop`.
16. **nginx HTTP/2 + brotli-static precompression — SHIPPED 2026-08-24
    (revised same day after review).** The site listener was HTTP/1.1: boot
    issues many small ext2 byte-range GETs plus the runtime assets,
    serialized across at most ~6 connections. FIXES: `http2 on;` on the SITE
    listener only (directive form, image ships nginx 1.30.x; CONTROL/WSS
    listeners stay h1-only — their traffic is WebSocket upgrades and nginx
    has no RFC 8441 WS-over-h2); `brotli_static on;` in the http{} context
    with .br siblings generated ONCE at frontend build time by the new
    `scripts/precompress-static.sh` (wired into webvm/package.json "build",
    so make build / ci.yml / pages.yml all produce them; a missing brotli
    CLI degrades to runtime-gzip-only with a notice and exit 0).
    Deliberately NO `.gz` siblings and NO `gzip_static on`: both modules
    register PRECONTENT-phase handlers and built-in gzip_static runs first,
    so with .gz present it shadows brotli_static for every gzip-capable
    client (i.e. all browsers) and the smaller br body would never be
    served — tailscale.wasm: 31 MB raw / 7.1 MB gz / 5.0 MB br; non-br
    clients fall through to runtime gzip. The brotli STATIC module ships as
    the Alpine subpackage `nginx-mod-http-brotli` (added to
    server/Dockerfile; apk version-matches it to nginx) loaded via
    `load_module` in the template (the entrypoint renders its own
    nginx.conf, so the packaged modules include does not apply). The ext2
    location turns `gzip off`, `brotli_static off` AND `gzip_static off`
    explicitly, so byte-range serving is structurally immune to any sibling
    appearing beside the image.
17. **Parallelised boot-resource fetch in the page — SHIPPED 2026-08-24.**
    WebVM.svelte's chain was strictly sequential: hydration → xterm import/
    terminal setup → dynamic import of the CheerpX runtime (~2.5 MB JS/WASM)
    → HttpBytesDevice.create (first ext2 range GETs) → IDBDevice → Linux.
    create. FIX (`startEarlyBootFetch`, called first thing in onMount): the
    runtime import AND both device creations start at mount time,
    concurrently with terminal setup; initCheerpX awaits the promises where
    the old inline code awaited the calls. Every early promise gets a no-op
    catch at creation (an early rejection must not fire the global
    unhandledrejection → premature trap-overlay path) while still
    propagating at the await site — error routing is byte-for-byte the old
    behavior (boot failures still land in showFatal("boot")).
18. **Keep-alive: 3 s xprop poll → event-driven `xprop -spy` — SHIPPED
    2026-08-24 (revised same day after review).** keep-file-explorer.sh
    spawned one xprop binary per poll forever (item 15 made it shell, but
    each execve is still expensive under emulation) and reacted up to
    POLL_SECONDS late to every window change. FIX: a `xprop -spy -root
    _NET_CLIENT_LIST` session — it prints the current value on attach and
    one line per WM update, so a closed explorer triggers the relaunch
    decision within milliseconds. Two review findings reshaped the session
    model from the first cut (per-session pipeline-subshell state + read -t
    stall timeout): (a) WINDOWLESS_SINCE lived in the subshell, so every
    session restarted the timer at zero and the STUCK_SECONDS force-kill —
    which only ever sees ONE line per session for a stuck explorer — was
    unreachable; (b) timing out the reader does not kill the writer, so an
    idle desktop respawned a spy every ~15 s and abandoned each previous one
    (SIGPIPE only fires on its next write, which never comes when idle).
    FINAL MODEL: sessions are hard-bounded with busybox `timeout
    SESSION_SECONDS` (60 s — the spy self-exits, so no orphans and ~1
    spawn/min steady state vs the old poller's ~20/min); the windowless
    timestamp persists in /tmp/.keep-alive-windowless across sessions (the
    decision logic runs in the per-session subshell), so the force-kill
    still accumulates to STUCK_SECONDS even at one line per session; the
    force-kill path now launches explicitly (a killed windowless explorer
    emits no property update, so the desktop would otherwise wait up to a
    full session for the next attach line). Contract preserved: unparsable
    lines are skipped (never read as a zero-window desktop), the idle/viewer
    (file-viewer.py) PID-file guards still protect the swap, the 3 s grace
    delay before each session's first evaluation keeps desktop-start from
    stacking a second explorer, elapsed time via date +%s with the NOW==0
    glitch guard (subshells have disjoint $SECONDS epochs). Worst-case
    stuck-desktop heal ≈ STUCK+SESSION+BACKOFF ≈ 92 s (was ~33 s under the
    old poller) in exchange for ~20× less process churn.
    wm-clients.sh --count remains for the rootfs smoke suite.
    **REGRESSION FIX + UNIT TESTS (2026-08-24, same day):** the first spy
    revision shipped three defects that together could stop close→relaunch:
    (a) xprop's missing-atom error is printed ALL-LOWERCASE ("no such atom
    on any window") while the filter matched only title-case "No such atom"
    and "not found", so at desktop start — before Openbox creates the atom —
    the attach line was parsed as ZERO windows instead of "unreadable";
    (b) launch()/kill() ran inside the spy pipeline's subshell, the exact
    blocked-subshell-spawn pattern desktop.start documents as unreliable
    under CheerpX; (c) a timing portability bug found BY the new unit tests:
    $SECONDS is not auto-incremented by every busybox build in play
    (python:3.14-alpine treats it as a plain variable), so with `set -u` one
    environment aborted the daemon outright ("parameter not set") and the
    other silently froze every timer at 0 — wall-clock date +%s with the
    NOW==0 glitch guard is the only portable source. FINAL ARCHITECTURE:
    the spy session's reader subshell is dumb — it parses lines and writes
    only the resulting count to /tmp/.keep-alive-count (failure lines are
    never published); the MAIN shell polls it every POLL_SECONDS with
    builtin-only reads (no per-tick exec chains), applies all decisions
    itself, and owns launch()/kill() from a stable process. Locked by the
    new behavioral suite tests/unit/test_keepalive.py (sandboxed daemon +
    scripted fake xprop, 5 cases: close→relaunch regression guard,
    unreadable-line skip incl. both atom-error spellings, IDLE-swap withdraw
    protection, stuck force-kill + explicit relaunch, healthy-desktop
    no-op). Verified: unit suite 98 passed; guest ext2 rebuilt;
    tests/rootfs/smoke.sh full PASS incl. real close→relaunch under
    Xvfb/Openbox; live-guest repro relaunches in ~2 s with exactly one
    explorer afterwards.
19. **Lazy non-UI imports in the desktop apps — SHIPPED 2026-08-24.**
    Every app launch re-paid module bytecode reads + import machinery off
    the overlay before the first window painted. Measured on the guest
    image (native i386 docker; multiply by CheerpX's interpreter slowdown
    in-browser): bare python 0.19 s → +tkinter 0.28 s (UI floor, cannot
    defer) → +subprocess/threading/zipfile 0.35 s → +PIL.Image/ImageTk +
    mistune 0.77 s. FIXES: file-viewer.py resolves Pillow and mistune
    lazily (`_ensure_pillow` / `_ensure_mistune`) — availability flags stay
    None until first use, then hold real bools and the imported names are
    published into module globals so every existing bare-name call site is
    untouched; a text-file open no longer imports PIL/mistune at all
    (~0.42 s native per open). `_kind` gates image routing through the
    resolver; `_to_photo`'s ImageTk fallback became tri-state (None =
    unresolved) since the flag can no longer be known at window
    construction. file-explorer.py defers subprocess/threading/zipfile via
    tiny globals-caching accessors (`_sp`/`_th`/`_zf`, `return mod` — an
    earlier draft's `return <module>` after `import <module>` hit Python's
    local-binding rule and died as UnboundLocalError on the cached path,
    caught immediately by the in-guest suite) (~70 ms/launch). Test-suite
    contracts preserved: file-explorer-tests.py executes the app source with
    an injected harness in ONE namespace and patches `subprocess.Popen`
    through that shared global, so the harness now imports the three modules
    itself (same sys.modules singletons); file-viewer-tests.py snapshotted
    `_ns["HAVE_PILLOW"]` at exec time and now calls `_ns["_ensure_pillow"]()`
    so image tests still run exactly when the shipped viewer would use
    Pillow. Verified: py_compile all five files; guest ext2 rebuilt;
    tests/rootfs/smoke.sh full PASS (both in-guest suites incl. real
    Pillow/mistune render paths under Xvfb); frontend + server images
    rebuilt with fingerprint dbb06b8c159d.
20. **IDLE launch: per-boot loopback-verdict cache — SHIPPED 2026-08-24.**
    IDLE's startup budget (measured on the guest image, native i386): bare
    python 0.31 s + tkinter 0.15 s + idlelib.pyshell chain 0.95 s (pyc
    prebaked — inherent to IDLE). On top of that, idle3.14-launcher re-ran
    its bind→connect→select→accept loopback probe on EVERY launch whenever
    eth0 had an address — and on the rebuilt tailscale.wasm the dead accept
    path made each probe burn its select/accept timeouts (~2-8 s of
    blank-screen swap per IDLE open on tailnet deployments). The verdict is
    a static runtime property (dead-accept does not heal mid-session) and
    -n works everywhere, so it is now computed ONCE per boot: new
    `idle-loopback-cache` (probe extracted verbatim from the launcher +
    verdict file /tmp/.idle-loopback), run backgrounded at desktop.start
    right after the eth0/DHCP settle loop; idle3.14-launcher shrank to a
    pidfile write + cache consult + exec (cache miss = undecided boot →
    inline probe fallback, identical to the old behavior; first open after
    boot pays at most what it always paid). Measured in-guest: repeat calls
    0.36 s → 0.03 s on the success path; browser mode unchanged (no eth0 →
    instant "no"). Not pursued: trimming the idlelib import chain (= forking
    IDLE) and -S/frozen-modules tricks (<100 ms, breakage risk).
    Verified: shellcheck/sh -n clean (both scripts added to CI shellcheck +
    unit script lists); unit suite 100 passed; guest ext2 rebuilt
    (4214532246cf); tests/rootfs/smoke.sh full PASS (IDLE still launches,
    subprocess mode on real Linux via cached "ok"); frontend + server images
    rebuilt.
21. **Guest-wide `time.sleep` patch via sitecustomize — SHIPPED 2026-08-24.**
    Root cause of the constant ~33% engine gauge (and periodic desktop
    stutter) on tailnet deployments: sync.py's `_sleep()` degraded to a
    busy-wait spin (§16 item 5 — no native wait primitive works), pinning
    the single guest vCPU 5 s per poll cycle while Xorg/Tk/keep-alive
    starved between forced preemptions. Fix at the PLATFORM level instead
    of per-caller: new
    `diskimage/rootfs/usr/lib/python3.14/site-packages/sitecustomize.py`
    (loaded by CPython's site module in EVERY guest interpreter — sync
    agent, explorer, viewer, IDLE `-n` user code, student scripts)
    replaces `time.sleep` with a select()-timeout wait. Mechanism:
    select's timeout arm is the one guest timer proven to fire under
    CheerpX — Tcl's notifier implements every Tk after() as select(timeout),
    and display-bug.md §2.8 traces its only defect (stale fd sets on a
    0-return), which Python's select.select is immune to (returns empty
    tuples). select is bound once at patch-install (no per-call import);
    non-positive/None delegate to the native call preserving edge
    semantics; select failure falls back to it too. Deliberate scope
    limits: thread-primitive timeouts (pthread condvars) and C-level
    sleepers (busybox sleep; would need an nanosleep→poll LD_PRELOAD shim,
    Tier 2) are NOT covered; single-call shape (no deadline-retry loop — a
    spurious-wakeup retry would be a hot spin); KeyboardInterrupt surfaces
    immediately. sync.py `_sleep()` collapsed to `time.sleep(seconds)` —
    the workaround deleted because the platform got fixed. Tests: new
    tests/unit/test_sitecustomize.py execs the file against FAKE time and
    select modules in sys.modules (the pytest interpreter is never patched;
    an earlier draft learned that function-local `import select` re-imports
    at CALL time — after the test restored real modules, so select is now
    bound at install): patch installs, positive durations consume wall time
    VIA SELECT (spy asserts no fallback), select failure falls back to the
    original, zero/negative/None delegate unchanged, sync._sleep delegates
    to time.sleep. Verified: unit suite 106 passed; guest ext2 rebuilt
    (0c041e66ba0f); in-guest checks — `time.sleep.__name__ ==
    "_cheerpx_sleep"`, sleep(1) consumes 1.00 s, webdav import path OK;
    tests/rootfs/smoke.sh full PASS; frontend + server images rebuilt.
    OPEN GATE: the select-timeout-fires-under-CheerpX assumption is proven
    for Tcl but not yet bare-Python in-browser — CI's webdav E2E phase
    (sync spec across many poll cycles) is the authoritative check; if it
    falsifies (daemon stalls between scans = degraded latency, lease
    takeover recovers), pivot per review plan to gateway-round-trip pacing
    or the Tier 2 poll() shim.
22. **REGRESSION: lazy imports hang the VM under CheerpX — REVERTED to
    eager imports (2026-08-24, evening).** After pushing item 16–21, the
    GitHub Pages deployment
    (ned14.github.io/webbrowser-python-idle/alpine.html, commit 045892c)
    hung shortly after the desktop appeared. Diagnosis chain: headless
    Playwright probes against the LIVE site reproduced it 3/3 — desktop
    paints (~80 s), then a CheerpX core fault kills a python3 process
    ("Fault addr 0/4/e9b10cf0, proc /usr/bin/python3", runtime "Fault from
    Inode" ids differing per run — an internal handle, not a stable file)
    and the session wedges. CI independently failed the SAME commit in the
    E2E browser-mode boot phase while unit/lint/image builds passed.
    Native rootfs smoke could never catch this class (it does not run
    CheerpX). Root cause (by elimination + timing signature): item 19's
    LAZY imports moved stdlib import-machinery filesystem walks out of
    quiet module-init into the live Tk event loop, where they interleave
    with X11 socket traffic and trip an intermittent CheerpX
    inode-handling race; on Pages the chunked-disk (GitHubDevice) network
    timing made it fire ~25 s after first paint every time. Two local
    repro subtleties worth recording for future bisection work: (a) the
    fault is NOT layout-deterministic — one local run faulted in xkbcomp
    during X init, an identical-content rebuild then booted clean twice;
    (b) artificial host load (saturated `yes` loops) makes EVEN HEALTHY
    builds wedge with "RuntimeError: memory access out of bounds" and a
    frozen framebuffer — saturated-host probes are invalid as bisect
    signals (the docs' "busy-waits starve the guest clock"/wedge-under-
    -load findings apply to the HOST side too). FIX: explorer and viewer
    restored to EAGER imports (accessor shims kept — they now resolve
    instantly from globals, call sites untouched; viewer resolvers invoked
    once at module bottom). Perf cost reinstated vs the reverted idea:
    ~70 ms/launch explorer, ~0.42 s/viewer open (native image numbers) —
    accepted until the core inode race is fixed upstream; the lazy-import
    optimization is dead under this CheerpX generation. Verified after
    fix: py_compile both apps; unit suite 106 passed; webdav ext2 rebuilt
    (f85a00004684); tests/rootfs/smoke.sh full PASS incl. keep-alive
    relaunch; frontend + server images rebuilt. Pages redeploy requires
    push; CI's browser-mode E2E phase is the authoritative regression gate
    for exactly this class and must be green before trusting any deploy.

**The fix shim today (`diskimage/faccessat-fix.c`, built in the Dockerfile
`shimbuild` stage, installed as `/usr/local/lib/faccessat-fix.so`):**
`bad_dfd→EBADF` for faccessat/unlinkat/fstatat/mkdirat/openat/renameat/
symlinkat/readlinkat/utimensat; faithful SIG_UNBLOCK→SETMASK conversion;
ppoll→poll; setsockopt(SO_PASSCRED)→0; **EOF for `/proc/<pid>/cmdline`
reads** (the pgrep fault, item 14). Loaded via
`/usr/local/sbin/rc-preload` (inittab sysinit/boot/default lines run through
it — busybox init can't set env) + `rc_env_allow="LD_PRELOAD"` in rc.conf.
Debug-only `segv-shim.so` (trace shim) is ALSO in the image but is only
activated by explicitly setting LD_PRELOAD to it (the inittab wrapper does
not include it) — the boot runs shim-only.

#### 9.5.2 VERIFIED GREEN (current stack)

- Browser-phase Playwright: **9/9 PASS** (boot.spec 3, desktop.spec 1,
  error-overlay.spec 1, idle-pointer.spec 1, persistence.spec 2, no-egress
  in boot.spec). Run with the stack as described in §9.5.4.
- The full boot chain under CheerpX: `/sbin/init` → openrc sysinit/boot/
  default → udevd up → dbus → local → desktop.start → Xorg (KMS FB
  1344x900x32, both static input devices) → openbox → file explorer (light
  window fills the canvas) → double-click a .py row → explorer withdraws →
  IDLE maps (in-process `-n` mode via idle3.14-launcher).
- Guest docker-side: `openrc sysinit`/`boot` run fine in the image; udevd
  runs under the shim (verified manually with `rc-service udev start`).

#### 9.5.3 RESOLVED — webdav-phase guest data path (was "the ONLY remaining failure")

> **RESOLVED 2026-08-21:** see the banner above — the failure was the
> browser-mode guest build (no sync agent), not a data-path defect. With a
> webdav-built guest, network.spec + sync.spec pass. The diagnosis below
> is kept as the record of the investigation (its page-side observations
> remain valid documentation of the tun/driver behavior).

Symptom (original): `network.spec` (root visit → baked config auto-wires
tailnet → desktop up → delete webvm.lock → poll for it to reappear via the
guest sync agent) times out after 240 s: the lock NEVER reappears; same for
`sync.spec`. Everything page-side works:

- Tailnet client reaches **Running**: two `up: starting backend` lines
  (driver autoConf+up AND the cheerpOSNetInit heal — both start, both reach
  `Switching ipn state Starting -> Running`; headscale shows the node
  online at 100.64.x.x).
- `window.cjTailscaleSocket`/`parseIp`/`adapter` are all present.
- Host-side backend is fine: `curl -u webdav:webdavpass
  http://127.0.0.1:8082/webdav/webvm.lock` → 404 (fast); rapid polls fine;
  Playwright `request.get` to 8082 works standalone.
- BUT the guest never gets eth0: the guest console shows NO udhcpc/eth0
  activity, and the guest's connect(2) data path never completes — the
  sync agent's `wait_for_tailnet` never succeeds, so no lock PUT.

Notes from the debug session:
- The gateway 443/8082/8443 socat relays are correct (443→server:443,
  8082→server:8082, 8443→server:8443; gateway reaches the control plane at
  GATEWAY_CONTROL_IP=172.28.0.10). The 8082 relay was flaky once (stale
  socat; `docker compose --profile tailnet up -d --force-recreate gateway`
  fixed it — if Playwright reports "socket hang up" on 8082, recreate the
  gateway first).
- headscale node table has ~909 stale nodes (ephemeral cleanup lag) —
  cosmetic; the current session registers fine.
- The networking-bug.md §16.8 "heal" (second autoConf+up via
  cheerpOSNetInit) IS running (2 clients) but does NOT heal the guest data
  path in this session — different from the 2026-08-16 observations. This
  is the exact behavior to investigate next.
- The page-side `nc` twin probe in network.spec (drive cjTailscaleSocket
  directly: bind, connect(parseIp('100.64.0.1'), 8082), waitOutgoing) has
  NOT yet been run manually in this session — run it first to bisect
  page-side driver vs guest-side tun glue:
  ```
  const sock = new window.cjTailscaleSocket();
  sock.bind(0);
  sock.connect(window.cjTailscaleParseIp('100.64.0.1'), 8082);
  sock.waitOutgoing().then(() => console.log('SYN-OK'));
  ```
- Also untested this session: `nc -z 100.64.0.1 8082` INSIDE the guest
  (the boot diag shell) to see whether the guest connect hangs or errors.

Next-step leads (in order of cost):
a. Manual page-side socket probe (above) — if SYN-OK, the driver/tun is
   fine and the problem is the guest's eth0/tun glue (runtime creates the
   NIC only when...? investigate the tun init after the two autoConf+up
   calls); if it hangs, the driver's netstack is the problem.
b. Guest-side `nc -z 100.64.0.1 8082` (busybox nc) in the boot shell with
   the shim set — confirm where the guest connect stops.
c. Compare against the 2026-08-16 state: what made the heal work then
   (2/2 runs) vs now (0/2)? Diff: the guest image changed massively
   (openrc fix shims, static Xorg input, udev-trigger removal) — but the
   page-side driver path is unchanged; the browser runtime is the same
   1.3.8. Possibly the guest's earlier socket attempts now behave
   differently because udevd holds a netlink socket, or the removed
   `networking` service changed something.
d. Check whether `window.cjTailscaleSocket` from the HEALED (second) client
   is what the core hands the guest; the network.js comment says the core's
   own cheerpOSNetInit sets the globals used by guest connect(2).

#### 9.5.4 THE REBUILD / TEST LOOP (exact commands)

- Build guest + ext2 (browser mode overrides .env's STORAGE_BACKEND=webdav):
  `./build.sh browser`
- Fingerprint: `cat webvm/custom-disk-images/image-build.txt` (current:
  `40bd8b569d69`; it CHANGES when faccessat-fix.c or the Dockerfile/rootfs
  change — always read it fresh).
- Frontend: `cd webvm && WEBVM_MODE=browser WEBVM_IMAGE_BUILD=$(cat custom-disk-images/image-build.txt) npm run build`
- Server: `docker compose build server && docker compose up -d server --wait --wait-timeout 120`
- Gateway (for webdav phase): `docker compose --profile tailnet up -d gateway` (add `--force-recreate` if the 8082 relay flakes).
- Browser E2E: `cd tests/e2e && npx playwright test tests/boot.spec.js tests/desktop.spec.js tests/error-overlay.spec.js tests/idle-pointer.spec.js tests/persistence.spec.js --reporter=line --timeout=240000`
- Webdav E2E (from repo root, with real secrets — do not commit):
  ```
  AK=$(grep '^HEADSCALE_PREAUTHKEY=' .env | cut -d= -f2)
  WEBDAV_URL="https://127.0.0.1:8081/alpine.html#authKey=${AK}&controlUrl=https://127.0.0.1:8443&syncUrl=http://100.64.0.1:8082/webdav/&syncUser=webdav&syncPass=webdavpass"
  cd tests/e2e && E2E_WEBDAV_URL="$WEBDAV_URL" E2E_GATEWAY_IP=100.64.0.1 \
    E2E_WEBDAV_BASE="http://127.0.0.1:8082/webdav/" E2E_WEBDAV_USER=webdav E2E_WEBDAV_PASS=webdavpass \
    npx playwright test tests/network.spec.js tests/sync.spec.js --reporter=line --timeout=300000
  ```
- The guest image build needs the shim compile stages; if a standalone
  shim edit is needed, build it in a minimal context dir (the full
  `diskimage/` context FAILS on macOS with an xattr error):
  `cp diskimage/faccessat-fix.c /var/folders/5s/4zr1hh3j76bbmmhx3gl_5wn40000gn/T/kilo/fixctx/ && docker build --platform=linux/i386 -t fix-build /var/folders/5s/4zr1hh3j76bbmmhx3gl_5wn40000gn/T/kilo/fixctx` (that dir has a Dockerfile that gcc's the .c; the Dockerfile's own `shimbuild` stage does the same in-image).

#### 9.5.5 DEBUG ARTIFACTS TO REVERT BEFORE THE TIER B GATE

- `webvm/config_public_alpine.js` — currently `cmd = "/sbin/init"` (the
  CORRECT production value; do not revert this one — but during debugging
  it was switched to `/bin/sh` and `/usr/local/bin/boot-diag.sh`; if it is
  ever seen not-`/sbin/init`, restore it).
- `diskimage/segv-shim.c` + `diskimage/segv-shim.so` — untracked trace
  shim; Dockerfile has a `# DEBUG-ONLY shim (temporary)` COPY block for it
  and `rootfs/usr/local/bin/boot-diag.sh` (also debug; COPY'd + chmod in
  Dockerfile). Remove all three before the gate.
- `tests/e2e/tests/diag.spec.js` — untracked debug spec (delete).
- `diskimage/rootfs/etc/local.d/desktop.start` — contains a DEBUG-ONLY
  `grep -i "config/udev|Adding input|..." /var/log/xorg.log` block after
  the xorg log tail (marked DEBUG-ONLY in a comment; remove).
- `librc.so.1`, `/tmp/*.dis`, `/tmp/diag*.txt`, `/tmp/*.tgz`,
  `/var/folders/.../T/kilo/{librc-0.63.2.c,openrc-0.63.2/,openrc-0.62.6/,
  openrc-0.55.1/,shimctx/,fixctx/,o0626.tgz,o0551.tgz,APKINDEX,v322-*}` —
  scratch diagnosis files outside the repo (safe to delete).
- `gui-vm/`, `info` (a PNG — the first trap screenshot), `test-results/`
  (Playwright artifacts), `webvm/custom-disk-images/webvm-custom-disk.ext2`
  (build output; regenerated by build.sh) — untracked/ignored.
- The long-lived headscale volume has ~909 stale nodes — optional
  `headscale nodes expire --all` style cleanup before final acceptance.

#### 9.5.6 ENVIRONMENT QUIRKS LEARNED (save debugging time)

- The CheerpX page xterm mirrors `/dev/console`; a guest process can write
  its debug trace there and the page xterm shows it even when the guest is
  wedged. To capture the FULL stream (not just the viewport), monkey-patch
  the xterm `write()` via `page.addInitScript` polling for
  `window.__webvmTerm` and appending every write to
  `window.__consoleCapture` (the pattern in `tests/e2e/capture-trace.mjs`
  and the debug diag spec). The Playwright page 'console' events do NOT
  carry /dev/console writes (only wasm trap reports like "Fault ..." and
  "TODO: SYS_SETSOCKOPT").
- CheerpX trap signature to recognize: `RuntimeError: function signature
  mismatch` + `log: [pid:pid] Fault addr <a>, ip <a>, proc <path>` — a wild
  call. `Fault addr 0, ip 0` = null call. Both kill the WHOLE emulator
  (all processes), so the shell wedges too — run probes as the VM's first
  process (boot cmd) or read the captured console, never wait for the
  shell afterwards.
- `poll` works; `ppoll` returns -1/errno=0; `select` reports ready on an
  EMPTY pipe (the stale-fdset quirk the tcl notifier patch works around);
  `epoll_create1/ctl/wait` work.
- python3 as the VM's first process fails ("Fatal Python error: error
  evaluating path") unless it `cd /`s first; every real desktop process
  starts from a valid cwd so only probes are affected.
- Guest clock: `date` is correct, but `clock_gettime(REALTIME)` returns
  year 2695 → openrc's deptree skew messages are cosmetic noise; don't
  chase them.
- busybox `timeout`, `setitimer`, SIGALRM, SIGSEGV handlers, gdb, strace
  and `/proc/self/maps` are all unusable under CheerpX (see §9.2 item 4
  "Tooling that does NOT work").
- Docker build with `diskimage/` as the build context fails on macOS
  ("failed to xattr ... permission denied") — use minimal contexts in
  /var/folders/.../T/kilo/ for standalone shim builds.
