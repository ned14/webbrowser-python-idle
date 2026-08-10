# Display bug: the graphical desktop never renders to the canvas

**Status:** OPEN (blocker for the `desktop.spec.js` E2E guarantee and for a usable
browser desktop). The guest boots, X starts, i3 runs, input reaches the guest —
but the X server only ever presents ONE frame (the cursor) to the CheerpX canvas
and never updates. Read this document BEFORE doing any diagnosis; it records
everything established on 2026-08-10 so you can start from the current state.

---

## 1. Symptom

In a real browser (and in Playwright headless/headed Chromium), opening
`/alpine.html`:
- The guest boots completely. **No `login:` prompt** and **no hang at
  `Starting local ...`** (both were fixed — see §2).
- The canvas shows the **X cursor** (a static ~40 white pixels at the screen
  centre) and **nothing else** — no i3 status bar, no IDLE window, no desktop.
- The canvas never updates: moving the mouse, clicking, and typing produce NO
  visible change (`getImageData` of `#display` stays identical).

The boot console (xterm) shows a normal boot log ending at
`* Starting local ... [ ok ]`.

## 2. Already fixed (keep these; do not regress them)

All of the following are DONE and verified; the current repo state is clean
(only the "real" fixes remain — all diagnostic scaffolding was removed).

1. **Login prompt removed.** `diskimage/rootfs/etc/inittab` (new file, copied by
   the Dockerfile) comments out all six `ttyN::respawn:/sbin/getty` lines. The
   guest is a single-user autologin desktop; gettys previously produced a
   console `login:` prompt instead of the desktop.
2. **X now starts (Xorg.wrap).** `/usr/bin/Xorg` is the `Xorg.wrap` security
   wrapper, which refuses to run the real X server as a non-root, non-console
   user. The guest has no console login session for `user`, so `su user -c
   startx` could never start X. Xorg is now launched **as root** (the
   display-manager pattern, exactly like LightDM in the reference image), then
   the user session runs as `user` via `~/.xinitrc`. (The plan's claim "Xorg
   refuses to run as root" is wrong for this Xorg — root is the sanctioned
   launcher.) Recorded in the plan as §12/22.
3. **startx VT hang avoided.** `startx` appends `vt<N> -keeptty` to its Xorg
   command line; Xorg's VT ioctls hang in the VT-less CheerpX guest. Xorg is
   launched directly as:
   `Xorg :0 -nolisten tcp -noreset -novtswitch -logfile /var/log/xorg.log </dev/null &`
   (from `desktop.start`, foreground socket-wait bounded to 60 s, then the user
   session is backgrounded with its output redirected to `/dev/console`).
4. **`/tmp/.X11-unix` created** (1777) in `desktop.start` before X starts.
5. **Input now reaches the guest (frontend fix).** The page console xterm
   overlay sat ON TOP of the display canvas and intercepted all mouse/keyboard
   events. Upstream `WebVM.svelte` only raised the canvas (`z-index: 5`) on
   guest **VT7 activation** (`handleActivateConsole(7)`), which a VT-less X
   session never triggers. Fixed in `webvm/src/lib/WebVM.svelte`:
   - new `raiseDisplay()` function sets `#display`'s parent `zIndex = 5`;
   - called from `handleActivateConsole(7)` (as upstream) AND unconditionally
     at the top of `initTerminal`, BEFORE `await initCheerpX()` — note
     `initCheerpX()` **never returns** (it runs the guest in
     `while(true) await cx.run(...)`), so the raise MUST happen before it.
   Verified: canvas parent computed `z-index` is now `5`.
6. **Non-destructive resize hook.** `diskimage/scripts/99-screen-resize.sh`
   previously ran `xrandr --output None-0 --off` then `--auto`, which blanks
   the display on the mis-enumerated CheerpX connector. Now it only re-applies
   `xrandr --auto` on a 3 s poll (never `--off`).
7. **`xorg.conf` for the CheerpX DRM limitation.**
   `diskimage/rootfs/etc/X11/xorg.conf.d/10-cheerpx.conf` (copied by the
   Dockerfile):
   ```
   Section "Device"
       Identifier "Card0"
       Driver "modesetting"
       Option "ShadowFB" "true"
       Option "AccelMethod" "none"
       Option "PageFlip" "false"
   EndSection
   ```
   This does NOT fix the presentation bug (§5) but is a safe software-rendering
   config (verified Xorg reads it: `ShadowFB: enabled YES`,
   `Damage tracking initialized`).

## 3. Reproduction / fast iteration loop

The E2E stack (Docker + Playwright + Chromium) works on this machine. The
existing suite lives in `tests/e2e` (Playwright 1.62.1, chromium installed).

Fast loop after any guest-image change:

```sh
./build.sh browser                      # ~3-4 min (QEMU i386)
docker compose build server             # ~1 min
docker compose up -d server             # browser mode, no secrets needed
curl -sk -o /dev/null -w "%{http_code}\n" https://127.0.0.1:8081/alpine.html  # 200
cd tests/e2e && E2E_SITE_URL="https://127.0.0.1:8081/alpine.html" npx playwright test tests/desktop.spec.js --reporter=list
docker compose down
```

Frontend-only changes (e.g. `webvm/src/lib/*.svelte`) do NOT need a guest
rebuild:
```sh
WEBVM_MODE=browser WEBVM_IMAGE_BUILD=$(cat webvm/custom-disk-images/image-build.txt) npm --prefix webvm run build
docker compose build server && docker compose up -d server
```

### Timeouts and measured timings (for calibration)

| Stage | Timeout used | Measured |
|---|---|---|
| Boot log reaches `Starting local ... [ ok ]` | 240 s | ~10–15 s |
| X cursor first canvas pixels | 240 s | **7.0–8.1 s** |
| X server socket up + session launched | 60 s | seconds after |
| i3 autostarts run | 60 s | a few seconds later |
| **IDLE window renders (light px > 35%)** | 60–240 s | **NEVER** (up to 200 s) |

Boot timing varies run to run (10–30 s); poll for markers, do not use fixed
waits.

## 4. Diagnosis tooling — read this before debugging (critical quirks)

These were hard-won; the naive approaches waste hours.

- **Reading the guest console (the page xterm):** the xterm is only readable
  reliably by monkey-patching `term.write` from an init script, because the
  terminal buffer gets cleared/reset during the boot. Pattern (works):
  ```js
  await page.addInitScript(() => {
    window.__consoleCapture = '';
    const iv = setInterval(() => {
      const t = window.__webvmTerm;                 // set by WebVM.svelte
      if (t && !t.__cap) {
        t.__cap = true;
        const ow = t.write.bind(t);
        t.write = (d) => {
          window.__consoleCapture += d instanceof Uint8Array
            ? new TextDecoder().decode(d) : String(d);
          return ow(d);
        };
        clearInterval(iv);
      }
    }, 50);
  });
  ```
  Then poll `window.__consoleCapture` for guest markers. `TextDecoder` is
  REQUIRED (appending `String(uint8)` yields comma-separated byte numbers).
  Decode in Node with `Buffer.from(...)`.
- **Guest → page console:** a guest process must write to **`/dev/console`**
  explicitly. Writes to the local service's **stderr (`>&2`) are LOST** (they
  never reach the page xterm). All guest diagnostics therefore use
  `echo ... >/dev/console` / `cat file >/dev/console`.
- **The xterm buffer (`term.buffer.active`) is unreliable** — it ends up with
  only the first ~6 boot lines + blanks (reset during boot). Use the write
  capture above, not the buffer.
- **Guest file reads from JS do not work:** the `cheerpOSStat/Open/Read`
  globals require internal runtime state and throw (`Cannot read properties of
  null (reading 'mountPoint')`). `cheerpjGetFSMountForPath` returns null for
  guest paths. Don't go down this path.
- **Canvas reads ARE valid:** `#display` uses a 2D context; `getImageData` on a
  scratch canvas drawn from it works and reflects the real (frozen) bitmap.
- **`waitForDesktop` in `boot.spec.js` is a WEAK signal:** it only requires
  ANY non-black pixel, which the static X cursor (40 px) satisfies — so the
  existing "boots the desktop" E2E passes even though the desktop never renders.

## 5. The evidence — root cause so far

The decisive data came from dumping the guest's X log to the page console
(via `cat /var/log/xorg.log >/dev/console` in `desktop.start`):

```
(II) modeset(0): using default device
(II) modeset(0): Depth 24, (==) framebuffer bpp 32
(II) modeset(0): glamor initialization failed          <-- no GL accel
(II) modeset(0): ShadowFB: preferred NO, enabled NO
(EE) modeset(0): failed to get plane resources: Invalid argument   <-- DRM ioctl fails
(II) modeset(0): EDID for output None-0
(II) modeset(0): Modeline ""x0.0    0.00  1344 1344 1344 1344  900 900 900 900 (0.0 kHz)
(II) modeset(0): Output None-0 connected
(II) modeset(0): Using sloppy heuristic for initial modes
```

Key facts established:
- The guest's CheerpX virtual DRM (`/dev/dri/card0`) **rejects Xorg's KMS plane
  query** (`DRM_IOCTL_MODE_GETPLANERESOURCES` → EINVAL). Glamor also fails
  (`libglamoregl` loads but init fails; `swrast_dri.so` is absent — harmless).
- The only reported mode is a **degenerate all-zero-timing mode** named `"0.00"`;
  `xrandr` shows `None-0 connected primary 1344x900+0+0 ... 0.00*`. `xrandr
  --mode 800x600` (and a CVT modeline) do not change it.
- Xorg still initializes the screen (1344x900, matching the canvas) and the
  **X cursor is presented exactly once** (40 white px at the screen centre).
- The X server renders subsequent content (i3 bar, windows) into the
  framebuffer but **no page flips reach the canvas**: with `ShadowFB: true`,
  `Damage tracking initialized` appears in the log, yet frames still do not
  present. Only a **mode set** triggers a presentation (the cursor frame
  appeared after the first `xrandr --auto`; with the hook disabled, even the
  cursor does not appear).
- Input devices exist: `/dev/input/event0` and `event1` are present; `xrandr`
  (run as `user`) connects to X, so X access control is fine.
- The page's console capture works reliably up to ~i3 start; some post-X
  `/dev/console` writes are captured (the `I3-AUTOSTART-RAN` marker appeared),
  but later ones may be missed — do not rely on it.

**Conclusion:** this is a CheerpX-virtual-DRM ↔ Xorg modesetting-driver
interaction problem. The driver cannot use planes; with glamor unavailable and
no working flip path, only the initial frame is presented. It is NOT a guest
boot/config/input problem.

## 6. Reference comparison (the strongest lead)

The public reference `https://webvm.io/alpine.html` was tested in the SAME
headless Chromium:
- Its canvas fills completely (~1.15M of 1.2M non-black pixels) at ~t+25 s.
- Its canvas hash **changes on mouse input** (input + frame presentation work).
- The reference image is built by Leaning Technologies (`alpine-image`,
  not public) on a recent Alpine (its ext2 is dated 2025-10-07).

Therefore the environment is fully capable of rendering CheerpX desktops; OUR
guest's X presentation is what differs. The reference uses **LightDM autologin**
(per the plan) and a newer base, so its `xorg-server`/modesetting driver almost
certainly handles the plane-query failure (or uses a different display path).

## 7. Experiments already tried (all FAILED to fix presentation)

| Experiment | Result |
|---|---|
| `startx` (original) | Xorg.wrap refused `user`; VT hang |
| `xinit -- Xorg :0 -nolisten tcp -noreset -novtswitch` | X started but same frozen presentation |
| Xorg as root, VT-less, `-novtswitch` (current) | X starts, cursor presents once |
| z-index raise (console overlay) | FIXED input; presentation unchanged |
| `xrandr --auto` polling (non-destructive hook) | cursor appears; no further frames |
| `xrandr --newmode` CVT modeline + `--addmode`/`--mode` | no effect |
| Xorg on `vt7 -keeptty` | no effect |
| `Option "SwapbuffersWait" "false"` | made it WORSE (cursor did not render) — reverted |
| `Option "ShadowFB" "true"` + `AccelMethod "none"` | no effect (kept as safe config) |
| `Option "PageFlip" "false"` | no effect |
| Disabling the resize hook entirely | cursor did not render (mode-set is the only present trigger) |
| Console capture via xterm buffer / cheerpOS | unreliable / unusable (see §4) |

## 8. Remaining hypotheses (in priority order)

1. **xorg-server version.** Our guest pins Alpine 3.17 (xorg-server 21.1.8,
   2022). The reference (2025-10) almost certainly runs a newer xorg-server
   whose modesetting driver tolerates `GETPLANERESOURCES` failing (or the newer
   CheerpX runtime emulates it). Test: bump just xorg-server (and deps) from a
   newer Alpine branch in the guest Dockerfile, or rebuild the guest base on a
   newer Alpine, and re-run the fast test. (Plan §12/8 pins versions — record
   any change there.)
2. **A different display driver/path in the reference** (e.g., `fbdev` on a
   CheerpX framebuffer, or a CheerpX-specific xorg setup). Test after
   inspecting the reference image (§9).
3. **A CheerpX runtime display-capture requirement our guest violates** (e.g.
   specific mode/DRI3/Present negotiation). Lower confidence.

## 9. Concrete next steps (in order)

1. **Inspect the reference image.** Download and extract
   `https://disks.webvm.io/alpine_20251007.ext2` (1.5 GB; HTTP 200, plain GET
   works). It is an ext2 — `e2cp`/`debugfs`/mount it read-only and compare:
   - `/usr/lib/xorg/modules/drivers/*` (driver set + versions)
   - `/usr/lib/xorg/modules/libglamoregl.so` presence
   - `/etc/X11/` configs, LightDM greeter/session setup
   - the desktop-start / LightDM Xorg invocation and flags
   - `/etc/os-release` / apk version to identify the Alpine release
2. **If it is the xorg-server version:** upgrade the guest's X stack (ideally
   keeping Alpine 3.17 if a newer xorg-server is installable, otherwise move the
   guest base forward) and re-run the fast test until the canvas presents
   continuous frames (IDLE window light-pixels > 35%).
3. **Re-validate** with `tests/rootfs/smoke.sh browser` and the E2E
   `desktop.spec.js` (below). Once green, also re-run the whole suite
   (`tests/e2e` all specs + `tests/unit`).

## 10. The E2E guarantee test (already written)

`tests/e2e/tests/desktop.spec.js` asserts, against the live canvas + console:
- no `login:` prompt and no boot hang at `Starting local ...`;
- IDLE's window is present (light-pixel ratio > 0.35 — i3's tiled IDLE window is
  white and fills the canvas);
- **keyboard** effect: click into the shell, type `print(6*7)`, Enter, and the
  canvas must change;
- **mouse** effect: clicking IDLE's `Options` menu (~x=210,y=15 at the
  1400x900 viewport) opens a dropdown (region pixel change); Escape closes it.

It currently fails at the IDLE-light-pixel assertion (correctly — the desktop
does not render). Its helpers (`waitForDesktop`, `canvasStats`, `canvasRegion`,
`consoleText`) are self-contained and reusable. NOTE: `consoleText` reads
`.xterm-rows` innerText which only shows the visible (scrolled-to-top) rows —
for the no-login/no-hang assertions this is acceptable because the boot log
tail that matters (a stuck `Starting local`) would be within the visible rows
in the failure case; if you need the full buffer, use the §4 write-capture.

## 11. Current repo state (files touched by this investigation)

Tracked modifications:
- `diskimage/Dockerfile` (inittab COPY, xorg.conf.d COPY)
- `diskimage/config/xinitrc` (sources resize hook, dbus-run-session -- i3)
- `diskimage/rootfs/etc/local.d/desktop.start` (root VT-less Xorg, socket wait,
  /tmp/.X11-unix, session launch; diagnostics via /dev/console)
- `diskimage/scripts/99-screen-resize.sh` (non-destructive polling)
- `webvm/src/lib/WebVM.svelte` (`raiseDisplay()` before initCheerpX)
- `tests/rootfs/smoke.sh` (updated checks: no gettys, VT-less Xorg, session-bus
  i3)

New files:
- `diskimage/rootfs/etc/inittab` (gettys disabled)
- `diskimage/rootfs/etc/X11/xorg.conf.d/10-cheerpx.conf` (ShadowFB config)
- `tests/e2e/tests/desktop.spec.js` (the guarantee test)

Guest fingerprint at last clean build: `e1b7cdada0ae` (browser). The local
server image is rebuilt from `webvm/build` + `webvm/custom-disk-images`; run
`make build && make up` (or the §3 loop) after any change.

## 12. Plan cross-references

- The plan's Step 2 "Boot to X" text and §12/22 record the Xorg.wrap/root
  finding. The §12/21 checklist item (e) covers the overlay device; item (h)
  covers display-related version checks.
- If you change the guest base/Alpine or the xorg-server version, update
  `plans/webvm_implementation.md` §12/8 (pinned versions) and re-verify the
  whole checklist.
