# Update-to-Latest Plan — bringing all components to their latest public releases

> **Implementation status (2026-08-21):** **Tier A COMPLETE AND Tier B
> COMPLETE.** All A1–A7 shipped and re-validated; Tier B (guest on Alpine
> 3.24, Python 3.14, Tcl/Tk 8.6.17, Pillow 12, mistune 3, openbox) is DONE:
> the openrc 0.63/CheerpX boot blockers were root-caused to five
> syscall-emulation defects and fixed in one LD_PRELOAD shim +
> image changes (see §9.5.1), and the final gate ran green on 2026-08-21 —
> unit ×92, rootfs smoke ×4 backends, server integration (incl.
> join-test-client), Playwright browser phase **9/9** + webdav phase
> **12/12**. Debug artifacts removed. **Tier C remains outstanding**
> (deferred by design; frontend framework majors — see §5). The full
> chronology of the Tier B session handoffs (2026-08-19/20, including the
> "webdav data path" red herring, §9.5.3) is in git history; the plan
> `plans/webvm_implementation.md` §12/21(33)/(34) records the Tier A/B
> results.

Non-negotiable repo rules still apply: HTTPS only; HOSTNAMES ARE BANNED
(`tests/unit/test_scripts.py::test_control_host_defaults_consistent` must stay
green); no secrets committed; no `#authKey` without a matching `controlUrl`;
CheerpX runtime stays self-hosted (zero external requests); every
version-dependent claim re-verified against the pinned build (§12/21 style).

## 1. Component inventory — current pins (as of 2026-08-21)

Tier A/B uplifted everything below (the pre-uplift "current vs latest"
snapshot is in git history):

| Component | Pin today | Where pinned |
|---|---|---|
| `@leaningtech/cheerpx` + self-hosted runtime | `1.3.8` (exact) | `webvm/package.json`, lock; `webvm/cheerpx/` via `scripts/fetch-cheerpx-runtime.sh` |
| `leaningtech/webvm` frontend | `8d68d2b18fa04d72ba49bc6c5b8c684a934fc268` (2026-08-13) | vendored `webvm/`, `webvm/WEBVM_COMMIT` (only the CheerpX 1.3.8 bump taken from the upstream range — the `messages.js` promo hunks are N/A) |
| Headscale | `0.29.3` (DB volume migrated in place from 0.28.0) | `server/Dockerfile`; `server/headscale/config.yaml.template` |
| `tailscale/tailscale` gateway image | `v1.102.2` | `gateway/Dockerfile` |
| Browser tailscale wasm | tailscale source `v1.102.2`; Go `golang:1.26.6` | `scripts/rebuild-tailscale-wasm.sh`; committed `webvm/cheerpx/tun/*` |
| pysmb | `1.2.15` | `diskimage/Dockerfile` pip |
| wsgidav / cheroot | `4.3.5` / `11.1.2` | `server/Dockerfile` pip |
| Server base | `python:3.14-alpine` | `server/Dockerfile`, `compose.yaml` (`test-unit`) |
| CI Node / actions | `24`; checkout@v7, setup-node@v7, setup-buildx/qemu@v4, upload-artifact@v7, download-artifact@v8, upload-pages-artifact@v5, deploy-pages@v5 | both workflows |
| e2fsprogs helper | `ubuntu:26.04` | `build.sh` |
| Guest base (Tier B) | `i386/alpine:3.24.1` (supported to 2028-06) | `diskimage/Dockerfile` |
| Guest Python/IDLE | 3.14.7 (`idle3.14` — rename repo-wide from `idle3.10`) | apk; `python3-idle` still hard-depends on `python3-tests` → the apk-fetch+tar extraction trick stays |
| Guest Tcl/Tk | 8.6.17 + patched `libtcl8.6.so` (`third_party/`) | **NOT 8.6.18** — the apk `init.tcl` demands an exact version match (8.6.18-built override aborts tkinter; §9.2 item 1) |
| Guest Pillow/mistune | `py3-pillow 12.2.0-r0`, `py3-mistune 3.2.1-r0` | apk (viewer; see §9.1 B3c) |
| Guest i3/Xorg/xvfb | i3wm → **openbox 3.6.1** (draggable windows + ✕ Close), xorg-server 21.1.24-r0 | apk |

Tier C (deferred): svelte 4.2.20, vite 5.4.21, `@sveltejs/vite-plugin-svelte`
3.1.2, `@sveltejs/kit` 2.48.5, tailwindcss 3.4.18, `@xterm/xterm` 5.5.0,
vite-plugin-static-copy 1.0.6, html2canvas-pro 1.5.13, node-html-parser
6.1.13, fontawesome 6.7.2 — see §5.

## 2. Customization surface — what any future update must not break

This repo is NOT "upstream webvm + thin config": every directory except the
vendored `webvm/` frontend is custom-built, and the vendored frontend itself
is heavily modified (§12/21 items 24–32). An update must NEVER re-clone,
re-vendor, or wholesale-replace any of the following; the only allowed
changes are in-place version constants, image tags, config schemas, and the
documented package-drift handling below:

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
| `diskimage/scripts/*`, `diskimage/rootfs/*`, `diskimage/config/*`, `diskimage/sync/*` | B | change only where Py3.14/Tk8.6.17/Pillow12/mistune3/i3→openbox require it; behavior identical otherwise | rootfs smoke per backend |
| `diskimage/trace/libtcl8.6.so.patched` + `third_party/tcl-*` + the alpine patch build recipe | B | REBUILT by design against the exact apk tcl/tk version, same CheerpX notifier fix; `third_party/` recipe is the source of truth | in-guest Xvfb tkinter tests |
| `build.sh` (fingerprint), `Makefile`, `compose.yaml`, `.env.example` | A5, B | survive; fingerprint covers `faccessat-fix.c` (a changed shim with an unchanged Dockerfile must not reuse the same cacheId — §9.1 item 8) | unit tests |
| `tests/**` and `.github/workflows/{ci,pages}.yml` | all | change only for renamed paths, CI node/actions versions, headscale-CLI parsing; the hostname-ban + headscale assertions are the tripwires | CI itself |
| `webvm/WEBVM_COMMIT`, README pinned-version blocks, plan §12/8 + §12/21 | A/B/C | updated documentation (expected) | — |

**Protection rules (hard):**
- Never `git clone`/re-vendor any upstream project into the tree.
- Never let a lockfile regeneration move the CUSTOM pins (cheerpx exact,
  wsgidav==4.3.5, cheroot==11.1.2, pysmb==1.2.15) — edit the manifest first.
- Never hand-edit `webvm/cheerpx/*` or the wasm — runtime files change only
  through `fetch-cheerpx-runtime.sh`, the wasm only through
  `rebuild-tailscale-wasm.sh`.
- A bump is not done until its named regression gate runs green in CI.
- Every bump is a small, revertible commit (rollback = `git revert` of the
  pinned file commits; the pre-uplift pins are in git history).

## 3–4. Tier A and Tier B — execution records (COMPLETE)

Each item below is the realized outcome; the step-by-step execution plans
(including the §12/21 items re-verified per step) are in git history and in
`plans/webvm_implementation.md` §12/21(33)/(34).

### A1. CheerpX 1.3.7 → 1.3.8 + webvm pin refresh
Shipped: pins + `webvm/cheerpx/` refreshed; only `cxcore.js`,
`cxcore-no-return-call.js`, `cxcore.wasm` differ from 1.3.7 (glue and
`tun/*` byte-identical, so the rebuilt-wasm pairing surface is untouched);
the §12/21(32) trap patch applied with NO target adaptation; tun-glue pairing
gate passed (network.spec + sync.spec green with `CONTROL_HOST=127.0.0.1`);
git history's `8d68d2b` diff contains the full fetch-list reconciliation.
Note: npm also publishes 1.3.9 — NOT taken; re-verify the patch + pairing
if ever moving past 1.3.8.

### A2. Headscale 0.28.0 → 0.29.3
Shipped: `ephemeral_node_inactivity_timeout` moved to the nested
`node.ephemeral.inactivity_timeout` key; every other template key exists
unchanged in 0.29.3; CLI surface re-verified (`preauthkeys create --user
<numeric id>`, `list` masks keys, `users list` numeric-ID first column);
**SQLite upgrade verified in place** — the 0.28.0-created volume came up
clean under 0.29.3, the gateway rejoined with its recorded tailnet IP;
§12/9(l) re-confirmed: still no fixed-IP mechanism.

### A3. pysmb 1.2.10 → 1.2.15
Shipped; samba-mode agility unchanged (no live Samba in CI).

### A4. tailscale wasm toolchain Go 1.26.5 → 1.26.6 (wasm REBUILT)
`wasm_exec.js` byte-identical across the Go patch; tailnet E2E re-run.

### A5. Server base python:3.11-alpine → python:3.14-alpine
Shipped (wsgidav 4.3.5 + cheroot 11.1.2 verified on 3.14).

### A6. CI Node 20 → 24 + actions majors; A7. e2fsprogs helper 24.04 → 26.04
Shipped; keep pin-by-major style.

### B1–B4. Guest rebuild on Alpine 3.24 (COMPLETE)
Base + repos on v3.24 (supported to 2028-06, no EOL caveat). Package deltas
shipped: python3 3.14.7-r1, python3-tkinter 3.14.7-r0, python3-idle 3.14.7-r0
(still `idle3.14` + `idlelib` only via apk-fetch+tar), py3-pillow 12.2.0-r0,
py3-mistune 3.2.1-r0 (no `AstRenderer` — the viewer walks both token shapes,
diskimage/scripts/file-viewer.py), tcl/tk 8.6.17-r1, openbox 3.6.1-r8,
xorg-server/xvfb 21.1.24-r0, git 2.54.0-r0, openssh-client-default
10.3_p1-r0, pysmb via `pip3 install --break-system-packages` (PEP 668).
Version-specific rework that must survive future bumps: the `idle3.14`
rename, the WB-theme `wm`-class gap, Pillow `exif_transpose` animation
handling + `draft()`/`thumbnail()`, the mistune token-shape walker, and the
`after()`-needs-running-`mainloop()` rule (all pinned in §12/21 of the
plan). Validation per backend: rootfs smoke ×4 + the full E2E (see banner).

## 5. Tier C — frontend framework majors (deferred, separate effort)

Upstream `leaningtech/webvm` still ships Svelte 4 / Vite 5 / Tailwind 3, so
Tier C diverges this repo further from the reference and is the riskiest,
lowest-urgency tier. Do NOT mix with other work. If/when executed:
1. One commit per major bump, regenerating `package-lock.json` each time and
   running the full E2E suite + the GitHub Pages workflow (service-worker
   headers must still provide cross-origin isolation).
2. Suggested order: `@sveltejs/kit` 2.70.2 (same major) → `vite` 6/7/8 with
   `@sveltejs/vite-plugin-svelte` → Svelte 5 migration of the vendored
   components (runes/legacy) → Tailwind 4 CSS-first rewrite → remaining
   majors (`@xterm/xterm` 6, `vite-plugin-static-copy` 4.1.1, html2canvas-pro,
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
- Adhere to §2's protection rules: no re-vendoring, no lockfile-driven moves
  of the custom pins, no hand-edits of `webvm/cheerpx/*` or the wasm; a bump
  is "done" only when its §2 regression gate is green.
- Keep bump commits small and revertible — never squash an uplift until its
  tier is CI-green.
- Update README "Pinned versions", `webvm/WEBVM_COMMIT`, and the plan's
  §12/8 + §12/21 checklist as each pin changes; §12/21 items whose behavior
  can shift with a version bump must be re-run, not assumed.
- The cacheId `blocks_alpine_<fingerprint>` churns on every change that
  touches `diskimage/`; this is correct and expected — old overlays are
  orphaned, not corrupted.

## 7. Validation harness (the gate for every tier)

`make test-unit` (pytest; includes the CONTROL_HOST/template tests) →
`./build.sh <backend>` + debugfs checks → `tests/rootfs/smoke.sh <backend>`
(the in-guest explorer 98-check + viewer suites under Xvfb, real-IDLE
launch, openbox managed window, keep-alive relaunch) → `make up`/`up-tailnet`
+ `tests/server/integration.sh` (incl. join-test-client) → Playwright E2E
(boot, desktop, error-overlay, idle-pointer, persistence, no-egress;
webdav: network, sync) → GitHub Pages deployment for Tier C →
`scripts/acceptance.sh` LAN checklist. CI itself is the final gate.

## 8. Risks & open items

- **CheerpX bump beyond 1.3.8**: the §12/21(32) trap patch may need target
  adaptation; the tun-glue pairing with the rebuilt wasm is the hard gate
  (roll back to the pinned pair rather than patching the glue — the §16
  failure class in networking-bug.md).
- **Headscale bump**: config schema + CLI-output parsing in
  `entrypoint.sh`/CI are the two spots most likely to surprise; the SQLite
  DB volume is forward-compatible (verified 0.28→0.29.3).
- **Tier B follow-ups**: Py3.14/Tk8.6.17/Pillow12/mistune3 version-sensitive
  code paths now exist in-tree — future bumps re-validate, don't assume.
- **Tier C vs upstream**: accepting the divergence makes upstream syncs
  manual; record the trade-off in the plan/README when starting Tier C.
- Unverified-at-write-time (verify when changing): GitHub Actions latest
  majors; headscale config-example exact keys; `python3-idle` package
  layout; the Tcl patch on a future apk tcl/tk version.

## 9. Tier B implementation record (condensed; full session handoffs in git history)

### 9.1 Completed Tier B work — VERIFIED GREEN (docker-side)

Base `i386/alpine:3.17` → `3.24.1`, packages auto-moved (see §1),
`idle3.10` → `idle3.14` repo-wide, third_party fork at tcl/tk-8.6.17 with
only the `tcl-notifier-stale-fdset.patch` (the other aports patches are
upstreamed in 8.6.17/8.6.18), mistune 3 adapter, pysmb
`--break-system-packages`, and the boot fixes below. Rootfs smoke ×4,
unit 92/92 on python:3.14-alpine, webdav server integration PASS.

### 9.2 The openrc 0.63.2/CheerpX boot blockers (chronological numbering kept)

1. **Tcl fork must be 8.6.17, not 8.6.18**: the first attempt built the
   patched lib from 8.6.18 sources; the guest aborts tkinter with
   `package require -exact Tcl 8.6.17` version conflict. The override
   library must be built from the exact apk version. (`third_party/README.md`
   cites this as §9.2.1.)
2. **openrc 0.63 crashes when `/run/openrc` is missing**: 0.60s moved the
   svcdir to /run; `rc_dirfd()` caches dirfds and a missing dir left
   `dirfds[] = -1`, then `faccessat(-1)` hangs the emulator forever. FIX:
   the Dockerfile bakes `/run/openrc/{starting,started,stopping,inactive,
   wasinactive,failed,hotplugged,daemons,options,exclusive,scheduled,
   init.d,tmp}` + `/run/lock` + `/run/secrets`.
3. **openrc 0.63's `init.sh` aborts sysinit** when it cannot mount tmpfs on
   /run (`mount(2)` = ENOSYS). FIX: patched
   `diskimage/rootfs/usr/libexec/rc/sh/init.sh` (abort → warning + `eend 0`).
4. **SUPERSEDED — was "openrc boot spins in librc `rm_dir()`"**: the true
   cause was the five syscall-emulation defects below (§9.5.1). The
   diagnostic tooling lessons from that hunt (gdb/strace/`busybox timeout`/
   SIGALRM/SIGSEGV handlers/`/proc/self/maps` all unusable under CheerpX;
   LD_PRELOAD syscall-trace shims + `kill -SEGV` eip extraction + trap
   reports work) are preserved in §9.5.6.

### 9.3–9.4. Debug state and resume checklist (RESOLVED)

The 2026-08-19 working-tree debug artifacts (`segv-shim`, `boot-diag.sh`,
`diag.spec.js`, DEBUG-ONLY Dockerfile blocks) were all reverted as part of
the fixes; the resume checklist was executed to completion. The rebuild /
test loop they documented is §9.5.4.

### 9.5.1 ROOT CAUSE — five CheerpX syscall-emulation defects (all FIXED)

The 2026-08-19 "rm_dir readdir spin" was a red herring: the trace shim
revealed the boot actually CRASHED at `faccessat(-1, "devfs", F_OK, 0)`. The
causal chain, each fixed in `diskimage/faccessat-fix.c` (LD_PRELOAD shim,
built in the Dockerfile `shimbuild` stage, installed as
`/usr/local/lib/faccessat-fix.so`, loaded via `/usr/local/sbin/rc-preload`
from inittab + `rc_env_allow="LD_PRELOAD"` in rc.conf):

1. **`faccessat(-1)` traps CheerpX** (wasm "function signature mismatch").
   openrc 0.60+ calls `faccessat(-1, …)` BY DESIGN: `rc_dirfd(RC_DIR_INVALID)`
   returns -1 and `rc_service_state()` probes every service on every runlevel
   change (old 3.17 openrc 0.55.1 used path-based `exists()`, which is why
   this only appeared with 3.24). FIX: shim short-circuits the whole `*at()`
   family to errno=EBADF for `dfd < 0 && dfd != AT_FDCWD`.
2. **`sigprocmask(SIG_UNBLOCK)` traps CheerpX** in openrc's `exec_service()`
   child before exec. FIX: faithful conversion — read the current mask with
   SIG_SETMASK, clear the requested bits, write back (a naive no-op leaves
   all signals blocked in the child).
3. **`ppoll` returns -1/errno=0 (never waits)** while `poll` works — GLib's
   `g_poll` uses ppoll, so openbox/dbus main loops spun and windows never
   mapped. FIX: shim converts `ppoll` → `poll` (timespec → ms, sigmask
   ignored).
4. **`setsockopt(SOL_SOCKET, SO_PASSCRED)` returns EPROTONOSUPPORT** and
   the runtime logs an endless "TODO: SYS_SETSOCKOPT" retry loop — udevd's
   netlink setup busy-spun and wedged the emulator. FIX: shim fakes success
   for exactly that call; nothing in the guest depends on real credentials.
5. **openrc's `env_filter()` scrubs LD_PRELOAD** from exec'd init scripts,
   so the shim vanished for `/etc/init.d/*` and the child crashed again
   (the last "mystery": no traced syscalls because the shim wasn't loaded).
   FIX: `/etc/rc.conf` gains `rc_env_allow="LD_PRELOAD"`. (`/etc/ld.so.preload`
   was tried first — it loaded into EVERY process and broke the parent; kept
   out of the image.)

**Image/infra changes that were also required (each independently verified):**

6. **udev-trigger/udev-settle removed from the boot, `networking` removed
   from the boot runlevel** — under CheerpX the device nodes already exist;
   trigger churns udevd's "Validate module index" loop forever and settle
   hangs the boot; `networking` WANTs dev-settle and its ioctl ifup cannot
   work (the guest NIC is configured by desktop.start's eth0 retry loop +
   udhcpc). udevd itself stays up (sysinit) for Xorg's udev monitor.
   Dockerfile rc-update: `bootmisc boot; udev sysinit; udev-postmount
   default; dbus default; local default`.
7. **Xorg's udev input backend finds NO devices under CheerpX** (shallow
   emulated sysfs). FIX: `xf86-input-evdev` + static
   `diskimage/rootfs/etc/X11/xorg.conf` (AutoAddDevices false + explicit
   InputDevice sections — event0 = CheerpXMouse, event1 = CheerpXKeyboard,
   wired via ServerLayout; no Screen section). Without it no pointer/keyboard
   attaches and double-clicks never dispatch.
8. **`build.sh` fingerprint did NOT include `diskimage/faccessat-fix.c`** —
   a changed shim with an unchanged Dockerfile produced the same cacheId and
   stale IndexedDB overlays served the OLD guest. FIX: `cat diskimage/faccessat-fix.c`
   added to FINGERPRINT_INPUT.
9. **Baked deptree**: `RUN /sbin/openrc sysinit; true` after the rc-update
   block so `/run/openrc/deptree` ships (sysinit only, NOT boot). The guest
   still regenerates due to a clock-skew path — `clock_gettime(REALTIME)`
   returns year 2695; benign, boot proceeds, don't chase it.
10. **The 2695 deptree skew is a REAL ~20 s boot cost; the interpose that
    removes it is SHIPPED (2026-08-22):** CheerpX `fstatat` returns a garbage
    st_mtime (year 2695) for DIRECTORY inodes, so openrc's
    `rc_deptree_update_needed()` always re-runs "Caching service
    dependencies" on every invocation. FIX: `faccessat-fix.c` interposes
    `rc_deptree_update_needed()` (skip the scan; the image ships a baked
    deptree) — verified in-guest: X starts ~6 s in, first pixels ~11 s (was
    ~26 s/45 s). The X-server wedge observed in testing is a PRE-EXISTING,
    host-load-dependent CheerpX defect, NOT caused by the interpose (a
    control build wedged identically under the same host load).
11. **Python `__pycache__` prebake via compileall — SHIPPED 2026-08-21**
    (replacing a `__pycache__` deletion that made the first import recompile
    everything onto the slow overlay: cold `import tkinter` 1.12 s no-pyc vs
    0.30 s baked; shipped webdav image: 0.26 s, boot-to-desktop ~42 s).
    NOTE: a uniform ~12% interpreter startup regression (Python 3.10 → 3.14)
    is the interpreter itself, paid by every Python process.
12. **keep-file-explorer.sh self-heal hardening (KEPT):** a WM-list failure
    (wedged X server) is treated as "no windows known" so the force-kill can
    recover a windowless explorer instead of being disabled by the wedge.
13. **Desktop-app prewarm — SHIPPED 2026-08-22, REMOVED 2026-08-24:** items
    11 (pyc prebake) made the import pre-fetch buy little; the extra Python
    starts + Tk→X probe were pure boot latency — removed.
14. **The pgrep boot fault (`Fault addr c0100000 … /usr/bin/pgrep`) — FIXED
    2026-08-22:** the core's `/proc/<pid>/cmdline` generation reads a bogus
    argv pointer for processes still being set up → deterministic guest-mode
    fault (benign but loud). FIX (`faccessat-fix.c`): interpose `read(2)` →
    EOF for any `/proc/<pid>/cmdline` read; single-instance/keep-alive
    detection moved to PID files (`/tmp/{explorer,viewer,idle}.pid`,
    `kill -0`); idlelib shell-subprocess discovery treats empty cmdlines as
    matches. Verified 3/3 clean boots, E2E 9/9, rootfs smoke PASS.
15. **wm-clients.py → shell + fold the poll into the explorer — SHIPPED
    2026-08-24:** the keep-alive's 3 s count is now the busybox-ash
    `wm-clients.sh --count` (one `xprop -root _NET_CLIENT_LIST` read) and the
    explorer reads the property in-process (`_wm_client_windows`) — no
    Python interpreter per poll.
16. **nginx HTTP/2 + brotli-static precompression — SHIPPED 2026-08-24:**
    `http2 on;` on the SITE listener only (control/WSS listeners stay h1 —
    their traffic is WS upgrades); `brotli_static on;` with `.br` siblings
    generated once by `scripts/precompress-static.sh` (wired into the
    frontend "build"); deliberately NO `.gz` siblings (built-in gzip_static
    shadows brotli_static for every gzip-capable client); the ext2 location
    turns gzip/brotli off explicitly so byte-range serving stays immune.
    tailscale.wasm: 31 MB raw / 7.1 gz / 5.0 br.
17. **Parallelised boot-resource fetch in the page — SHIPPED 2026-08-24:**
    WebVM.svelte's `startEarlyBootFetch` starts the runtime import AND both
    device creations at mount time, concurrently with terminal setup (every
    early promise gets a no-op catch at creation so an early rejection
    cannot fire the global unhandledrejection → premature trap overlay;
    error routing unchanged).
18. **Keep-alive: 3 s xprop poll → event-driven `xprop -spy` — SHIPPED
    2026-08-24:** sessions are hard-bounded with busybox `timeout` (60 s),
    the windowless timestamp persists in `/tmp/.keep-alive-windowless`,
    launch()/kill() run in the MAIN shell from a stable process; worst-case
    stuck-desktop heal ≈ 92 s in exchange for ~20× less process churn.
    Regression fixes same day: lowercase "no such atom" parse, no
    subshell-spawn, portable wall-clock `date +%s` (busybox $SECONDS is not
    auto-incremented everywhere) — locked by tests/unit/test_keepalive.py.
19. **Lazy non-UI imports in the desktop apps — SHIPPED 2026-08-24:** the
    viewer resolves Pillow/mistune lazily; the explorer defers
    subprocess/threading/zipfile via globals-caching accessors (~70 ms/launch
    saved). **REGRESSION (2026-08-24 evening) → REVERTED to eager imports,
    see item 22.**
20. **IDLE launch: per-boot loopback-verdict cache — SHIPPED 2026-08-24:**
    `idle-loopback-cache` computes the bind→connect→select→accept verdict
    once per boot (backgrounded at desktop.start after the eth0 settle);
    idle3.14-launcher shrunk to pidfile + cache consult + exec (cache miss =
    inline probe fallback). Repeat calls 0.36 s → 0.03 s on the success path.
21. **Guest-wide `time.sleep` patch via sitecustomize — SHIPPED 2026-08-24**
    (`diskimage/rootfs/usr/lib/python3.14/site-packages/sitecustomize.py`):
    `select()`-timeout wait replacing `time.sleep` in EVERY guest interpreter
    (select's timeout arm is the one guest timer proven to fire under
    CheerpX), fixing the ~33% engine gauge + periodic desktop stutter from
    sync.py's busy-wait (item 5 of networking-bug.md §16.3). sync.py's
    `_sleep()` collapsed to `time.sleep(seconds)`. Unit-tested
    (test_sitecustomize.py). OPEN GATE at ship time: select-fires-under-
    CheerpX was proven for Tcl but not yet bare-Python in-browser — CI's
    webdav sync phase is the authoritative check; it passed.
22. **REGRESSION — lazy imports hang the VM under CheerpX; REVERTED to
    eager imports (2026-08-24, evening):** after items 16–21, the GitHub
    Pages deployment hung shortly after the desktop appeared (CheerpX core
    fault `Fault addr 0/4/e9b10cf0 … python3`, "Fault from Inode" — an
    internal handle, not a stable file); CI failed the same commit in the
    browser-mode boot phase. Root cause (by elimination + timing): lazy
    imports moved stdlib import-machinery filesystem walks into the live Tk
    event loop, interleaving with X11 socket traffic and tripping an
    intermittent CheerpX inode-handling race. Two bisect subtleties: the
    fault is NOT layout-deterministic, and artificial host load makes EVEN
    HEALTHY builds wedge ("RuntimeError: memory access out of bounds") —
    saturated-host probes are invalid as bisect signals. FIX: explorer and
    viewer restored to EAGER imports (accessor shims kept, resolvers invoked
    once at module bottom); the lazy-import optimization is dead under this
    CheerpX generation. CI's browser-mode E2E boot phase is the authoritative
    regression gate for exactly this class.

**The fix shim today** (`diskimage/faccessat-fix.c`, references in code
point here): `bad_dfd→EBADF` for faccessat/unlinkat/fstatat/mkdirat/openat/
renameat/symlinkat/readlinkat/utimensat; faithful SIG_UNBLOCK→SETMASK
conversion; ppoll→poll; setsockopt(SO_PASSCRED)→0; EOF for
`/proc/<pid>/cmdline` reads (item 14); the deptree-scan interpose (item 10).

### 9.5.2 VERIFIED GREEN (current stack)

Browser-phase Playwright **9/9**; webdav-phase **12/12**; unit **92/92**;
rootfs smoke ×4; integration.sh PASS. Full boot chain under CheerpX:
`/sbin/init` → openrc sysinit/boot/default → udevd → dbus → local →
desktop.start → Xorg (KMS FB 1344x900x32, static input devices) → openbox →
file explorer → .py double-click → explorer withdraws → IDLE maps
(in-process `-n` via idle3.14-launcher).

### 9.5.3 RESOLVED — webdav-phase guest data path (was "the ONLY remaining failure")

**RESOLVED 2026-08-21:** the failure was an artifact of the debug loop — the
webdav-phase specs were run against a BROWSER-mode guest build, whose
`/etc/webvm-backend` is `browser`; `desktop.start` then never starts the
sync agent, so `webvm.lock` can never land on the backend regardless of
tailnet health. Rebuilding the guest with `./build.sh webdav` (matching the
.env deployment) makes network.spec + sync.spec pass end to end. Full record
in `plans/webvm_implementation.md` §12/21(34).

### 9.5.4 THE REBUILD / TEST LOOP (exact commands)

- Build guest + ext2: `./build.sh <backend>`
- Fingerprint: `cat webvm/custom-disk-images/image-build.txt` (CHANGES with
  faccessat-fix.c or the Dockerfile/rootfs — always read it fresh).
- Frontend: `cd webvm && WEBVM_MODE=<mode> WEBVM_IMAGE_BUILD=$(cat custom-disk-images/image-build.txt) npm run build`
- Server: `docker compose build server && docker compose up -d server --wait --wait-timeout 120`
- Gateway (webdav phase): `docker compose --profile tailnet up -d gateway` (add `--force-recreate` if the 8082 relay flakes).
- Browser E2E: `cd tests/e2e && npx playwright test tests/boot.spec.js tests/desktop.spec.js tests/error-overlay.spec.js tests/idle-pointer.spec.js tests/persistence.spec.js --reporter=line --timeout=240000`
- Webdav E2E (from repo root, with real secrets — do not commit): set
  `E2E_WEBDAV_URL`, `E2E_GATEWAY_IP`, `E2E_WEBDAV_BASE`, `E2E_WEBDAV_USER`,
  `E2E_WEBDAV_PASS` (see `tests/e2e` and the CI workflow), run `npx playwright
  test tests/network.spec.js tests/sync.spec.js --reporter=line --timeout=300000`.
- Standalone shim builds: the full `diskimage/` context FAILS on macOS with
  an xattr error — build in a minimal context dir (see the Dockerfile's
  `shimbuild` stage, which does the same in-image).

### 9.5.5 DEBUG ARTIFACTS (all reverted as of 2026-08-21)

`segv-shim.c/.so`, `boot-diag.sh`, `diag.spec.js`, the DEBUG-ONLY Dockerfile
block and the desktop.start xorg-log grep block were removed; `config_public_alpine.js`
must always be `cmd = "/sbin/init"` (if it is ever seen otherwise, restore
it before any E2E run).

### 9.5.6 ENVIRONMENT QUIRKS LEARNED (save debugging time)

- The CheerpX page xterm mirrors `/dev/console`; a guest process can write
  its debug trace there even when the guest is wedged. To capture the FULL
  stream, monkey-patch the xterm `write()` (the pattern in
  `tests/e2e/capture-trace.mjs`); Playwright 'console' events carry only wasm
  trap reports, not /dev/console writes.
- CheerpX trap signature to recognize: `RuntimeError: function signature
  mismatch` + `log: [pid:pid] Fault addr <a>, ip <a>, proc <path>` = a wild
  call; `Fault addr 0, ip 0` = null call. Both kill the WHOLE emulator —
  run probes as the VM's first process (boot cmd) or read the captured
  console, never wait for the shell afterwards.
- `poll` works; `ppoll` returns -1/errno=0; `select` reports ready on an
  EMPTY pipe (the stale-fdset quirk the Tcl notifier patch works around);
  `epoll_create1/ctl/wait` work.
- python3 as the VM's first process fails ("Fatal Python error: error
  evaluating path") unless it `cd /`s first; only probes are affected.
- Guest clock: `date` is correct, but `clock_gettime(REALTIME)` returns
  year 2695 → openrc deptree-skew messages are cosmetic; don't chase them.
- busybox `timeout`, `setitimer`, SIGALRM, SIGSEGV handlers, gdb, strace
  and `/proc/self/maps` are all unusable under CheerpX (see §9.2 item 4).
- Docker build with `diskimage/` as the build context fails on macOS
  ("failed to xattr … permission denied") — use minimal contexts in
  /var/folders/.../T/kilo/ for standalone shim builds.