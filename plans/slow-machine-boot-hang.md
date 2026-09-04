# Diagnosis: slow-machine boot hang (keep-alive kill-loop) — 2026-09-04

Symptom: on a slow client (a low-end Chromebook), `https://webvm.nedprod.com/
alpine.html` hangs just before the File Manager appears, while
`https://webvm.io/alpine.html` boots to the desktop. The guest console reaches
the Openbox autostart and then goes silent — no `webvm desktop ready` marker,
no window, never recovers.

## Reproduction (tests/e2e/slow-boot-hunt.mjs)

The CheerpX guest runs in a Web Worker, so CDP `Emulation.setCPUThrottlingRate`
does NOT slow it (measured: no effect). The boot is disk-READ-latency-bound
(~660 × 128 KiB range reads, near-serial — see why-webvm-io-loads-faster.md),
so the faithful simulation is CDP network latency
(`Network.emulateNetworkConditions`):

- `slow-boot-hunt.mjs 0`  — boots 5-14 s local, desktop-ready marker ×1.
- `slow-boot-hunt.mjs 200` — boots, but the console shows SIX `webvm desktop
  ready` markers: the explorer was force-killed and relaunched 5 times before
  one mapped its window.
- `slow-boot-hunt.mjs 400` — **reproduces the hang bit-for-bit**: no desktop-
  ready marker, no file-manager window, 7-minute deadline hit. Verified both
  against the local stack AND the live pre-fix webvm.nedprod.com.
- `slow-boot-hunt.mjs 600` — fails earlier inside desktop.start (Xorg 60 s
  bound) — a SEPARATE pre-existing limit, not the keep-alive loop.

## ROOT CAUSE (proven): the keep-alive daemon's relaunch/kill loop

`keep-file-explorer.sh` decides "no explorer" from the pid file
`/tmp/explorer.pid`, which the explorer previously wrote only AFTER its heavy
module imports (tkinter/subprocess/threading/zipfile/file_types — 60+ s of
emulated-disk reads on a slow machine). During that window the daemon saw no
explorer and relaunched. Each relaunch stacked ANOTHER concurrent explorer,
and the multiplied disk thrash meant none of them ever mapped a window:

1. `open-file-explorer.sh` → exec python3 (no pidfile yet) → the daemon's
   windowless sweep fires at 30 s (`STUCK_SECONDS=30`) → force-kill +
   relaunch → repeat. Instrumentation (`ka_log` to /dev/console) logged
   `keep-alive: relaunch (no explorer process)` ten times in the 400 ms run
   and the console never reached `webvm desktop ready`.
2. Even WITHOUT the relaunch race, the old 30 s force-kill threshold killed
   healthy-but-slow startups: at 200 ms/read a fast-enough machine eventually
   mapped the window (after 5 kill cycles); at 400 ms/read it never did.

## THE FIX

Three coordinated changes (guest image + frontend fingerprint):

1. **`open-file-explorer.sh` writes the pidfile (`pid starttime`) BEFORE
   exec'ing python3** (exec keeps the same pid, so it is the explorer's own
   record). The keep-alive daemon sees the explorer from the very first
   moment and can never stack a second launch.
2. **`file-explorer.py` also writes the pidfile immediately at interpreter
   start** (before the heavy imports; the full `pid starttime` record — the
   recycled-pid guard stays armed from the first byte).
3. **`STUCK_SECONDS` 30 → 300** in `keep-file-explorer.sh`: a windowless-but-
   alive explorer is force-killed only after 5 min of windowlessness. On a
   slow machine a healthy Tk startup legitimately needs 2-3 min to map its
   window; the old 30 s threshold killed exactly those boots. The force-kill
   remains as a genuine-deadlock backstop (the page-side watchdog at 200 s/270
   s already reloads the page before the 300 s kill would ever fire, so the
   kill-loop cannot recur even in pathological cases).
4. Diagnostic `ka_log` lines are appended to `/dev/console` (the boot xterm)
   so future keep-alive decisions show up in the page console.

`tests/unit/test_keepalive.py` updated for the new `STUCK_SECONDS=300` literal.

## Validation

- `slow-boot-hunt.mjs 400` (local, fixed build): boots to the desktop
  (~257 s), **1** desktop-ready marker, zero relaunch lines. Pre-fix live run:
  0 markers, no desktop, deadline hit.
- `slow-boot-hunt.mjs 200`: 1 marker, desktop in ~133 s (was 6 markers,
  ~141 s with 5 kill cycles). `slow-boot-hunt.mjs 0`: 1 marker, ~14 s.
- Unit tests: 287/287 pass. Frontend vitest: 80/80 pass. rootfs smoke: PASS
  ALL (incl. both keep-alive relaunch scenarios — first- and second-generation
  zombie-safe).
- The 600 ms Xorg bound is a separate, pre-existing limitation (desktop.start
  gives Xorg 60 s; the page watchdog covers it with a reload).

## Deployment note

Live webvm.nedprod.com runs the pre-fix build (fingerprint
`62b4ae7783e9`; reproduced the hang at 400 ms through the CF front). The fix
deploys by the normal path (push → box pull/`git pull --ff-only` → `make
build` → restart on the 6-hourly reset-cycle or manually); the new `?v=`
fingerprint must be re-warmed per Cloudflare PoP after the rebuild.
