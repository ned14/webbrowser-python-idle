# Diagnosis: flaky WebVM boots (trap / silent-stall) — 2026-09-01/02

Symptom: loading the VM in a browser is flaky (~10-25 % on the live site,
~50-60 % locally on warm loads). The web console usually shows
`Unexpected exit RuntimeError: memory access out of bounds` (or
`table index is out of bounds`, `function signature mismatch`), and/or the
boot silently stalls. Previously dismissed as an unfixable "CheerpX upstream
bug" (plans/webvm_implementation.md §32). This session found the ROOT CAUSE,
quantified it, and shipped a fix that makes boots reliable.

## ROOT CAUSE (proven)

**Reusing the IndexedDB block-overlay store across loads crashes ~50-60 % of
boots inside the CheerpX core.** The overlay (`cjFS_/blocks_alpine_<id>/`
databases — CheerpJIndexedDBFolder "cjFS" layout, `files` object store,
record per 128 KiB ext2 block) is the OverlayDevice's read-through cache +
write COW store. When a load reuses a store written by an EARLIER session,
the boot dies — content-independently:

- Byte-verified every record against the local ext2 (djb2 over all records,
  full-content checks): ~573/590 records were byte-perfect image content,
  ~15-17 guest-write deltas, a few all-zero. Deleting the mismatched/zeroed
  records did NOT heal boots (0/6 stalls after sanitizing to byte-perfect
  records only).
- Keeping ONLY 15 guest-write records: 0/6 stalls.
- Full store wipe between boots: 8/8 OK (cold-overlay boots measured 0
  failures across 50+ runs in every configuration).
- Clean cross-boot experiment (prime → wipe → boot 1-5 with no wipes):
  OK/STALL/OK/STALL/OK — coin-flip stalls whenever the store is reused,
  even when every record was written by clean, successful boots.
- Same-session store use (a boot reading records its own session wrote) is
  FINE — only CROSS-session reuse poisons.
- Identical behaviour on CheerpX runtimes 1.3.8 AND 1.3.9 (both tested).

Conclusion: a CheerpX core defect in the cjFS overlay read-hit path when the
folder store carries state from a previous session (inode/metadata reuse —
the guest never sees wrong bytes from the HTTP layer; all network reads
byte-verified exact). Not fixable from the page, server, or guest; not fixed
by the 1.3.9 runtime.

### Failure modes (both from the same cause)
- Mode A — trap (~4-5 s): xkbcomp faults (`Fault addr 14 ... proc
  /usr/bin/xkbcomp`, `Fault from Inode 461` = /lib/ld-musl-i386.so.1) while
  executing garbage/zeroed code pages; the core reports `Unexpected exit
  RuntimeError: memory access out of bounds`.
- Mode B — silent stall (~10-18 s): guest reaches `FB 1194x800x32` (KMS up)
  then goes silent; the last reads are the explorer's `import tkinter` burst
  (tcl/tk libs at 148-152 MiB + /usr/lib/libpython3.14.so.1.0 at 60.03 MiB).
- The 416 Range errors an earlier session saw are a red herring: NOT present
  in any local failing run; nginx + h2 + browser-cache variants all ruled
  out by experiment (no-store / h1-only / byte-verified routing all failed
  or succeeded identically).

## THE FIX (shipped in this session)

**Every load boots with a fresh ephemeral overlay** (`webvm/src/routes/
alpine/+page.svelte`): `cacheId = ephemeralCacheId()` for ALL backends
(browser/samba/webdav/none — previously browser/samba/webdav used the
shared `blocks_alpine_<image-build>` store protected by the session guard).
A pre-boot sweep (`deleteOverlayDatabases()` from `$lib/cjfsVersion.js`)
removes leftover per-session stores before the VM opens its fresh one
(blocked-safe: a store held by another live tab survives and is swept by
that tab's next load; growth bounded to ~1 store per live session). The
sweep scope is deliberately ONLY this app's own `cjFS_/blocks_alpine_*`
family — NOT the runtime's generic `cjFS_/files/` store (post-review
tightening 2026-09-02: that name may belong to co-tenant CheerpX apps on a
shared origin such as a GitHub Pages account origin; this app never creates
it anyway).

Consequences (user-approved direction):
- Browser mode now persists files only within one session (like the `none`
  backend); the page copy, E2E persistence specs, and scripts/acceptance.sh
  were updated.
- samba/webdav modes are unaffected functionally: user files restore from
  the network backend at boot.
- The session guard (`$lib/sessionGuard.js`) and shared-cacheId code are now
  UNUSED by the app (modules + unit tests still in the tree and green).
  A cleanup commit may remove them + plan §9.4 references.

Supporting page-side hardening (also shipped, post-review semantics):
- `reportEngineTrap` (WebVM.svelte): a pre-desktop `Unexpected exit`
  console report triggers the one-shot auto-reload (was: overlay
  immediately for post-pixel traps; every observed trapped boot was fatal).
  Gate = `bootStarted && !fileManagerSeen` — NOT `!bootedOnce` (cx.run()
  only resolves when the guest exits, so bootedOnce stays false for the
  whole first desktop session; a mid-session trap report must show the
  overlay, never reload a working session — 2026-09-02 review fix).
- Watchdog (WebVM.svelte): UNCHANGED from the pre-session calibration for
  display VMs — `!pixelSeen` + 200 s silence + 270 s floor (an earlier
  90/150 s "post-FB" tightening was reverted after review: kmsInitialized
  flips at Linux.create() — before the guest boots — so it is not guest-FB
  evidence, and the tightened windows violated the file's documented
  invariant that stuck thresholds stay above the 240 s E2E first-pixel
  budget). Fast pre-desktop death recovery comes from the trap-report
  reload; the watchdog is only the backstop for silent halts.
- Guest-side Tk warm (`desktop.start`): background `python3 -c "import
  tkinter"` before the X session (parallel with Xorg) so the explorer's Tk
  read burst hits the FS cache. Harmless rate-reducer; keep.

## Validation numbers

- Cold loads (fresh profile): 0 failures across ~50 runs (before AND after
  the fix — the bug never affected cold loads).
- Warm loads (same profile, HTTP cache warm) with the OLD shared overlay:
  ~40-60 % failed (trap or stall).
- Warm loads with the fix: 55/56 reached the desktop (the single failure
  self-recovered via the page retry; ~96 % first-attempt).
- Unit tests: 78/78 pass (cacheId/sessionGuard/cjfsVersion tests unchanged
  and green). E2E: persistence (rewritten to the per-session contract),
  error-overlay (trap path), desktop boot — all pass.

## Reproducing / measuring locally

Stack: `make build && make up` (browser backend; the repo .env was switched
to browser for this work — see "Machine state" below). Harnesses in
tests/e2e/:
- `flaky-hunt.mjs <N> <label>` — cold-boot reliability + retry tracking.
  IMPORTANT: use INLINE `page.evaluate` canvas polling — a probe injected
  via `page.addScriptTag({type:"module"})` is LOST across the app's initial
  double navigation (alpine.html loads twice at ~80-120 ms) and reports
  false stalls for every run (this wasted hours — see git history).
- `warm-hunt.mjs <N> normal|idb-only|warm-http` — warm-boot measurement.
  VALIDITY NOTE (2026-09-02, post-fix): the discrimination matrix below was
  measured BEFORE the fix, when the app still used the shared overlay and
  harnesses could prime it. Post-fix the app's per-load sweep deletes any
  primed/edited store before each measured boot, so `normal`/`warm-http`
  both measure warm-HTTP-cache + FRESH-overlay boots (the actual post-fix
  user warm path — still worth measuring), and `idb-only` measures cold
  HTTP too. Re-testing overlay REUSE would need a sweep bypass hook.
- `overlay-repair-test.mjs`, `overlay-sanitize-test.mjs`,
  `overlay-writeonly-test.mjs`, `crossboot-test.mjs`, `idb-verify-all.mjs`,
  `byte-verify-hunt.mjs`, `warm-hunt.mjs`, `flaky-hunt.mjs` — the
  hypothesis/measurement tools (kept for the live-site phase; the rest of
  the scratch probes from this hunt were deleted). All pre-fix measurements
  cited in this file were taken with the shared-overlay app build.

## Instrumentation notes (if the runtime must be re-probed)

The block-device read path lives in `webvm/cheerpx/cx_esm.js` (served as
/cheerpx/cx_esm.js; vite copies webvm/cheerpx into every build):
- Request tag + log: in `t1`, after `a.setRequestHeader(n,c);` stash
  `a.__webvmRg=c; a.__webvmId=...` and log `[DRV-REQ]`.
- Completion logs at `j=f.a6.response;` (t1 sync path) and `a=h.a6.response;`
  (tP async path): status, byteLength, Content-Range, stashed range.
- `tU` is the "wait" transition, NOT an error path — do not log it as one.
- The `(u-1|0)+r|0` range arithmetic is 32-bit — fine below 2 GiB images.
Rebuild after patching: `cd webvm && WEBVM_MODE=browser WEBVM_IMAGE_BUILD=
$(cat ../webvm/custom-disk-images/image-build.txt) npm run build` then
`docker compose build && make up` (the server image bakes webvm/build).

ext2 forensics (image = plain file at webvm/custom-disk-images/):
`docker run --rm -v $PWD/webvm/custom-disk-images:/img:ro debian:bookworm-slim
sh -c 'debugfs -R "<cmd>" /img/webvm-custom-disk.ext2'`
- `ncheck <inode>` (461 = /lib/ld-musl-i386.so.1); `icheck <fs-block>`;
  fs block = byte/4096; overlay record /N covers fs blocks N*32..N*32+31.

## What the NEXT session (live-site phase) should do

1. Deploy this work (commit + CI + reset-cycle pulls it to
   webvm.nedprod.com). The live site runs `STORAGE_BACKEND=browser` via
   `make up`; note the reset-cycle script only restarts on git pull/rebuild.
2. Re-measure on the live site with flaky-hunt/warm-hunt against the public
   URL. Remember: Cloudflare fronts it (cert + headers rewritten; CDN
   range/caching may behave differently — byte-verify-hunt.mjs checks
   content integrity through any proxy).
3. Verify the page copy change ("files last for the current session") is
   what the site should say, and that browser-mode persistence removal is
   acceptable for the public demo (it was approved for this repo's local
   direction; the live site is the same code).
4. Watch for a NEW failure population on the live site: cold first-visit
   boots over Cloudflare (HTTP cache misses; CDN range behaviour) — measure
   boot times; the watchdog thresholds are the calibrated 200 s/270 s
   (pre-pixel), deliberately above the 240 s E2E first-pixel budget.
5. Consider the cleanup commit: remove sessionGuard.js (now unused) +
   sharedCacheId + the "Acquiring session lock" UI remnants, and update
   plan §9.4 + §32 text to the per-session overlay model. Update
   plans/webvm_implementation.md's design sections that document the shared
   overlay/session lock (cacheId derivation, E2E persistence contract).
6. IF an upstream fix for the cjFS read-hit defect ever lands, re-testing
   with the shared overlay restored would return browser-mode persistence;
   the git history + this file contain the full reproduction matrix.

## Machine state / housekeeping (as of 2026-09-02)

- Local stack: browser backend running on 127.0.0.1:8081 (guest fingerprint
  `98698f43fe00`). The repo `.env` was switched browser for this work; the
  user's previous local webdav config was NOT preserved as a file — the
  `.env.webdav-backup` copy was deleted after review (it held live
  HEADSCALE/GATEWAY/WEBDAV secrets and `.env*` is now gitignored;
  restoring webdav mode = re-derive `.env` values + `make build` +
  `make up-tailnet`, the headscale-data volume still holds the DB).
- nginx experiments (no-store, h1-only) reverted; served stack is h2 +
  immutable again.
- Homebrew node 25 on this Mac is broken (libmerve → libsimdutf.34 missing
  after a simdutf upgrade). Use `/opt/homebrew/opt/node@22/bin` first on
  PATH for npm/vite work.
- `webvm/package-lock.json` regenerated with npm 10.9.8 (was stale: `npm ci`
  failed "Missing: esbuild@0.28.2"; the lock now includes the platform
  packages so fresh macOS checkouts install cleanly). The esbuild 0.28.2
  entries come from vitest 4's optional deps.
- CheerpX runtime: reverted to the pinned 1.3.8 after testing 1.3.9 (no
  fix for this bug; keep the pin bump out of this change).
- Runtime cx_esm.js / cxcore.js: byte-identical to the committed versions
  (any instrumentation from this session was reverted via git).