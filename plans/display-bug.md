# Display bug: the graphical desktop never renders to the canvas

**Status:** OPEN (blocker for the `desktop.spec.js` E2E guarantee and for a usable
browser desktop). The guest boots, X starts, i3 runs, input reaches the guest —
but the X server only ever presents ONE frame (the cursor) to the CheerpX canvas
and never updates. Read this document BEFORE doing any diagnosis; it records
everything established on 2026-08-10 so you can start from the current state.

**Update 2026-08-10 (afternoon):** the reference image
(`reference_images/alpine_20251007.ext2`) was downloaded and inspected in full.
It uses the **exact same Alpine 3.17.10 + xorg-server 21.1.8-r0 + modesetting
driver** as our guest, and the CheerpX runtime versions are byte-identical —
both version hypotheses are DISPROVEN (§6). The working reference differs in
how X is launched (LightDM: `vt7 -novtswitch -background none`, no `-noreset`),
has **no `xorg.conf.d`**, and uses a destructive `--off`+`--auto` resize hook.
The fix is likely to converge the guest launch on the reference shape (§9).

## 0. Live bisection log (2026-08-10, DEBUG SESSION)

Current state of the investigation — **the present path works; the guest
renders nothing by default.** This is the single most important update:

- **Proven: the CheerpX present path works in our guest.** Replacing the
  `idle3.10` autostart with `xterm -fullscreen` in i3 makes the canvas fill
  completely (1,204,980 white px) within ~9 s. The "40 px cursor only" symptom
  is therefore NOT a broken present path — it is `idle3.10` failing to render
  (plus no i3bar, so there is literally nothing to present).
- **LightDM is ruled out.** Installing `lightdm` + `lightdm-openrc` in our
  guest with `autologin-user=user`, `autologin-session=i3` (reference
  config), LightDM in the default runlevel, and desktop.start NOT launching X:
  canvas still fills with the xterm but **does not update on mouse/keyboard**.
  The reference image served through our stack updates on mouse move.
- **Xorg logs are byte-identical** between our LightDM guest and the reference
  (same `using VT number 7`, same glamor failure, same `failed to get plane
  resources`, same `ShadowFB: preferred NO`, same degenerate mode, same
  `Damage tracking initialized`). The only diff is font dir warnings and input
  device lines (timing).
- **Gettys ruled out.** Enabling gettys on tty1–6 in our LightDM guest does
  not change anything (canvas fills via xterm, still frozen on input).
- **`xev`-based destructive resize hook ruled out as the present trigger:**
  the reference presents continuously even with its hook disabled
  (`alpine_nohook.ext2`, hook chmod 0644 so Xsession skips it).

Remaining untested guest-side differences (see §8):
1. **Session content**: reference i3 autostarts `polybar` + `feh wallpaper`;
   we autostart `xterm -fullscreen` (or `idle3.10`). The reference canvas
   "changes on mouse move" may be the polybar clock, not input-driven frames —
   needs verification with a static-content reference variant.
2. `-auth` Xauthority file (LightDM passes `-auth`; we do not).
3. `~/.xinitrc` presence: the reference has none (LightDM runs i3 directly
   via `/etc/X11/xinit/Xsession`); ours still has one (not used under
   LightDM, but present).

Next bisection step: **serve the reference image with polybar/feh REMOVED from
its i3 config (static black desktop)** and test whether mouse input still
changes the canvas. This separates "reference presents input frames" from
"reference's canvas changes because polybar's clock redraws every second".
If the static reference still updates on mouse → present path differs between
guests → keep bisecting launch details. If it does NOT update → the reference
"responsiveness" was polybar, and the real bug is that OUR guest renders
nothing meaningful (idle3.10 fails) — fix the autostart content, not the
present path.

### Session round 2 (afternoon, continued)

**CONFIRMED — the reference presents input-driven frames on a STATIC desktop.**
Serving the reference image with a static i3 config (no polybar, no feh, no
autostart, pure black desktop) still shows the 40 px cursor AND the cursor
moves on mouse input (`changed: true` for both mouse moves). This is true
even with the reference's resize hook disabled (chmod 0644). **So the
reference's present path is genuinely continuous; our guest's is not.**

**CONFIRMED — our guest's cursor does NOT move, with identical Xorg logs.**
Our guest under the same conditions (static i3, no hook, LightDM, vt7) shows
the same 40 px cursor but `mouse move -> changed: false`. The Xorg logs are
byte-identical (normalized diff shows only font-dir warnings + input-device
lines). So the difference is NOT the X server startup.

**The runtime blit loop stops after ~6 frames in our guest.**
Instrumenting `CanvasRenderingContext2D.putImageData` on `#display`:
- Our guest: 6 blits total, then NEVER again (even on input).
- Reference: 6 blits, then +2 more on mouse move (keeps presenting).
This proves the CheerpX runtime capture loop halts in our guest once nothing
changes on screen — because **X has no input devices, so the cursor never
moves, so nothing changes**.

**OUR GUEST'S X SERVER NEVER REGISTERS INPUT DEVICES.** This is the sharpest
difference found so far:
- Reference Xorg log: `config/udev: Adding input device AT Translated Set 2
  keyboard (/dev/input/event0)` + `VirtualPS/2 VMware VMMouse
  (/dev/input/event1)` at ~6.3 s; `XINPUT: Adding extended input device ...`
  for both.
- Our Xorg log (LightDM AND direct-launch): the server prints "relies on udev
  to provide the list of input devices" but **never adds any input device**.
- `/dev/input/event0` and `/dev/input/event1` DO exist in our guest (verified
  via in-guest `ls /dev/input`), and `/dev/dri/card0` exists.
- `udevadm trigger` + `udevadm settle` at t+40 s inside the guest did NOT make
  X register the devices.
- udev runlevels/rules/conf are identical between reference and our guest
  (eudev, `udev`/`udev-trigger`/`udev-settle` in sysinit, `udev-postmount`
  in default).
- Gettys on tty1–6 (reference keeps them) do not change anything.
- LightDM vs our direct-Xorg launch does not change anything either — BOTH
  fail to register input devices in our guest.

Root-cause hypothesis (current best): the CheerpX **input-device uevents are
emitted before X's udev monitor starts listening** (or X's libudev monitor
never connects in our guest), so X never discovers the virtual keyboard/mouse.
The reference's boot is slower (X starts ~5.3 s vs our ~4.2 s), giving the
udev trigger/settle time to run BEFORE X starts its monitor; OR the reference
ships a udev rule/config that keeps the input events alive. Either way: no
input devices → X cursor frozen → runtime capture loop idles → no presents.

Untested next steps (see §9):
1. Diff the reference vs our X server's udev monitor connection (does our
   libudev open `/run/udev/control` / netlink?). Possibly start X AFTER
   `udevadm settle` (add `need dev-settle` to the `local` service, or start
   LightDM after udev-settle).
2. Compare `/etc/udev/rules.d` content (both empty) and check whether the
   CheerpX kernel input devices need a specific udev rule to emit `add`
   uevents (the reference may install one via `eudev` vs our package set).
3. Try `xinput`/`evdev` vs `libinput` driver registration, or start X with
   `-keeptty`/no `-novtswitch` to see if the udev monitor behaves differently.
4. Compare the actual reference boot: does the reference's udev-trigger run
   BEFORE LightDM starts X, while ours races? Add `sleep`/dependency so X
   starts after input uevents.

Note on earlier confusion: the plan's §5 claim "input devices exist
(/dev/input/event0/event1)" referred to the device NODES, not X registering
them. X registering them is the missing piece.

### Session round 3 — ROOT CAUSE FOUND AND FIXED

**THE ROOT CAUSE OF THE DISPLAY BUG IS `/.dockerenv` BAKED INTO THE GUEST
IMAGE.** It is now fixed in `build.sh` (and documented in the Dockerfile):

- The Docker daemon creates a `/.dockerenv` file in the container at start
  (runc), so `docker create` + `docker export` (how `build.sh` produces the
  guest rootfs) always carries it into the ext2. Our guest image had it; the
  reference image does NOT.
- OpenRC's `rc` reads `/.dockerenv` to autodetect a docker container and sets
  `rc_sys` accordingly. All `keyword -containers` services are then SKIPPED —
  and udev, udev-trigger, udev-settle all carry `keyword -containers`.
- Consequence: udevd never starts → `/run/udev/control` socket never exists →
  X's libudev monitor can't receive input-device uevents → X never registers
  the virtual keyboard/mouse (`/dev/input/event0/1` exist but X ignores them)
  → the X cursor freezes → the CheerpX runtime capture loop stops presenting
  after ~6 frames (it only blits when the screen changes).
- Observable signature in the boot console: the reference shows
  `Remounting devtmpfs on /dev`, `Starting udev`, `Populating /dev`,
  `Waiting for uevents`; ours went straight from `Can't continue` to the
  default runlevel. Also the OpenRC banner differed: `(i386)` vs `(i386)
  [DOCKER]`.
- Fix (applied in `build.sh`): after `tar -xf` of the exported rootfs, run
  `rm -f /tmp/rootfs/.dockerenv /tmp/rootfs/.dockerinit`. A Dockerfile
  `RUN rm -f /.dockerenv` is NOT enough — the daemon recreates the file at
  container start, after the RUN layer.
- Verified: with the file gone, udevd runs, `/run/udev/control` exists, X's
  Xorg log shows `XINPUT: Adding extended input device "VirtualPS/2 VMware
  VMMouse"` + keyboard, the cursor MOVES on mouse input, and the canvas
  updates (`changed: true` on both mouse moves with a fullscreen xterm).

**Bisection trail that led here (all ruled out):**
- vt7 alone / LightDM shape / no xorg.conf.d / gettys / destructive resize
  hook / delayed udev trigger: all negative (canvas frozen).
- LightDM installed in our guest: ruled out the launch mechanism (still
  frozen). Xorg logs byte-identical to reference.
- Serving the reference image through OUR stack: works (present path + input).
- Serving our image: frozen. → difference is guest-side, not frontend/runtime.
- `/run/udev` absent + udevd not running in our guest → input devices never
  reach X → traced to `/.dockerenv`/`keyword -containers`.

**REMAINING ISSUE (not the display bug): `idle3.10` (Tk) does not render its
window.** With the fix, a fullscreen `xterm` fills the canvas and input works,
but `idle3.10` (and a minimal `tkinter` test) leave the canvas at only the
cursor (40 px). `Tk()` appears to hang (a foreground Tk test blocked i3 from
ever starting; no window-created output reached the console). The E2E test
`desktop.spec.js` requires IDLE's white tiled window (light-pixel ratio
> 0.35). Next step: debug why Tk apps don't render (Tk X protocol / font /
extension issue), e.g. run a Tk window with a timeout and check `strace`-less
signals, or test `xmessage`/`xterm`-based window mapping to confirm it's
Tk-specific.

**UPDATE — Tk hangs in the REFERENCE image too (a CheerpX limitation, not a
guest bug).** Injected the full tkinter stack (`_tkinter.so`, `libtcl8.6.so`,
`libtk8.6.so`, `tkinter/` python pkg, `/usr/lib/tcl8.6` + `/usr/lib/tk8.6`
script dirs incl. `init.tcl`/`tk.tcl`/ttk) into a copy of the reference image
and ran the same Tk test through our stack: the reference ALSO shows only the
40 px cursor — no Tk window. The unmodified reference (polybar/feh/i3) fills
the canvas and responds to mouse, so i3 and its X clients work; only Tk
window creation fails. **`tk.Tk()` hangs in the CheerpX X environment in both
images.** The reference avoids Tk entirely (python3 without tkinter; desktop
is polybar + feh + xterm). So this is a separate CheerpX↔Tk X-protocol
limitation, not something the reference comparison can fix.

### Tk hang deep-dive (LD_PRELOAD Xlib logger)

Since ptrace/strace are unavailable (CheerpX does not implement ptrace —
verified zero occurrences across cx_esm.js/cxcore.js/cxcore.wasm), built an
**LD_PRELOAD shim** (`xcall-logger2.c`, compiled for i386 musl in
`docker.io/i386/alpine:3.17`) that interposes Xlib entry points and logs each
call with a monotonic timestamp to stderr (captured by the boot console).
Findings:

- **`XOpenDisplay(":0")` from a plain C program succeeds** (~10 ms), and from
  python `ctypes` succeeds even with SIGPIPE=SIG_IGN.
- **`tk.Tk()` hangs**, and the hang is in C (SIGALRM + `faulthandler` both
  fail to dump — the process never returns to Python).
- SIGPIPE is NOT the cause: restoring `SIG_DFL` does not fix `tk.Tk()`
  (the earlier "SIGPIPE fix" was a false lead — ctypes XOpenDisplay works
  regardless).
- The hang reproduces in the reference image (with tkinter injected) too, so
  it is CheerpX↔Tk-specific, not a guest-config issue.
- Debugging notes for the next session: the logger's `dlsym(RTLD_NEXT, ...)`
  returned NULL inside the Tk process (caused a false "infinite XOpenDisplay
  retry" artifact); `dlopen("libX11.so.6")` + `dlsym` works. The real block
  is inside `Tk_Init` after XOpenDisplay, at a C syscall level.

**Conclusion: the display bug (frozen presents/input) is FIXED by removing
`/.dockerenv`. IDLE/Tk cannot render in CheerpX regardless of guest — a
separate CheerpX limitation that the `desktop.spec.js` IDLE assertions
depend on.** The E2E's IDLE expectations may need re-scoping (e.g., to a
working X client like xterm) or a non-Tk REPL, until CheerpX supports Tk.

### Final E2E state (2026-08-10, production guest)

`desktop.spec.js` now fails at exactly ONE assertion: the IDLE light-pixel
ratio (line 119, `Expected > 0.35`, `Received 0.000033`). The error context
proves the display bug is fixed — the Xorg log shows input devices registered
(`XINPUT: Adding extended input device "VirtualPS/2 VMware VMMouse" (type:
MOUSE, id 7)` + keyboard), the cursor presents, and mouse/keyboard reach the
guest. The failing assertion is purely the Tk-window render, which cannot
happen in CheerpX (see above). The earlier "no login prompt / no boot hang"
and keyboard/mouse assertions were reached but the test's light-pixel gate
fails first.

**Recommended next step for the E2E:** change the desktop guarantee to assert
on something that renders in CheerpX (e.g., autostart a fullscreen `xterm`
instead of / in addition to `idle3.10`, and assert on its light pixels), or
scope the IDLE check to a non-Tk REPL. The display bug itself is closed.

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
   **NOTE (2026-08-10): the reference runs X on `vt7` (without `-keeptty`) and
   works; the "VT-less guest" conclusion is now suspect (§6.3, §8 item 1).**
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
   **NOTE (2026-08-10): the working reference uses the destructive `--off` +
   `--auto` pattern on randr events (§6.5). This hook may need to be restored
   to the reference shape (§8 item 4, §9 step 3).**
7. **`xorg.conf.d` for the CheerpX DRM limitation.**
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
   This does NOT fix the presentation bug (§5) but was believed to be a safe
   software-rendering config (verified Xorg reads it: `ShadowFB: enabled YES`,
   `Damage tracking initialized`). **NOTE (2026-08-10): the working reference
   has NO xorg.conf.d at all (§6.4). This file is a divergence from the
   reference and may be actively harmful — §8 item 2 and §9 step 2 propose
   removing it.**

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

**Conclusion (as of the original investigation):** this looked like a
CheerpX-virtual-DRM ↔ Xorg modesetting-driver interaction problem. The driver
cannot use planes; with glamor unavailable and no working flip path, only the
initial frame is presented. It is NOT a guest boot/config/input problem.

**However, the reference-image inspection (§6, 2026-08-10) shows the working
reference uses the SAME driver/versions and renders continuously.** The
"driver cannot present frames" framing is therefore incomplete: the SAME
modesetting driver presents fine when X is launched LightDM-style (on `vt7`,
stock config, no `-noreset`). The bug is better described as an interaction
between the CheerpX virtual DRM and the *specific Xorg launch/configuration we
use*, not between the DRM and the driver per se.

## 6. Reference comparison (the strongest lead)

The public reference `https://webvm.io/alpine.html` was tested in the SAME
headless Chromium:
- Its canvas fills completely (~1.15M of 1.2M non-black pixels) at ~t+25 s.
- Its canvas hash **changes on mouse input** (input + frame presentation work).
- The reference image is built by Leaning Technologies (`alpine-image`,
  not public); its ext2 is dated 2025-10-07.

Therefore the environment is fully capable of rendering CheerpX desktops; OUR
guest's X presentation is what differs. **The reference image was downloaded
and inspected in full** (2026-08-10) — see `reference_images/alpine_20251007.ext2`
(1.5 GB, valid clean ext2, e2fsck passes). The findings below REFRAME the
problem.

### 6.1 The X stack is byte-identical to ours (hypothesis #1 DISPROVEN)

The reference image is **Alpine 3.17.10 with xorg-server 21.1.8-r0 — the exact
same versions we pin.** There is NO newer xorg-server, NO different driver, NO
newer Alpine base:

| Component | Reference | Ours | Match |
|---|---|---|---|
| `/etc/os-release` | Alpine 3.17.10 | Alpine 3.17.10 | identical |
| xorg-server | 21.1.8-r0 | 21.1.8-r0 | identical |
| `Xorg` binary (`/usr/libexec/Xorg`) | 2230756 B | 2230756 B | identical |
| `Xorg.wrap` (`/usr/libexec/Xorg.wrap`) | 13620 B, 04555 | 13620 B, 04555 | identical |
| `modesetting_drv.so` | 113336 B, 29-Mar-2023 | 113336 B, 29-Mar-2023 | identical |
| `libglamoregl.so` | 199324 B | 199324 B | identical |
| `libshadowfb.so` / `libfbdevhw.so` / `libwfb.so` | same sizes | same sizes | identical |
| libdrm | 2.4.114-r0 | 2.4.114-r0 | identical |
| mesa / mesa-gl | 22.2.5-r1 | 22.2.5-r1 | identical |
| i3wm | 4.21.1-r0 | 4.21.1-r0 | identical |
| xf86-input-libinput | 1.2.1-r0 | 1.2.1-r0 | identical |
| `/etc/X11/xinit/xserverrc` | `exec /usr/bin/X -nolisten tcp "$@"` | identical | identical |
| `/etc/modprobe.d/{kms,blacklist,i386}.conf` | stock Alpine | stock Alpine | identical |
| eudev / udev-init-scripts | installed | installed | identical |

So "upgrade xorg-server" is a **dead end** — the working reference runs the
SAME modesetting driver that fails to present in our guest. The difference must
be in **how X is launched and configured in the guest**, or in page-side
factors.

### 6.2 CheerpX runtime 1.3.7 vs 1.3.8 is a red herring

The reference page (webvm.io, served 2026-08-10) loads CheerpX **1.3.8**
(`_app/immutable/chunks/CSlnZUTA.js`: `const t="1.3.8"`), while we pin
**1.3.7**. BUT the two runtime artifacts are **byte-identical**:

```
$ shasum cx_esm.js (1.3.7)  = 65cb8891...  = shasum cx_esm.js (1.3.8)
$ shasum cx.esm.js (1.3.7)  = e93f7a87...  = shasum cx.esm.js (1.3.8)
```

(Downloaded from `https://cxrtnc.leaningtech.com/{1.3.7,1.3.8}/cx_esm.js` and
`cx.esm.js`; both pairs identical.) Bumping `@leaningtech/cheerpx` to 1.3.8 is
therefore NOT a fix by itself.

### 6.3 The reference runs X via LightDM with a VT and NO xorg.conf.d

The reference is a **LightDM autologin → i3** guest (not our `local.d/
desktop.start` + direct Xorg approach):

- `lightdm` + `lightdm-openrc` installed; `/etc/init.d/lightdm` is in the
  `default` runlevel and runs `command=/usr/bin/lightdm`.
- `/etc/lightdm/lightdm.conf` (only non-comment lines):
  ```
  [Seat:*]
  autologin-user=user
  autologin-user-timeout=0
  autologin-session=i3
  ```
- `/usr/share/xsessions/i3.desktop` → `Exec=i3`.
- `lightdm-session` is a symlink to `/etc/X11/xinit/Xsession`, which loads
  `~/.Xresources`, sources `/etc/X11/xinit/xinitrc.d/*` (incl. the resize
  hook), then `exec`s the session (i3). The reference user has **no
  `~/.xinitrc`** and **no dbus-run-session** (i3 runs directly).

The **Xorg command LightDM builds** (from the `lightdm` binary's format string
`%server -nolisten tcp vt%d -novtswitch -background %s`) is:

```
/usr/bin/X :0 -nolisten tcp vt7 -novtswitch -background none -auth <authority>
```

Key differences vs OUR invocation
(`Xorg :0 -nolisten tcp -noreset -novtswitch -logfile /var/log/xorg.log`):

| Aspect | Reference (LightDM) | Ours |
|---|---|---|
| VT | **`vt7`** (getty VTs remain enabled in inittab!) | none (`-novtswitch` only) |
| `-noreset` | **not passed** | passed |
| `-background` | **`-background none`** | not passed |
| authority | `-auth` Xauthority file | none (xhost-free, user session connects via root Xorg) |
| `-logfile` | default `/var/log/Xorg.0.log` | `-logfile /var/log/xorg.log` |
| gettys in inittab | **all six enabled** (tty1–6) | all commented out |

**This contradicts two of our earlier conclusions:**
1. §2.3 claimed "Xorg's VT ioctls hang in the VT-less CheerpX guest" — but the
   reference keeps gettys on tty1–6 AND runs X on **vt7** successfully. VTs are
   NOT the blocker; the earlier hang was with `startx`'s `vt<N> -keeptty`
   combination. LightDM passes `vt7` WITHOUT `-keeptty` and it works.
2. §2.1 disabled gettys to remove the `login:` prompt — the reference never
   needs that because LightDM takes over vt7 and the gettys stay on tty1–6.

### 6.4 The reference has NO xorg.conf.d — our 10-cheerpx.conf is a divergence

`/etc/X11/xorg.conf.d/` **does not exist** in the reference image (only
`/etc/X11/xinit/` exists). The reference runs the modesetting driver with
**stock defaults** — no `ShadowFB`, no `AccelMethod none`, no `PageFlip false`.
Our `10-cheerpx.conf` is therefore an addition the working reference does NOT
have. Its options were called "safe" in §2.7, but they may be actively harmful
(forcing `ShadowFB true`/software paths could break the present path that the
stock config uses). **Test by removing it.**

### 6.5 The reference's resize hook is the destructive pattern we removed

Reference `/etc/X11/xinit/xinitrc.d/99-screen-resize.sh`:

```sh
#!/bin/bash
while true; do
	xev -root -event randr | while read line; do killall -q xev; done
	xrandr --output None-0 --off
	xrandr --auto
	if [ -f ~/.fehbg ]; then ~/.fehbg & fi
done &
```

That is exactly the **destructive `--off` + `--auto` pattern we removed in
§2.6** because "it blanks the display on the mis-enumerated CheerpX connector".
In the reference it is present, runs, and the desktop renders continuously.
Our non-destructive `--auto`-only polling is a DIVERGENCE from the working
reference. (Note the reference also runs a feh wallpaper, so its "canvas
fills" partly reflects real desktop content — but the point stands: `--off` +
`--auto` is not inherently display-killing in the working guest.)

### 6.6 Other reference-side facts

- Reference desktop is **i3 + polybar + feh wallpaper** (no IDLE window, no
  i3bar `bar {}` block). Its i3 config autostarts `polybar` and
  `feh --bg-fill .../alpine_bg.png`. Our guest autostarts `idle3.10`. This is
  a content difference, not a presentation-path one.
- Reference has extra X tools we lack: `xev`, `xprop`, `xset`, `xrandr`
  (needed by its resize hook).
- The disk is served to the browser via `wss://disks.webvm.io/...` using the
  CheerpX `CloudDevice` WS protocol (`diskImageType:"cloud"`); we use
  `HttpBytesDevice` (`"bytes"`). For the comparison this only mattered for
  downloading the image (see §6.7 note) — not for guest rendering.

### 6.7 How the reference image was obtained (for reproducibility)

`https://disks.webvm.io/alpine_20251007.ext2` serves **plain HTTP GET with
500** now (HEAD 200); the live page actually loads it over
`wss://disks.webvm.io/alpine_20251007.ext2` (CloudDevice protocol). The WS
handshake is: server sends `<size>-<epoch>` (e.g. `1572864000-1759846169`),
then answers each client message `"<start>-<end>"` (inclusive byte range) with
the raw bytes. **Responses to pipelined requests arrive OUT OF ORDER** (the
first parallel attempt produced a scrambled, non-ext2 file); the working
downloader issues ONE range request at a time and writes sequentially
(375×4 MiB chunks). Saved as `reference_images/alpine_20251007.ext2`.

### 6.8 Revised conclusion

The reference renders the desktop with the **same xorg-server, same modesetting
driver, same libdrm, same i3, same Alpine** — so the presentation bug is NOT in
the X stack versions at all. The working guest differs from ours in exactly
three guest-side ways:
1. **X runs on a VT** (`vt7`), launched by LightDM, without `-noreset`;
2. **no `xorg.conf.d`** (stock modesetting defaults);
3. **resize hook does `--off` + `--auto`** on randr events (plus feh).

The likely fix is to converge our guest on the reference's launch shape (VT +
stock config + LightDM-style flags) rather than upgrade any package.

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

## 8. Remaining hypotheses (in priority order, REVISED 2026-08-10)

The reference comparison (§6) eliminates the version hypotheses. The remaining
candidates are guest-side launch/config differences:

1. **VT handling.** The reference runs X on **`vt7`** (LightDM default, gettys
   on tty1–6 remain enabled) and renders fine. We run X with no VT at all
   (`-novtswitch` only). Hypothesis: X needs a VT argument (without
   `-keeptty`) for the CheerpX display path to present frames. Test: launch
   `Xorg :0 -nolisten tcp vt7 -novtswitch -background none` (LightDM's exact
   flags, no `-noreset`, no `-logfile` redirect) and re-run the fast test.
   This is the single highest-value experiment.
2. **Our `10-cheerpx.conf` (ShadowFB/AccelMethod/PageFlip) is a divergence.**
   The working reference has **no `xorg.conf.d` at all**. Our "safe" software
   config may be actively breaking the present path. Test: delete
   `10-cheerpx.conf`, rebuild guest, re-run fast test (keep the rest of the
   launch unchanged first, then combine with #1).
3. **`-noreset` / `-background none` / `-logfile` flags.** Reference omits
   `-noreset` and passes `-background none`; we do the reverse. Test after #1
   (folded into the LightDM-style command line).
4. **Destructive resize hook.** Reference's `--off` + `--auto` on randr events
   works; our `--auto`-only polling diverges. Only relevant once #1–#3 are
   tested (it was previously found to be the only present-trigger — combined
   with a VT the mode-set may finally present continuously).
5. **CheerpX runtime 1.3.8 vs 1.3.7.** **DISPROVEN as a fix** — artifacts are
   byte-identical (§6.2). No need to bump.
6. **xorg-server/modesetting version.** **DISPROVEN** — the reference runs the
   exact same 21.1.8-r0 + modesetting and renders (§6.1). Do NOT spend time
   upgrading the X stack.

_Deprecated hypotheses removed in this revision:_ "newer xorg-server in the
reference" (false — same version) and "a different display driver/path in the
reference" (false — only `modesetting_drv.so` exists in both).

## 9. Concrete next steps (in order, REVISED 2026-08-10)

Step 1 is DONE — the reference image has been downloaded and fully inspected
(see §6; saved at `reference_images/alpine_20251007.ext2`). The inspection
disproved the version hypotheses and isolated three guest-side divergences.

1. **Converge the Xorg launch on the reference's (LightDM) invocation.** In
   `diskimage/rootfs/etc/local.d/desktop.start`, change the Xorg command line
   from:
   `Xorg :0 -nolisten tcp -noreset -novtswitch -logfile /var/log/xorg.log`
   to the reference shape:
   `Xorg :0 -nolisten tcp vt7 -novtswitch -background none` (drop `-noreset`,
   drop `-logfile`; keep running as root). Rebuild the guest
   (`./build.sh browser`), re-run the fast test (§3) until the canvas presents
   continuous frames (IDLE light-pixels > 35%).
   - If VT7 works, optionally re-enable gettys in `inittab` to fully match the
     reference (or confirm they are not needed with `vt7`).
2. **Remove `10-cheerpx.conf`** (`diskimage/rootfs/etc/X11/xorg.conf.d/`) to
   return to stock modesetting defaults, matching the reference. Rebuild + fast
   test. (Do this after #1 if #1 alone doesn't fix, or together if you want one
   combined experiment — but changing one variable at a time is preferred.)
3. **Restore the destructive resize hook** (reference pattern: `xev -root
   -event randr` trigger, then `xrandr --output None-0 --off` + `--auto`, plus
   optional feh wallpaper) if #1+#2 alone still leave frames frozen. Note: the
   reference requires the `xev` package (in the reference's `apk world`), which
   we do not currently install — add it to the guest Dockerfile if restoring
   this hook.
4. **Re-validate** with `tests/rootfs/smoke.sh browser` and the E2E
   `desktop.spec.js`. Once green, re-run the whole suite (`tests/e2e` all
   specs + `tests/unit`).
5. **If VT/LightDM-shape still does not fix it**, the remaining lever is
   page-side: compare `WebVM.svelte` init order/canvas handling against the
   live webvm.io build (our repo already contains the `raiseDisplay()` fix
   from §2.5). Do NOT bump xorg-server or the CheerpX runtime (both disproven).

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
- `diskimage/rootfs/etc/X11/xorg.conf.d/10-cheerpx.conf` (ShadowFB config —
  a DIVERGENCE from the reference, see §6.4)
- `tests/e2e/tests/desktop.spec.js` (the guarantee test)

Untracked/new in this investigation (2026-08-10, not part of the guest build):
- `reference_images/alpine_20251007.ext2` — the downloaded reference image
  (1.5 GB, valid ext2; the live page serves it via the CloudDevice WS
  protocol, see §6.7). Used for the §6 comparison; safe to keep for
  re-inspection, but it is a large binary and could be gitignored.

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
- **Do NOT change the xorg-server/Alpine versions for this bug**: the
  reference image (§6.1) runs the identical stack and renders, so version
  bumps are disproven as fixes.
