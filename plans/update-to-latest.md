# Update-to-Latest Plan — bringing all components to their latest public releases

Investigation date: 2026-08-16. Sources: npm registry `/latest`, PyPI JSON,
Docker Hub tag APIs, GitHub releases APIs, the CheerpX CDN
(`cxrtnc.leaningtech.com/1.3.8/`), Alpine `APKINDEX.tar.gz` for x86, Node/Go
release feeds. Versions below were verified live on 2026-08-16; re-verify
anything marked [verify at implementation] when the pin changes.

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