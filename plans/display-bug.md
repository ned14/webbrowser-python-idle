# Display bug: the graphical desktop never rendered to the canvas

**Status:** the display bug is **FIXED**, and the separate Tcl-under-CheerpX
hang it exposed is also **FIXED** (vendored Tcl patch, §2.8). The full
chronological investigation (dead-end workarounds, trace artifacts, session
logs) is in git history.

## 1. Display bug — root cause and fix (DONE)

**Symptom:** the guest booted, X started, i3 ran, input reached the guest —
but the canvas showed only the static 40 px X cursor and never updated.

**Root cause:** a `/.dockerenv` file baked into the guest ext2 (produced by
`docker create` + `docker export` in `build.sh`). OpenRC detected it and set
`rc_sys` to a container, so all services carrying `keyword -containers` were
skipped — including **udev, udev-trigger and udev-settle**. Without udevd,
`/run/udev/control` never exists and X's libudev monitor never receives the
input-device uevents, so **no keyboard/mouse are registered** (`/dev/input/
event0/1` exist but X ignores them). The cursor stays frozen and the CheerpX
capture loop stops presenting frames (it only blits on screen change).

**Fix (in `build.sh`):** `rm -f /tmp/rootfs/.dockerenv /tmp/rootfs/.dockerinit`
after extracting the exported rootfs. (A Dockerfile `RUN rm` is NOT enough —
the daemon recreates the file at container start.) Verified: udevd runs, Xorg
logs the input devices, the cursor moves, and the canvas updates. Bisection
trail (all ruled out): VT/launch shape, LightDM, gettys, the resize hook,
delayed udev trigger, `xorg.conf.d`, X stack versions. Working-boot signature:
`Starting udev` / `Populating /dev` / `Waiting for uevents`; the broken boot
went straight to the default runlevel (OpenRC banner suffixed `[DOCKER]`).

## 2. Tk hang deep-dive — Tcl-under-CheerpX (FIXED, §2.8)

`tk.Tk()` hung inside the CheerpX guest, so IDLE never rendered. Two distinct
CheerpX defects were found and both fixed by the vendored Tcl source patch in
§2.8 (the earlier LD_PRELOAD shims remain only as diagnostic scaffolding).
No ptrace exists in CheerpX, so all diagnosis used LD_PRELOAD interposers
(Xlib, libc, Tcl/Tk) with stderr streamed to `/dev/console` and captured
page-side (`tests/e2e/capture-trace.mjs`). Key finding history (each
superseded by the next): filesystem content was byte-identical to a standard
Alpine install (§2.1 — the environment differed, not the files); the first
hang was `getsockname()` on a non-socket fd (§2.2); the second was a Tcl
notifier `Tcl_DoOneEvent(TCL_DONT_WAIT)` storm fed by a stale-fdset
`select()` (§2.3–§2.7); both fixed in Tcl source (§2.8).

### 2.2 Direct-libc probe — the hang is `getsockname()` on a non-socket fd (DONE)

A standalone C probe (`diskimage/trace/probe.c`) running the exact syscall
sequence Tcl's channel-open performs proved:

- **`getsockname()` on a NON-SOCKET fd hangs (never returns) under CheerpX** —
  a 2-line C repro, independent of Tcl/Tk/X/Python and of the loggers. The
  guest's std fds are character devices (`fstat mode=20777 chr=1`); Tcl calls
  `getsockname()` on each standard channel to classify it, and any real Linux
  returns `ENOTSOCK` instantly. `isatty()`/`ioctl(TIOCGWINSZ)` return cleanly
  (the §2.1 TIOCGWINSZ count difference was a trace artifact, not a defect).
- Workaround shim (`diskimage/trace/getsockname-fix.c`): return `ENOTSOCK`
  immediately for any fd `fstat()` shows is not a socket — behaviourally
  identical to a correct kernel, inert on real Linux. With it, bare `tclsh`
  fully works — but `tk.Tk()` still did not reach `mainloop()`: a SECOND,
  independent defect remained.

### 2.3 (plus §2.4–§2.7) The second defect: a Tcl-notifier event-drain storm (DONE)

With `tclsh` fixed, `window.update()` (`Tcl_DoOneEvent(TCL_DONT_WAIT)`)
entered a busy loop of XSync-style flush-waits (~239k `XNoOp` +
`XEventsQueued` + `XFlush` triples with an always-POLLOUT X socket) — the
keysym/keymap build and library loading all completed; only the DONT_WAIT
drain never converged. Dead-end levers tested and rejected: blocking the X
socket (§2.4, `xblock-fix.c` — hang moved earlier), XIM disable (§2.5, no
effect; the disable didn't even take), sync short-circuit (§2.6, `xsync-fix.c`
— storm persisted as 2.4M `XFlush`). Root cause: Tcl's Unix notifier relied
on `select()` clearing the fd sets on return; CheerpX's `select()` returns 0
yet leaves the sets populated, so the notifier's ready-scan reported the X
socket readable every poll and Tk's connection probe ran forever.

### 2.8 FIXED — Tcl/Tk source patch via the vendored fork (DONE)

**IDLE now runs under CheerpX.** The fix is a source patch to Tcl 8.6.x built
from the vendored fork (`third_party/tcl-8.6.17` + `third_party/alpine/*.patch`)
and shipped as a replacement `/usr/lib/libtcl8.6.so` in the guest (owned and
documented in `third_party/README.md`; the 8.6.18-built first attempt
conflicted with apk's exact-match `package require -exact Tcl 8.6.17` —
see plans/update-to-latest.md §9.2.1). Two root causes, both fixed in Tcl
source:

1. **`getsockname()` on non-socket fds (§2.2)** — `Tcl_MakeFileChannel`
   probes every fd; fix (`tcl-getsockname-guard.patch`): guard the probe with
   `fstat() + S_ISSOCK()`. Behavior-identical on a correct kernel.
2. **The `window.update()` / `Tcl_DoOneEvent(TCL_DONT_WAIT)` storm (§2.3)**
   — fix (`tcl-notifier-stale-fdset.patch`): zero the fd sets when
   `select()` returns <= 0 in both the non-threaded wait path and the
   threaded `NotifierThreadProc`, mirroring the kernel contract.

Wired into the guest via `diskimage/Dockerfile` (COPY + override of
`/usr/lib/libtcl8.6.so` after `apk add`); the `idle3.x` autostart is via the
file explorer's *Open with IDLE*. Verified by the full E2E desktop test
(renders, keyboard, mouse).

**IDLE's shell subprocess (DONE 2026-08-12, reworked 2026-08-17, §2.11):**
IDLE by default runs the Python shell in a subprocess over a 127.0.0.1 TCP
loopback socket. Under CheerpX with no tailnet there is no loopback
networking, so the launcher (`idle3.14-launcher`) probes loopback
bindability and applies IDLE's `-n` (in-process) ONLY when the probe fails —
see §2.11 for the current gate.

### 2.9 PARTIAL — the GTK3 file manager (pcmanfm) desktop (SUPERSEDED)

> **SUPERSEDED (2026-08-14):** pcmanfm/spacefm and every §2.9 shim
> (setsockopt-fix.so, the instrumented libfm, the instrumented pcmanfm, the
> `/trace` diagnostics, the `/proc/self/mountinfo` stub) are removed from the
> image. The desktop client is now the stdlib-only Tk **file explorer**
> (`diskimage/scripts/file-explorer.py`), which never touches GTK/GIO/dconf —
> the whole deadlock class this section documented is gone with it (see
> `plans/webvm_implementation.md` §12/25). The Tcl/Tk `libtcl8.6.so.patched`
> fix (§2.8) remains, as the explorer and IDLE are both Tk apps. The §2.9
> diagnostic sources still in `diskimage/trace/` (gtk-*/inotify-*/glib-probe,
> statvfs-probe) are historical instrumentation of a removed component.

### 2.10 FIXED — after()-timer drawing freezes on screen (2026-08-17)

Timed animation freezes: the snake game's `after()`-driven canvas updates
never flushed to the framebuffer because the screen updates only when the
loop performs a full event drain — the emulated X socket stays perpetually
"readable" to the Tcl notifier (the §2.8 stale-fdset patch only zeroes the
sets on `select() <= 0`; `select() > 0`-with-stale-readiness is not covered,
so the idle pass starves). Verified standalone and in-IDLE; a
`window.update()` every 10th tick animates. **The general rule for Tk code
that must animate under CheerpX: flush from the timer callback**
(`window.update_idletasks()` usually suffices; use `update()` when in doubt)
— passive reliance on the event loop's idle redraw does not work here. The
shipped `diskimage/examples/snake-game.py` calls `update()` every
tick; the deeper notifier gap remains open as a future Tcl patch.

### 2.11 FIXED — IDLE hangs forever when opening any .py with networking enabled (2026-08-17)

**Root cause:** with the guest data path working (networking-bug.md §16.8),
`bind()` succeeds in the guest, so the launcher's old bind-only probe picked
subprocess mode — but the rebuilt tailscale.wasm's **inbound accept path is
dead** (networking-bug.md §16.9), so IDLE's shell-subprocess loopback
handshake never completes and the IpStack spin starves the display (mouse
pointer stops moving). Verified page-side with the same cjTailscale adapter
the guest's connect(2) uses.

**Fix (shipped):** `idle3.14-launcher` probes the **full loopback round
trip** — bind+listen, connect a second socket, accept — bounded by Python
socket timeouts AND the busybox `timeout` wrapper (an external kill, since
select()-based timeouts are not guaranteed to fire under CheerpX). `-n` is
applied unless the round trip succeeds. **REVISED (same day, `make up`
without the gateway):** with the tailnet client up but UNREGISTERED the
probe's own connect can wedge the guest display before any timeout fires —
the probe is now gated on `eth0` having an assigned inet address (the CheerpX
NIC exists only once the client CONNECTED); no address → `-n` unconditionally
(real Linux docker/Xvfb has eth0+DHCP → probe runs → subprocess mode pinned).

**2026-08-18 CI hardening:** the shell-cursor blink does NOT render on the
canvas (after()-timer starvation, §2.10), so the idle-pointer E2E now uses
pointer-follow as the aliveness gate; and browser-phase IDLE launches black-
screen for 20-40 s (in-process idlelib boots slowly), so the spec waits for
the BLACK→LIGHT transition itself with a generous window. Rootfs smoke still
asserts no `-n` on real Linux (probe success path).

## 3. E2E implication (CLOSED)

`tests/e2e/tests/desktop.spec.js` passes: no login prompt, no boot hang,
IDLE/explorer fills the canvas, keyboard and mouse work. The fix is the
source patch in §2.8; the LD_PRELOAD shims are NOT needed for IDLE.

## 4. Working desktop (do not regress)

- No console `login:` prompt: gettys disabled in `diskimage/rootfs/etc/inittab`.
- X launched as **root** (Xorg.wrap refuses non-root, non-console users) by
  `desktop.start`: `Xorg :0 -nolisten tcp -noreset -novtswitch -logfile
  /var/log/xorg.log` (VT-less — `startx`'s `-keeptty` VT ioctls hang in the
  VT-less guest); `/tmp/.X11-unix` (1777) created first; the user session
  runs as `user` via `~/.xinitrc` under `dbus-run-session -- openbox`.
- Canvas raised above the console overlay: `raiseDisplay()` in
  `webvm/src/lib/WebVM.svelte` (must run before `initCheerpX()`, which never
  returns).
- Non-destructive resize hook (`99-screen-resize.sh`: `xrandr --auto` poll,
  never `--off`) and `10-cheerpx.conf` (ShadowFB software rendering).
- Openbox (since 2026-08-18) gives real titlebar ✕ Close buttons; window
  enumeration is via the EWMH `_NET_CLIENT_LIST` root property (keep-alive
  uses `wm-clients.sh`; the explorer reads it in-process).

## 5. Reproducing / iterating

```sh
./build.sh browser                     # ~3-4 min (QEMU i386)
docker compose build server && docker compose up -d server
curl -sk -o /dev/null -w "%{http_code}\n" https://127.0.0.1:8081/alpine.html  # 200
cd tests/e2e && E2E_SITE_URL="https://127.0.0.1:8081/alpine.html" \
  npx playwright test tests/desktop.spec.js --reporter=list
```

Frontend-only changes skip the guest rebuild:
`WEBVM_MODE=browser WEBVM_IMAGE_BUILD=$(cat webvm/custom-disk-images/image-build.txt) npm --prefix webvm run build && docker compose build server && docker compose up -d server`.

Boot timing varies 10–30 s; poll for console/canvas markers rather than using
fixed waits. Tracing: `tests/e2e/capture-trace.mjs` + the run-mode-based
autostart (`diskimage/trace/trace-run.sh`, run-mode = `both`/`syscall`/`x11`/
`x11-entry`/`tcl`/`tclsh`).

## 6. Diagnosis tooling — hard-won quirks

- **Guest → page console:** a guest process must write to **`/dev/console`**
  explicitly; stderr/stdout are otherwise lost. Console capture requires
  monkey-patching `term.write` from an init script — the working pattern is
  in `tests/e2e/capture-trace.mjs`.
- **No ptrace in CheerpX** (strace impossible): syscalls are captured at libc
  level via `diskimage/trace/syscall-logger.c`; X11 via `xcall-logger.c`;
  Tcl/Tk via `tcl-logger.c`. Real symbols resolve via `dlopen`+`dlsym`, never
  `RTLD_NEXT` (fails inside the Tk process).
- **Guest file reads from JS do not work** (cheerpOS read globals throw); the
  only read channel is the console.
- The **hang is unkillable from the guest** (no signal delivery; the spin
  starves threads): bound captures by detecting the console stall and tearing
  the VM down page-side.

## 7. Trace artifacts

The captured trace/verify outputs were removed with the `/trace` cleanup
(diagnosis complete). Surviving diagnostic sources in `diskimage/trace/`:
`probe.c` (§2.2), `getsockname-fix.c` (§2.3), `xblock-fix.c` (§2.4 — tested
negative), `xsync-fix.c` (§2.6 — tested negative), `syscall-logger.c`; the
capture script is `tests/e2e/capture-trace.mjs`. The §2.9 GTK probes remain
as historical instrumentation of a removed component.

## 8. Current repo state

- `third_party/` — vendored Tcl/Tk 8.6.17 fork: the two Tcl fixes (§2.8)
  APPLIED and saved as `alpine/tcl-getsockname-guard.patch` and
  `alpine/tcl-notifier-stale-fdset.patch` (plus the stock Alpine patches and
  both APKBUILDs). See `third_party/README.md`.
- `diskimage/trace/libtcl8.6.so.patched` — the built patched library;
  `diskimage/Dockerfile` overrides `/usr/lib/libtcl8.6.so` with it after
  `apk add`.
- `diskimage/config/openbox/` autostarts `open-file-explorer.sh` (the stdlib
  Tk file explorer on `~/`; guarded against a second instance) and
  `keep-file-explorer.sh` (relaunches when the last window closes). IDLE is
  launched on demand — *Open with IDLE* / double-click a `.py` launches
  `idle3.14-launcher` and disables the explorer's UI for the duration.
- `diskimage/rootfs/usr/local/bin/idle3.14-launcher` — the §2.11 conditional
  launcher (round-trip probe, eth0-gated).
- `build.sh`'s content fingerprint includes `diskimage/trace/`.

## 9. Cross-references

- **Do NOT change the xorg-server/Alpine versions for this bug**: the
  reference image runs the identical stack and renders — version bumps are
  disproven.
- If you change the guest base or xorg-server, update
  `plans/webvm_implementation.md` §12/8 (pinned versions) and re-verify its
  §12/21 checklist.

## Post-boot mode-set regression in CheerpX 1.3.8/1.3.9 (2026-08-25) — RESOLVED: our-image-specific, runtime stays 1.3.8

Viewport resizes after session start froze rendering (frame static, screen
never adjusted; user report on `make up`, reproduced 3/3 on GitHub Pages as
python3 core faults ~25 s after first paint). Runtime bisect (headless
Chromium, single-variable swaps):

| stack | runtime | post-resize attrs | verdict |
|---|---|---|---|
| 474584d (openbox era) | 1.3.7 | 1344x900 -> 1060x768 | modeSet WORKS, live |
| 474584d | **1.3.8** | frozen 1344x900 | broken |
| ed711ef (upgrade commit) | 1.3.8 | frozen; also live=false | broken |
| ed711ef app + 1.3.7 runtime mix | mixed | frozen + core fault | incompatible mix |
| main | 1.3.9 | frozen 1344x900 | broken |

**Mechanism:** `setKmsCanvas` posts {type:95,width,height} to the core worker
for the KMS mode-set; on 1.3.8/1.3.9 a POST-BOOT call answers with a garbage
fallback surface ("CREATE 320x200x32") and the pipeline wedges (pointer-dead
static frame). The initial pre-pixels call works on every version; the caller
(WebVM.svelte) and deps are byte-identical across the boundary.

**Workaround shipped (WebVM.svelte `kmsInitialized`):** program the KMS
framebuffer EXACTLY ONCE at session start; afterwards viewport resizes are
absorbed by CSS scaling of the fixed backing store (correct rendering, guest
untouched and live). Guarded by `tests/e2e/tests/resize.spec.js`. Re-enable
post-boot calls only after an upstream runtime fixes the type:95 path.

**Refined diagnosis (2026-08-26, `repro/mode-set/`):** the regression is
**OUR-IMAGE-SPECIFIC**, verified headlessly and deterministically (same page,
same CDN runtime, same trigger):

| guest | 1.3.8 post-boot setKmsCanvas | core KMS trace |
|---|---|---|
| upstream alpine_20251007.ext2 | backing 1344×900 → 1062×770, LIVE | ... CREATE 320x200x32 → **CREATE 1062x770x32** |
| our webvm-custom-disk.ext2 | backing stays 1344×900, frame STATIC | ... CREATE 320x200x32 → (never superseded) |

The "garbage 320×200" is the core's **intermediate placeholder on BOTH
images**; the difference is whether the guest completes the disconnect/
reconnect re-negotiation (`xrandr --output None-0 --off` → `--auto`) that
makes the core program the real surface. **Our guest never completes it** —
the X server wedges inside the `xrandr --off` round-trip; the runtime version
is not the driver (1.3.7 merely tolerated the missing re-negotiation).
Ruled out by single-variable tests: the resize-loop script, xorg-server
version (21.1.8 == upstream's), `AutoAddDevices` true/false, modesetting
ShadowFB config. Remaining delta (next bisect step): the X session launch
path — our direct root `Xorg -novtswitch` + `su user` session vs upstream's
lightdm-launched server. Full details, the submission-ready `upstream.html`
repro, and the new CheerpX KMS facts (two-phase GETRESOURCES, legacy ADDFB
only, ext2 block-group limits) are in **`repro/mode-set/README.md`** — file
the upstream report from there.

**CORRECTION + pin history (2026-08-26, project-owner direction):** a
sporadic early-boot python3 fault ("Fault addr c014…", "Fault from Inode N" —
Inode 1100 = libpython) is a VERSION-INDEPENDENT core page-in defect (struck
identically on 1.3.7 ten consecutive boots; no shared page cache between
guest processes). The runtime was briefly re-pinned to 1.3.7 on owner
direction, then **restored to 1.3.8 the same day** — the 1.3.7
"last-version-where-mode-set-works" premise no longer holds (the failure is
guest-side, proven above), and 1.3.8 is the version documented to boot
headless reliably (1.3.7 fails every headless boot with the early-boot
fault). **Shipped state: runtime 1.3.8 + one-shot kmsInitialized guard +
select-based sitecustomize sleep patch.** Open items: upstream report for
BOTH core defects (post-boot type:95 mode-set re-negotiation requirement;
sporadic overlay page-in fault), and re-testing the post-boot setKmsCanvas
call path when a fixed runtime lands (flip `kmsInitialized` off).

## CI E2E flake: renderer "Target crashed" on late boots (2026-08-26) — FIXED: fresh browser per test

CI E2E (browser phase, `workers: 1`) began failing with `page.evaluate:
Target crashed` / `Page crashed` at ~40 s into boots — but ONLY from the
4th–5th VM boot of the run onward; `boot.spec`'s first boots always
survived, and earlier runs of the same commit passed on re-run (0baae29:
fail → pass). Runs failed with either boot HANGS (lightRatio ≈ 0 for the
full window — swap thrash) or renderer CRASHES (OOM) — both signatures of
memory exhaustion, not a code regression: the built-in Playwright `browser`
fixture is WORKER-scoped, so all ~8 sequential VM sessions shared one
Chromium whose renderers (same origin → same process pool) accumulated each
session's WASM heap until the runner's ~7 GB was gone.

**Fix:** `tests/e2e/lib/browser.js` shadows the `browser` fixture with a
TEST-scoped one (launch → use → close), so every VM boot starts from a clean
browser and a crashed attempt cannot poison the next test; all specs import
`test`/`expect` from it. Also merged `boot.spec`'s two full-boot tests into
one (7 full boots per run → 6).