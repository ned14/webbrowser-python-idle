# CheerpX-VM Tk startup trace — notes

Companion to `cheerpx-trace.txt` (the trace), `standard-linux-trace.txt` (the
known-good reference) and `standard-linux-trace-notes.md`. Produced 2026-08-12
for the `display-bug.md` investigation: this run repeats the standard-trace
exercise **inside the CheerpX VM** so the two traces can be compared to pinpoint
the `tk.Tk()` hang.

## 1. Headline finding

**The CheerpX app hangs inside `tk.Tk()` after making 5,267 X11 calls that are
byte-identical in function-and-argument terms to the working standard run. The
last call is `XAllocColor(black) -> 1` — call #5272, which is EXACTLY call
#5272 of the standard trace. In the working run the very next call is the first
`XStringToKeysym("x")` of Tk's keymap/keysym-table build; in CheerpX the app
never issues it.** The process then spins in userland with no further syscalls
and no further X11 calls (see §5). It never reaches `TRACE_MAINLOOP_BEGIN`.

So the CheerpX↔Tk hang is a **userland spin in Tcl/Tk C code at the boundary
between Tk's colour/GC setup and its keymap (keysym-table) initialization** —
after `XAllocColor(black)` (default cursor colour?) and before
`TkpInitKeymapInfo`'s first `XStringToKeysym("x")`.

## 2. Environment (deliberately the same guest as the standard run)

| Item | Value |
|---|---|
| Runtime | **CheerpX 1.3.7** (repo-pinned `@leaningtech/cheerpx`, served same-origin from `webvm/cheerpx/`) |
| Guest kernel | `Linux 4.15.0-54-cheerpx (i386)` (virtual kernel; `mount()` = `Function not implemented`) |
| Guest userland | Alpine 3.17.10, i386, musl — the SAME ext2 the standard run used its userspace from |
| python3 / tkinter | 3.10.15-r0 / 3.10.11-r0 (Tk 8.6.12-r1 / Tcl 8.6.12-r1) |
| X server | the guest's own **Xorg 21.1.8-r0** (modesetting on the CheerpX DRM), display **:0**, 1344x900x24 |
| libx11 / libxft / fontconfig | 1.8.7-r0 / 2.3.7-r0 / 2.14.1-r0 |
| Desktop | Xorg + i3 as root+user (`desktop.start`); the i3 autostart runs `/trace/trace-run.sh` INSTEAD of `idle3.10` |
| App | `/trace/example.py` == `python-tkinter/example.py` (marker included, byte-identical) |

The boot console shows udev starting (`Populating /dev`, `Waiting for uevents`)
— the `/.dockerenv` fix is in effect; there is no `login:` prompt and no boot
hang. The boot log also shows `mount: ... Function not implemented` for
`/proc`/`/run`/`/dev` (CheerpX provides these virtually).

## 3. Capture method (no ptrace in CheerpX)

CheerpX does not implement ptrace (verified in the plan), so strace is
impossible. The same two LD_PRELOAD loggers as the standard run were baked into
the guest and run from the i3 autostart:

- **syscall trace** (`syscall-logger.c`): a libc syscall-wrapper interposer
  (musl `open`/`read`/`connect`/`poll`/…). It is the libc-level analog of the
  standard run's `qemu -strace`. Lines: `SYS<TAB><ts><TAB><pid> <func>(args) = ret`.
  Allocator internals (mmap/munmap/madvise/brk) are intentionally omitted.
- **X11 call trace** (`xcall-logger.c`): the SAME Xlib interposer as the
  standard run (dlopen+dlsym resolution, all entry points incl. XIM varargs).
  Lines: `X11<TAB><entry_ts><TAB><func>(args) -> ret`, sorted by timestamp.
- **Channel**: the loggers' stderr is redirected to `/dev/console`; the page's
  xterm write() is monkey-patched (plan §4 pattern) to accumulate every byte.

Both sections were cut at the console stall (the app's hang), not at the
marker — the marker was never printed.

### Solo vs parallel (and why)

`trace-run.sh` has a baked mode (`/trace/run-mode`: `both` | `syscall` | `x11`):
- `both` (default): the two loggers run IN PARALLEL. This is necessary because
  the hang cannot be killed from inside the guest (§4), so a sequential script
  would stall forever on the first run and never start the second.
- `x11` / `syscall` (solo): single-logger boots used for the CLEAN traces in
  `cheerpx-trace.txt`.

A parallel X11 run sees the other app's Tk registration and is contaminated:
the root window already has `InterpRegistry`, and Tk registers itself as
`"tk #2"` (extra `XGetWindowProperty(TK_APPLICATION)` call, `data="{tk #2}"`).
The parallel run hung slightly earlier (right after the `TK_APPLICATION`
write). The solo X11 run is the faithful comparison, and it is the one shipped
in `cheerpx-trace.txt`. The parallel syscall trace was identical in count and
hang point to the solo one, so a single syscall capture sufficed.

## 4. The hang is unkillable from inside the guest

- `timeout -s KILL 300` did NOT terminate the hung app (no `SIGKILL` delivery to
  the spun-up WASM thread); the guest-side shell then blocks forever.
- A Python watchdog thread (`os._exit` after N seconds) also failed: threads
  DO work in the guest (`TRACE-THREADS-OK` printed), but the main thread's hang
  is a tight userland spin that **starves the cooperative scheduler**, so the
  watchdog never runs.
- The only reliable bound is **page-side**: the Playwright capture waits for the
  console to stop growing (~60 s) and then closes the browser, tearing the VM
  down.

## 5. Where the traces diverge (the diagnosis)

Both traces were aligned on `(function, normalized arguments)` sequences:

- **X11 level**: the CheerpX solo trace is IDENTICAL to the standard trace for
  **5,267 of 5,273 calls** (the only differences in the identical prefix are
  server-local values: window ids, atom values, pointers). Then:
  - last CheerpX call: `XAllocColor(d, cmap, {red=0,green=0,blue=0, pixel=0x0}) -> 1`
  - standard call #5273 onwards: `XStringToKeysym(string="x") -> 0x78`,
    `XStringToKeysym(string="F20")`, `XStringToKeysym("X")`, `XStringToKeysym("c")`,
    `XStringToKeysym("F16")`, … — Tk's keymap/keysym-table build
    (`TkpInitKeymapInfo`, tk/unix/tkUnixKey.c).
  - The CheerpX app makes **no further X11 calls and no further syscalls** — a
    pure userland spin between the colour/GC setup and the keysym build.
- **syscall level** (libc): the last syscalls are Tcl's auto-load reads,
  `open("/usr/lib/tclIndex")`, `open("/usr/lib/tcl8.6/tclIndex")`,
  `fcntl(F_SETFD)`, `lseek(0, 0, 1)` (lseek on STDIN), then silence. In the
  standard run the same tclIndex reads happen in the same region and the app
  continues; here it stops.

Corroborating detail: the CheerpX X11 trace contains the XOpenIM keysym-table
loop (5,211 `XStringToKeysym` calls right after `XOpenIM`) — so libX11's
`XStringToKeysym` itself works under CheerpX; the spin is in Tcl/Tk code at the
*boundary*, not inside `XStringToKeysym`.

## 6. Other observed differences (not the hang)

1. **`RESOURCE_MANAGER` on the root window**: the guest root carries a 76-byte
   RESOURCE_MANAGER property (the guest's `~/.xinitrc` runs `xrdb`); the
   standard Xvfb root had none (`format=0 nitems=0`). Harmless.
2. **XOpenIM keysym-loop count** is emulator-dependent: 5,211 (CheerpX) vs
   5,462 (qemu 7.2 user-mode) vs 62,849 (Docker Desktop linux/386 qemu) — see
   standard-linux-trace-notes.md §7.3. Treat as a magnitude, not a constant.
3. **No fontconfig font scan** in the CheerpX syscall trace: the app hangs
   before Tk's font initialization, which is where the standard run scans
   `/usr/share/fonts/encodings`. (The guest's Xorg/i3 already did the fontconfig
   work at boot, so the trace app would not re-scan everything even if it got
   further — fontconfig caches.)
4. **No allocator churn** (by design): the libc logger omits mmap/munmap/brk, so
   the syscall section is ~870 lines vs ~11,900 raw syscalls for the standard.
5. The guest X connection is to **`:0`** via `AF_UNIX "/tmp/.X11-unix/X0"`
   (`connect(...AF_UNIX(abstract)"/tmp/.X11-unix/X0", len=20) = 0`, immediate
   success — no refused-first-attempt as in the qemu-user run).
6. `lseek(0, 0, 1)` on stdin appears at the very end of the syscall trace —
   Tcl probing stdin. Present in the standard trace too, earlier.

## 7. What this means for the display bug

- The display/input bug itself is already fixed (the desktop boots, X runs,
  udev works). The remaining E2E blocker is purely **this Tk hang**.
- The hang is not a CheerpX X-server/protocol failure: the app's X11 call
  stream is identical to a working system for 5,267 calls, including
  `XOpenDisplay`, the XIM keysym build, window/property/colour/GC setup. The
  divergence is a userland spin inside Tcl/Tk C code right before the keymap
  table build.
- Suspect areas for the CheerpX-side bug (further debugging): Tcl/Tk code
  between the default-cursor colour allocation and `TkpInitKeymapInfo` — e.g.
  Tk's resource/option handling, `Tk_InitFonts`, or a loop whose exit condition
  depends on something CheerpX's libc/locale does differently. The `lseek(0)`
  on stdin and the RESOURCE_MANAGER reads are the closest observed activity.
- A practical E2E workaround (already suggested in the plan) remains: assert on
  a non-Tk X client (e.g. xterm) until CheerpX's Tk support is fixed.

## 8. Reproducing

1. `diskimage/` carries the trace tooling: `trace/{example.py,syscall-logger.so,
   xcall-logger.so,trace-run.sh,run-mode}`; the i3 config autostarts
   `/trace/trace-run.sh` instead of `idle3.10`. Set `/trace/run-mode` to
   `syscall`/`x11`/`both` and rebuild (`./build.sh browser` + frontend + server).
   `build.sh`'s fingerprint input now includes `diskimage/trace/` (added here),
   so the browser's IndexedDB cache key changes with the trace content.
2. Boot `/alpine.html` in Playwright Chromium with the `tests/e2e/capture-trace.mjs`
   console write-capture (mirrors `term.write`); wait for `TRACE-RUN-START`, the
   section markers, then the console stall; the VM is torn down page-side.
3. Split the captured console on the `SYS\t`/`X11\t` prefixes and sort by entry
   timestamp. For the solo runs, the shipped files were captured with
   `--syscall-only` / `--x11-only`.

## 9. Caveats

- The shipped sections are from SOLO-mode boots (clean); the parallel (`both`)
  capture is contaminated by the second app's Tk registration (`"#2"`).
- No per-process correlation between the syscall and X11 timestamps (separate
  boots, separate monotonic clocks). Align them on the call SEQUENCE, as in §5.
- The hang position varies by a few calls between runs (parallel App B hung at
  the `TK_APPLICATION` write; solo hung at `XAllocColor(black)`; App A's
  syscalls stopped after the tclIndex reads). The XOpenIM-loop magnitude and
  the exact stall point are timing/emulator-dependent; the call sequence up to
  the hang is deterministic.

## 10. Follow-up: where the spin is (entry-loggers + tclsh probe)

Three more trace levels, all on 2026-08-12, pin the hang inside Tcl C
internals:

1. **X11 entry logger** (`xcall-logger.c` built with `-DXCALL_LOG_ENTRY`; every
   interposed call logs `X11<TAB><ts><TAB><func>(...) ENTERED` BEFORE the real
   call, so a hung call shows as a dangling entry line). Result: **all 5,273
   interposed Xlib calls were entered AND returned** — including the final
   `XAllocColor(black)`. No X11 function is entered after it. The spin is
   between X11 calls, not inside Xlib (rules out the XStringToKeysym/XIM-
   inside theories).
2. **libtcl/libtk entry logger** (`tcl-logger.c`, same entry-only pattern,
   interposing Tcl_Eval*/Tcl_PkgRequireEx/Tcl_GetCommandFromObj/Tk_Init/...).
   Result: the last line is
   `Tcl_FSOpenFileChannel(interp=…, path=/usr/lib/tcl8.6/tclIndex, mode="r") ENTERED`
   — the app entered Tcl's file-channel-open during the **Tcl package
   auto-load** and never returned. The preceding `/usr/lib/tclIndex` open
   (ENOENT) *completed*; the successful open (fd 4, `fcntl(F_SETFD)`) then hung
   with the file never read. The spin is in the success path of
   `TclpFSOpenFileChannel`/`Tcl_CreateChannel`.
3. **`tclsh` probe** (`tclsh /trace/probe.tcl` printing `TCLSH-OK`, under the
   tcl-logger): bare Tcl — **no Tk, no X, no Python** — also hangs: it sources
   `init.tcl`, opens/reads the probe script, then spins on its first command
   lookup (`Tcl_FindCommand(name="puts")` never returns). `TCLSH-OK` is never
   printed.

Conclusion: **the CheerpX↔Tk hang is a Tcl-under-CheerpX defect, independent
of the Tk/X layer.** The exact spinning function varies between runs (command
lookup in `tclsh`, channel-open in the tkinter run), pointing at a systemic
issue in Tcl's C internals under CheerpX 1.3.7 (allocator/hash/channel
machinery) rather than a single function. Both hang states show no further
syscalls — consistent with an allocator spin too, since the libc logger
omits mmap/munmap/brk. Minimal upstream repro: `tclsh -c 'puts hi'`.
