# Display bug: the graphical desktop never rendered to the canvas

**Status:** the display bug is **FIXED**. A separate, root-caused **Tcl-under-CheerpX
hang** still prevents IDLE/Tk from running, which blocks the `desktop.spec.js`
E2E guarantee (re-scope recommended below).

## 1. Display bug — root cause and fix (DONE)

**Symptom:** the guest booted, X started, i3 ran, input reached the guest — but
the canvas showed only the static 40 px X cursor and never updated.

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
logs `XINPUT: Adding extended input device "VirtualPS/2 VMware VMMouse"` +
keyboard, the cursor moves on mouse input, and the canvas updates.

Bisection trail (all ruled out on the way): VT/launch shape, LightDM, gettys,
the resize hook, delayed udev trigger, `xorg.conf.d`, and the X stack versions
(the reference image runs the identical Alpine 3.17.10 + xorg-server 21.1.8-r0
and renders). Signature in the boot console: a working boot shows
`Starting udev` / `Populating /dev` / `Waiting for uevents`; the broken one
went from `Can't continue` straight to the default runlevel (OpenRC banner
suffixed `[DOCKER]`).

## 2. Remaining issue — Tk hangs, root-caused to Tcl-under-CheerpX

`tk.Tk()` hangs inside the CheerpX guest, so IDLE never renders (the reference
image avoids Tk entirely; its desktop is polybar + feh + xterm). This is a
separate CheerpX limitation. **Status: two distinct defects found and one
removed.** The getsockname defect (§2.2) is worked around and `tclsh`
passes; the remaining blocker is a Tcl-notifier event-drain storm in
`window.update()` (§2.7), with the keysym/library init already completing —
so the path to IDLE is concrete (§2.7 approaches 1-3). History:

- **No ptrace exists in CheerpX**, so tracing uses LD_PRELOAD interposers
  (Xlib, libc, Tcl/Tk) with stderr streamed to `/dev/console` and captured
  page-side (`tests/e2e/capture-trace.mjs`).
- A trace comparison against a working standard-Linux run
  (`python-tkinter/standard-linux-trace.txt` vs `cheerpx-trace.txt`) shows the
  CheerpX X11 call stream is **identical for 5,267 of 5,273 calls**, then the
  app **spins in userland** (no syscalls, no X11 calls).
- An **entry-logging Xlib logger** shows every interposed X11 call was entered
  *and* returned — the spin is between X11 calls, not inside Xlib.
- A **libtcl/libtk entry logger** initially pinned the spin to inside
  `Tcl_FSOpenFileChannel("/usr/lib/tcl8.6/tclIndex")` (the Tcl package
  auto-load; the successful open hung, file never read) — later traced to the
  `getsockname()` non-socket hang (§2.2), fixed by the shim.
- **Bare `tclsh` also hangs** (on its first command lookup,
  `Tcl_FindCommand("puts")`) — the bug is **Tcl-under-CheerpX 1.3.7,
  independent of Tk/X/Python**. The exact spinning function varies between runs
  (command lookup vs channel-open), pointing at a systemic issue in Tcl's C
  internals (allocator/hash/channel machinery).
- **Minimal upstream repro:** `getsockname()` on a non-socket fd hangs under
  CheerpX (§2.2; a 2-line C call, no Tcl needed — supersedes the earlier
  `tclsh -c 'puts hi'` repro). File a report with Leaning Technologies. The
  hang is unkillable from the guest (signals don't deliver; the spin starves
  the cooperative scheduler), so captures are bounded page-side.

## 2.1 Tk filesystem & environment comparison — findings (2026-08-12)

Investigated whether the Tk filesystem/environment in the CheerpX image differs
meaningfully from a standard Linux. Method: extracted `/usr/lib/tcl8.6` and
`/usr/lib/tk8.6` from `webvm/custom-disk-images/webvm-custom-disk.ext2` with
`debugfs`, md5-diffed against a fresh `apk add tcl tk` on `i386/alpine:3.17`,
and compared the syscall patterns in `python-tkinter/{standard,cheerpx}-trace.txt`.

**The filesystem is exonerated — content is byte-identical.** All 307 files
(221 tcl8.6 + 86 tk8.6) md5-match a standard Alpine 3.17 install, including
`libtcl8.6.so`/`libtk8.6.so`, `encoding/*.enc`, `msgs/*.msg`, `init.tcl`,
`auto.tcl`. `tclIndex` is the stock 5539-byte file in both; `/usr/lib/tclIndex`
(top-level) is absent in both (ENOENT is normal in both runs). Symlinks match
(`tclsh`→`tclsh8.6`, `wish`→`wish8.6`; the `.so` files are regular files, mode
555, in both). No missing/corrupt/locally-patched Tcl files.

**The environment differs, and the divergence lands on the guest's
`isatty()`/channel-type probing:**

1. **`ioctl(TIOCGWINSZ)` (isatty) probes: 108 in the standard run, ZERO in the
   CheerpX guest.** Every file open in the standard trace is followed by
   `ioctl(fd,TIOCGWINSZ)` (Python `_io` and Tcl channel open); the guest emits
   no TIOCGWINSZ ioctl at all (its only ioctl is `ioctl(3,FIOCLEX,0)`, which
   proves the ioctl interposer itself works in the guest). So the guest's
   `isatty()` is short-circuited/patched by the CheerpX runtime and never
   reaches the interposed `ioctl`. `getsockname()` std-channel socket probes
   (3 in standard, 0 in guest) behave the same way.
2. **The hang sits exactly at the first isatty probe of Tcl's channel open.**
   Aligned on the call sequence: the standard run's step right after the stdin
   `lseek(0,0,SEEK_CUR)` probe is `ioctl(0,TIOCGWINSZ)` (isatty on stdin);
   the CheerpX run's **last logged syscall is `lseek(0,0,1) = 0` on stdin**
   (`cheerpx-trace.txt` line 934), then silence — a spin where the standard run
   would issue the stdin isatty probe. This is inside
   `Tcl_FSOpenFileChannel("/usr/lib/tcl8.6/tclIndex")`, matching the tcl-logger
   pin (§10 of the trace notes).
3. **`/proc` and `/sys` are virtual in the guest** (`mount()`=ENOSYS). The
   standard run's Tcl does `readlink("/proc/self/exe")` (Tcl_FindExecutable);
   the guest never touches `/proc` — Tcl's executable discovery and
   `tcl_platform` population take a different path.
4. **stdio fds are virtual devices**: stdin is seekable
   (`lseek(0,0,1)=0`), stdout→`/dev/null` (seekable), **stderr→`/dev/console`
   (non-seekable: `lseek(2,0,1) = -1`)** in the guest, vs three seekable
   regular files in the standard run. Tcl probes each std channel for
   seek/tty/socket during channel setup.

**Conclusion/priority:** the FS content is not the differentiator. The trace
comparison pointed at the std-channel probing, but the direct probe (§2.2)
**supersedes the isatty suspicion**: `isatty()` and `ioctl(TIOCGWINSZ)` both
return cleanly in the guest — the defective call is **`getsockname()` on a
non-socket fd**, which spins forever. (The 108-vs-0 TIOCGWINSZ ioctl count
below is an artifact of the traced code paths, not evidence about `isatty()`
itself; the probe is the ground truth.)

## 2.2 Direct-libc probe — the hang is `getsockname()` on a non-socket fd (DONE 2026-08-12)

A standalone C probe (`python-tkinter/probe.c`, baked at
`/trace/probe`, no Tcl/Tk/Python) runs the exact syscall sequence Tcl's
channel-open performs, printing `PROBE\t<name> ENTER` before and `RET` after
each call. It is selectable via `/trace/run-mode` (`probe` runs it under the
syscall-logger, `probe-plain` without; both print `===BEGIN-PROBE===` for
`capture-trace.mjs --probe`).

**Result: `getsockname()` on a NON-SOCKET fd hangs (never returns) under
CheerpX. It is the Tcl/Tk hang, reduced to a 2-line C repro, independent of
Tcl/Tk/X/Python and of the LD_PRELOAD loggers.**

Guest output (`python-tkinter/cheerpx-probe.txt`, plain, no logger):

```
PROBE	=== control: getsockname on a real AF_UNIX socket ===
PROBE	socket(AF_UNIX,SOCK_STREAM) RET=3 err=OK
PROBE	getsockname(socket) RET=0 err=OK family=1        <- sockets fine
PROBE	=== stdin (fd=0) ===
PROBE	stdin:lseek(SEEK_CUR) RET=0 err=OK
PROBE	stdin:isatty() RET=0 err=ENOTTY                   <- isatty fine
PROBE	stdin:ioctl(TIOCGWINSZ) RET=-1 err=ENOTTY         <- ioctl fine
PROBE	stdin:fstat() RET=0 err=OK mode=20777 chr=1 ... rdev=259
PROBE	stdin:getsockname() ENTER                         <- NEVER RETURNS
```

Standard Alpine 3.17 i386 (same binary, `python-tkinter/
standard-linux-probe.txt`): every `getsockname()` on the non-socket std fds
returns immediately `RET=-1 err=ENOTSOCK`; the control socket also works.

Interpretation:

- **fd 0 in the guest is a character device** (`fstat mode=20777 chr=1`,
  `rdev=259` — a /dev/null-style node), while the standard container run's
  stdin is a FIFO (`mode=10600 fifo=1`). Tcl's channel code calls
  `getsockname()` on each standard channel to classify it (file/tty/socket);
  on any real Linux `getsockname()` on a non-socket returns `ENOTSOCK`
  instantly.
- **Under CheerpX 1.3.7, `getsockname()` on a non-socket fd spins forever**
  (no syscall return, no userland resumption — matches the "userland spin,
  no further syscalls, unkillable" signature from §2, and explains why the
  hang site wanders between runs: any `getsockname` on a std fd stalls).
- **Minimal upstream repro (better than `tclsh -c 'puts hi'`):**

  ```c
  int fd = 0; struct sockaddr sa; socklen_t sl = sizeof(sa);
  getsockname(fd, &sa, &sl);   /* hangs under CheerpX; ENOTSOCK on Linux */
  ```

- This supersedes the §2.1 `isatty()` suspicion: `isatty()` and
  `ioctl(TIOCGWINSZ)` both return cleanly in the guest. The defective call is
  `getsockname()` on non-sockets.
- Trace-level corroboration: the §2 syscall trace's last line was the stdin
  `lseek(SEEK_CUR)` probe (`cheerpx-trace.txt:934`); the standard run's very
  next std-channel step is `getsockname(0)` — exactly the call that hangs.

## 2.3 Workaround verification — getsockname shim (DONE 2026-08-12)

An LD_PRELOAD interposer (`diskimage/trace/getsockname-fix.c`, baked at
`/trace/getsockname-fix.so`) returns `ENOTSOCK` immediately for any fd that
`fstat()` shows is not a socket, forwarding genuine sockets to the real
`getsockname()` — behaviourally identical to a correct kernel for Tcl's
channel classification, inert on real Linux. Loaded via
`LD_PRELOAD=/trace/getsockname-fix.so` (run-modes `verify-tclsh`,
`verify-tk`, `verify-tk-sys`; captured with `capture-trace.mjs --verify`).

**Result — the getsockname hang is FIXED, but a SECOND, independent CheerpX
defect remains for the full Tk path. The second defect is NOT a poll/select
readiness lie (earlier hypothesis, now disproven by direct measurement):**

0. **Control run (xterm) — poll is exonerated.** `verify-xterm` runs a real,
   working X11 client (xterm) under the syscall-logger in the guest. It
   starts, connects to the guest X server, and **blocks normally in its event
   loop** (console stalls at ~8.7s guest time). It performs **zero**
   XNoOp/XSync/XEventsQueued calls after init. Its poll/select readiness
   matches a working system: every `poll()=1 [POLLIN]` is followed by actual
   received bytes; `poll()=1 [POLLOUT]` is a normal always-true "writable"
   report. So the user's intuition ("xterm surely polls on the X socket too")
   is correct — and xterm does NOT hang, because poll is honest.
1. **`tclsh` now fully works.** `verify-tclsh` prints `TCLSH-OK` and exits
   RC=0 in the guest (previously hung forever at channel init).
2. **`tk.Tk()` gets past the original hang point.** The tclIndex/auto.tcl/
   tk.tcl/button.tcl/fonts reads that previously stalled now all complete
   (`verify-tk-sys` trace reads `/usr/lib/tk8.6/{tk,icons,button,...}.tcl`).
3. **But `tkinter` still does not reach `mainloop()`.** After the library
   loads, `window.update()` (Tcl_DoOneEvent TCL_DONT_WAIT) enters a **busy
   loop of XSync-style flush-waits**: ~239k `XNoOp`+`XEventsQueued(mode=1)`+
   `XFlush` triples with an always-POLLOUT X socket, driven by the Tcl
   notifier pipe (`select(to=0) ready=[4]` — fd 4/5 is Tcl's notifier pipe,
   `pipe([4,5])` created just before the spin). `XEventsQueued` returns 0
   every time yet the loop re-issues the flush-wait; the `XNoOp` reply never
   completes, so the loop never exits and the `TRACE_MAINLOOP_BEGIN` marker
   never prints. The standard run does the same keysym-table build
   (`XStringToKeysym`, 5,211 calls) with **zero** XNoOp/XSync traffic and
   proceeds. Decoded pollfd data: `poll(fd=Xfd, POLLIN|POLLOUT, -1) = 1
   re=POLLOUT` — the socket is genuinely writable, so the XSync-style wait
   never blocks. This is an **application-level (Tcl/Tk notifier + XSync)
   busy loop**, not a kernel-readiness defect.

So: the §2.2 minimal repro and its workaround are correct and sufficient for
the tclsh hang; the full `tk.Tk()` path is blocked by a **separate,
Tcl/Tk-notifier-specific busy loop** (XSync flush-wait during keymap init
under CheerpX) that the getsockname shim cannot address. xterm/poll evidence
rules out a generic poll/select workaround. Next candidates: figure out why
`XEventsQueued`/`XNoOp` never completes under CheerpX (XIM? PropertyNotify
flood? notifier pipe), or re-scope the desktop to non-Tcl clients (§3).

## 2.4 Blocking-X-socket workaround — TESTED, NEGATIVE (DONE 2026-08-12)

Hypothesis: the spin was Xlib busy-retrying because the X socket was set
`O_NONBLOCK` by Xlib, so its XSync reply-wait never blocked (poll kept
returning `re=POLLOUT` on a genuinely-writable socket, recvmsg kept returning
EAGAIN). Test: `xblock-fix.so` (`python-tkinter/xblock-fix.c`, baked at
`/trace/xblock-fix.so`) identifies the X fd at `connect()` (AF_UNIX path
matching `.X11-`/`X11-unix`), strips `O_NONBLOCK` from its `fcntl(F_SETFL)`,
and neutralizes `ioctl(FIONBIO)` for that fd only. Every other fd passes
through untouched (Tcl's notifier pipe, logger fds keep their semantics).
Run modes `verify-tk-block` (getsockname-fix + xblock-fix) and
`verify-tk-block-sys` (+ syscall-logger).

**Result — negative: blocking the X socket does NOT fix the Tk hang, and it
regresses progress.**

- The shim demonstrably works: the X fd now shows `fcntl(3,F_SETFL,arg=2)`
  (O_NONBLOCK stripped from Xlib's `2050`) and the fd stays blocking.
- But the app stalls **earlier** than without it — before the tclIndex/keysym
  phase (`verify-tk-block-sys`: 750 syscalls, last at ~9.41s guest time vs the
  unshimmed run's thousands before the spin). The X setup handshake completes
  (writev 12 + recv 8/276 + writev 20 + recvmsg 32 + writev 4 [XNoOp] +
  recvmsg 32), then the app blocks with no further syscalls — the busy loop
  became a silent blocking wait, and the hang point moved earlier.
- No `TRACE_MAINLOOP_BEGIN` marker in either blocking run. The original
  XSync-style spin also never returns to the marker.

Interpretation: the X socket's blocking mode is not the lever. The X server
(guest Xorg) is not completing the XSync reply exchange the way the standard
run's does, and blocking mode simply converts the client's busy-retry into a
blocking read. The root cause is upstream of the socket mode — most likely
the X server's handling of the sync request/reply under CheerpX (possibly
XIM/PropertyNotify-driven; `XOpenIM` runs twice right before the spin in
§2.3). Next candidates: disable XIM (`XMODIFIERS=@im=none` / `*useXIM: false`),
or re-scope to non-Tcl clients (xterm is verified working).

## 2.5 XIM-disable workaround — TESTED, NO EFFECT (DONE 2026-08-12)

Hypothesis: the XSync storm (§2.3) was driven by XIM setup (`XOpenIM` runs
twice right before the spin). Test: run-modes `verify-tk-noxim` /
`verify-tk-noxim-sys` / `verify-tk-noxim-x` set BOTH `*useXIM: false` (via
`xrdb -merge /trace/noxim.xresources`) and `XMODIFIERS=@im=none` before
launching, stacked with the getsockname shim and (in `-x`) the X11 entry
logger.

**Result — no effect, and the disable did NOT take hold:**

- `XOpenIM` still runs and still returns a valid IM (`-> 0xafaef110`,
  0xafaef300) — neither mechanism suppressed it (Tk reads the resource under
  its own name/context; `@im=none` did not make Xlib return NULL here).
- The storm is byte-identical to the un-disabled run: ~242k XNoOp + ~242k
  XEventsQueued(mode=1) + ~971k XFlush, interleaved with the 5,462-keysym
  `XStringToKeysym` build, still no `TRACE_MAINLOOP_BEGIN`.

Interpretation: the storm is a **raw XNoOp+XEventsQueued(QueuedAfterFlush)+
XFlush XSync-style round-trip** — i.e., something in the Tcl/Tk notifier
issues a NoOp sync and waits for its reply via `XEventsQueued`; the guest X
server never completes the NoOp round trip, so the wait returns 0 forever and
the loop re-issues. This is insensitive to XIM because XIM was never actually
disabled AND because XIM is not on the loop's critical path. The next
candidate is to make the sync itself short-circuit: interpose
`XNoOp`/`XSync`/`XEventsQueued` so the flush-wait returns immediately (or a
one-shot probe on the guest X server's NoOp reply), before re-scoping to
non-Tcl clients.

## 2.6 Sync short-circuit workaround — TESTED, NEGATIVE (DONE 2026-08-12)

Hypothesis (from §2.5): short-circuiting the XSync-style primitives
(`XNoOp`, `XSync`, `XEventsQueued`) so the flush-wait completes immediately
would let `window.update()`'s `Tcl_DoOneEvent` drain. Implemented
`xsync-fix.so` (`python-tkinter/xsync-fix.c`, baked at `/trace/xsync-fix.so`):
`XNoOp`/`XSync` become no-ops (return 1 without sending/waiting),
`XEventsQueued` passes through. Run modes `verify-tk-sync`/`-sys`/`-x`
(getsockname-fix + xsync-fix [+ loggers]).

**Result — negative: the short-circuit does not stop the loop.**

- The shim works (0 `XNoOp`, 0 `XEventsQueued`, ~2 `XSync` calls in the run),
  but the app still storms: now **2.4M back-to-back `XFlush`** calls, each
  doing `poll(fd=X, POLLIN|POLLOUT, -1)` → `re=POLLOUT` → recvmsg EAGAIN.
  No `TRACE_MAINLOOP_BEGIN`; capture grew to 800MB before stalling.
- The Tcl-side driver is unchanged: `Tcl_DoOneEvent(flags=0x2)` (TCL_DONT_WAIT)
  in a tight loop (226k entries in the `verify-tk-tcl` run) — i.e.
  `window.update()`. With the X sync primitives neutered, the loop's remaining
  X work is `XFlush` + the always-POLLOUT poll + EAGAIN recvmsg.
- Combined with §2.4 (blocking socket) and §2.5 (XIM disable), this closes
  the obvious client-side levers: **blocking mode, XIM disable, and sync
  short-circuit all fail to unblock Tk.** The root cause sits in how the
  guest X server + Tcl's notifier interact under CheerpX, not in any single
  Xlib call the app makes.

**Recommendation (superseded by §2.7):** §2.6 initially recommended
re-scoping the desktop guarantee to non-Tcl clients, but **IDLE is a hard
requirement** — the guest ships `idle3.10` as the primary desktop client and
its autostart must be restored (§8). Tcl/Tk under CheerpX is therefore a
must-fix, and §2.7 lays out the concrete path: the keysym/library init
already completes, so the ONLY blocker is `update()`'s
`Tcl_DoOneEvent(TCL_DONT_WAIT)` drain never converging. The two upstream
repros for the CheerpX report stand: `getsockname()` on a non-socket fd hangs
(§2.2), and the Tk DONT_WAIT event drain storms when the X server fails to
complete sync round-trips under CheerpX.

## 2.7 The Tk event-loop storm, precisely — and the path to IDLE (DONE 2026-08-12)

Two more diagnostic runs pinned the remaining blocker down to a narrow,
attributable mechanism:

- **`verify-tk-tcl`** (getsockname-fix + Tcl entry logger + X11 entry logger +
  syscall logger in one LD_PRELOAD — the symbol sets are disjoint so they
  coexist): the looping function is **`Tcl_DoOneEvent(flags=0x2)`**, entered
  226,662 times. flags=0x2 is `TCL_DONT_WAIT` — i.e. **`window.update()`**
  (the marker is printed after `update()` in `example.py`; `update()` calls
  `Tcl_DoOneEvent(TCL_ALL_EVENTS|TCL_DONT_WAIT)` in a loop until it returns 0,
  and under CheerpX it never returns 0).
- **The Tk keysym/keymap build COMPLETES**: `XStringToKeysym` runs 5,462
  times in the guest vs 5,463 in the standard run — byte-for-byte the same
  keymap table work, finished. The library loads (tclIndex/auto.tcl/tk.tcl)
  also complete. So Tk initialization itself is essentially fine; the ONLY
  blocker to `mainloop()` is `update()`'s DONT_WAIT event drain spinning.
- The spin mechanism (decoded pollfd data): the X socket is genuinely
  writable (`poll(fd=X, POLLIN|POLLOUT, -1) = 1 re=POLLOUT`), so the notifier
  never sees an "empty, quiet" state; each `Tcl_DoOneEvent(TCL_DONT_WAIT)`
  iteration issues XSync-style flush work (XNoOp/XEventsQueued/XFlush) and
  returns "did something", so `update()` loops forever. No
  `TRACE_MAINLOOP_BEGIN` in any run.

**The three client-side levers are now all tested and negative** (§2.4
blocking socket — hang moved earlier; §2.5 XIM disable — no effect, disable
didn't even take; §2.6 XSync/XNoOp/XEventsQueued short-circuit — storm
persists as 2.4M `XFlush`). The remaining defect is in how the guest X
server + Tcl's Unix notifier interact under CheerpX — a `Tcl_DoOneEvent`
DONT_WAIT drain that never converges.

### Remaining approaches to get IDLE up (ranked)

1. **Patch/replace Tcl's notifier so `Tcl_DoOneEvent(TCL_DONT_WAIT)` drains.**
   The loop is inside Tcl/Tk's own event machinery, not Xlib (the entry
   logger shows the app is IN `Tcl_DoOneEvent`; X11 calls are made from it).
   Two concrete sub-approaches:
   a. **Rebuild Tcl/Tk with a source patch** that makes the Unix notifier
      treat a writable-but-idle X socket as "no event" (e.g. mask POLLOUT for
      the X fd, or make the DONT_WAIT path check for real X events before
      returning 1). Heavier (need tcl/tk source in the build) but attacks the
      root.
   b. **LD_PRELOAD `Tcl_DoOneEvent`** to return 0 immediately when called
      with `TCL_DONT_WAIT` and nothing is queued — makes `update()` a no-op
      so the app sails past to `mainloop()`. Risk: `update()` semantics are
      subtly wrong (idle/event processing skipped during Tk startup), but
      `mainloop()` itself is a different flags path and would still run. This
      is the cheapest test.
2. **`window.update()` avoidance at the Python layer.** `example.py` calls
   `update()` before `mainloop()` purely as a trace marker boundary. If the
   autostart script calls `mainloop()` directly (and IDLE itself calls
   `mainloop()` — IDLE does not call `update()` at startup), the DONT_WAIT
   drain may never run and the app could reach the event loop, where
   blocking `mainloop()` behaves like xterm (which works). Worth testing
   even before any shim: point the autostart at a script that does
   `tk.Tk(); mainloop()` with no `update()`.
3. **xterm-style `XNextEvent` blocking in mainloop.** If (2) reaches
   `mainloop()` and it still spins, the DONT_WAIT-drain theory is wrong and
   the problem is in the blocking wait too; then compare `mainloop()`'s
   syscalls to xterm's working `XNextEvent` poll pattern (§2.3 control) to
   find the divergence.
4. **Re-scope the desktop guarantee to non-Tcl clients** (§3) as the
   fallback; keep the §2.2 upstream repro (`getsockname` non-socket hang)
   for the CheerpX report.

Recommended immediate step: **(2) then (1b)** — zero-risk autostart change,
then the `Tcl_DoOneEvent` DONT_WAIT shim if needed. Both are one build
cycle each.

## 2.8 FIXED — Tcl/Tk source patch via the vendored fork (DONE 2026-08-12)

**IDLE now runs under CheerpX.** The full E2E desktop test passes: IDLE
boots, renders (light-pixel ratio ~0.99), keyboard input works, mouse clicks
work. The fix is a source patch to Tcl 8.6.12 built from the vendored fork
(`third_party/tcl-8.6.12` + `third_party/alpine/*.patch`) and shipped as a
replacement `/usr/lib/libtcl8.6.so` in the guest.

**Two root causes, both fixed in Tcl source:**

1. **`getsockname()` on non-socket fds (§2.2)** — `Tcl_MakeFileChannel`
   (`unix/tclUnixChan.c`) probes every fd with `getsockname()` to classify
   sockets; under CheerpX that hangs on stdin/stdout/stderr (non-sockets).
   Fix (`tcl-getsockname-guard.patch`): guard the probe with
   `fstat() + S_ISSOCK()` so `getsockname()` is only ever called on real
   sockets. Behavior-identical on a correct kernel; removes the hang.
2. **The `window.update()` / `Tcl_DoOneEvent(TCL_DONT_WAIT)` storm (§2.7)** —
   Tcl's Unix notifier relied on `select()` clearing the fd sets on return.
   CheerpX's `select()` returns 0 (nothing ready) yet leaves the sets
   populated (trace: `select(...) = 0 ready=[3,4]`), so the notifier's
   ready-scan reported the X socket readable every poll, Tk's `DisplayFileProc`
   ran its `XNoOp+XFlush` connection probe in a tight loop, and `update()`
   never drained. Fix (`tcl-notifier-stale-fdset.patch`): zero the fd sets
   when `select()` returns <= 0 in both the non-threaded wait path and the
   threaded `NotifierThreadProc`, mirroring the kernel contract.

**Build:** patched `libtcl8.6.so` built i386 with Alpine's exact configure
(`--build=x86_64-alpine-linux-musl --host=i586-alpine-linux-musl
--disable-64bit --with-system-sqlite`, all 4 patches applied:
tcl-stat64, restore-fp-control-word, + the 2 above). Wired into the guest via
`diskimage/Dockerfile` (COPY + override of `/usr/lib/libtcl8.6.so` after
`apk add`). The `idle3.10` i3 autostart is restored.

**Test delta:** `desktop.spec.js`'s boot-hang assertion was fixed — the old
`not.toMatch(/Starting local\s*$/m)` regex matched the bare `" * Starting
local ..."` line that OpenRC always leaves (its `[ ok ]` status is appended
via ANSI cursor-up, rendered on a separate xterm row). The corrected
assertion checks the boot actually progressed: `trimEnd()` not ending in
`Starting local ...` AND `"launching the X desktop session"` present.

**Final piece — IDLE's shell subprocess (DONE 2026-08-12):** IDLE by default
runs the Python shell in a subprocess over a 127.0.0.1 TCP loopback socket.
Under CheerpX with no tailnet controlUrl there is no loopback networking, so
the bind fails and IDLE degrades (warning dialog + broken menus). Fixed with a
conditional launcher (`diskimage/rootfs/usr/local/bin/idle3.10-launcher`,
wired into the i3 autostart): it probes loopback bindability with a fast
`python3 socket.bind()` and applies IDLE's `-n` (in-process, no subprocess)
ONLY when the bind fails. **`-n` is intentionally NOT applied when networking
is enabled** (tailnet controlUrl set, samba/webdav/any net-capable guest) —
IDLE then runs normally with its subprocess.

## 2.9 PARTIAL — the GTK3 file manager (pcmanfm) desktop (DONE 2026-08-13)

> **SUPERSEDED (2026-08-14):** pcmanfm/spacefm and every §2.9 shim
> (setsockopt-fix.so, the instrumented libfm, the instrumented pcmanfm, the
> `/trace` diagnostics, the `/proc/self/mountinfo` stub) are removed from the
> image. The desktop client is now the stdlib-only Tk **file explorer**
> (`diskimage/scripts/file-explorer.py`), which never touches GTK/GIO/dconf —
> the whole deadlock class this section documents is gone with it (see
> `plans/webvm_implementation.md` §12/25). The Tcl/Tk `libtcl8.6.so.patched`
> fix (§2.8) remains, as the explorer and IDLE are both Tk apps.

**Status (historical): pcmanfm now boots and maps its window a large fraction of the time
(was: never — black canvas). A flaky, timing-dependent startup race remains
(see below).** The desktop client was switched from IDLE to the file manager
(pcmanfm, GTK3 + libfm), per the product plan. pcmanfm's startup under CheerpX
was a sequence of deadlocks, found one by one:

### 2.9.1 `setsockopt(SO_REUSEADDR)` fatal exit — FIXED (shim)

pcmanfm's single-instance setup (`single-inst.c` `single_inst_init`) calls
`setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, ...)` on a fresh AF_UNIX socket and
treats ANY failure as fatal (`if(ret || bind(...) == -1) return
SINGLE_INST_ERROR;` → `main()` exits 1). CheerpX rejects that call on AF_UNIX,
so pcmanfm exited 1 before mapping any window; the keep-alive relaunched it
forever → black desktop. **Fix: `diskimage/trace/setsockopt-fix.so`**
(LD_PRELOAD interposer) returns success for a failed `SO_REUSEADDR` — a no-op
hint on AF_UNIX. Preloaded into pcmanfm at every launch site (i3 autostart,
keybinding, keep-file-manager).

### 2.9.2 Deadlock inside `g_file_monitor_directory` — FIXED (custom libfm)

With the setsockopt shim, pcmanfm stalled (no syscalls; futex) inside libfm's
`fm_monitor_directory` — the GIO `g_file_monitor_directory` call — while the
main thread held libfm's global `hash` lock. **In isolation the same call
works** (probe-verified with/without GTK, with/without a held mutex,
inotify on/off), but in the full app it hangs. GIO file watching is a
non-essential convenience. **Fix: custom libfm 1.3.2 built from source**
(`diskimage/trace/libfm.so.4.1.3.instrumented`), with `fm_monitor_directory`
returning libfm's existing **dummy monitor** (no events) for every path —
`G_FILE_MONITOR_*` GIO call completely bypassed. Folder views no longer
auto-refresh (F5 reloads).

### 2.9.3 Deadlock in GIO filesystem-info queries — FIXED (custom libfm)

After the monitor fix, pcmanfm stalled while reading the mount table
(`/etc/mtab`, `/proc/self/mountinfo`) inside
`g_file_query_filesystem_info`. Two call sites were the culprits:
- `_fm_file_info_set_from_native_file` — sync `FILESYSTEM_READONLY` query for
  every directory file-info (removed; directories assumed writable).
- `fm_folder_query_filesystem_info` — async `FILESYSTEM_SIZE/FREE` query
  (disabled; the status bar shows no free-space figure).
The read-only flag and free-space display are cosmetic; removed in the custom
libfm. `statvfs` itself works under CheerpX (probe-verified) — the hang was in
the mount-table machinery around it, not the syscall.

### 2.9.4 Remaining: flaky startup race (NOT yet fixed)

pcmanfm now boots and fills the canvas a good fraction of the time, but a
timing-dependent deadlock remains in `fm_main_win_init` / early
`pcmanfm_run`: it sometimes stalls (futex, no syscalls) right after GTK icon
theme loading, before the main window maps — in production (no at-spi, no
dconf via `GSETTINGS_BACKEND=memory`) with NO worker thread visibly active.
`pkill -9` cannot recover it (CheerpX signal delivery to a futex-stuck process
fails, as in the §2.7 Tk hang). Suspects investigated and ruled out: inotify
GSource (`inotify-off.so` forced GIO polling — no effect), atk-bridge
(`NO_AT_BRIDGE=1` — no effect), dconf/GDBusWorker
(`GSETTINGS_BACKEND=memory` — no effect), the thread-pool dir-list job
(running it synchronously — no effect), volume monitor (`fm-places-model`
skips `g_volume_monitor_get()` — not reached at the stall point). The
`keep-file-manager.sh` now force-kills and relaunches a windowless pcmanfm
after 30 s as a self-heal (helps only when the stall is killable).

**Repo state:** custom libfm (`libfm.so.4.1.3` + `libfm-gtk3.so.4.1.3`
built from libfm 1.3.2 source with the §2.9.2/2.9.3 fixes and LIBFM-MARKER
instrumentation) and custom pcmanfm (`pcmanfm.instrumented`, from 1.3.2 source
with PCMANFM-MARKER instrumentation) override the apk binaries via the
Dockerfile. Remove the marker instrumentation for a clean production build
(the fixes themselves are in the same source). Diagnostic probes shipped in
`diskimage/trace/`: `setsockopt-fix.so` (fix), `inotify-off.so`,
`inotify-probe`, `glib-probe`, `gtk-hello`, `gtksync-probe`, `icon-probe`,
`gtkmonitor-probe`, `statvfs-probe`, `fmgtk-probe` (dlsym-based, needs
exported symbols so unbuilt), `trace-run.sh` verify-* modes. `run-mode`
default is `both`.

## 3. E2E implication

**CLOSED — IDLE works.** `tests/e2e/tests/desktop.spec.js` passes in the
guest: no login prompt, no boot hang, IDLE's window fills the canvas
(light-pixel ratio ~0.99 > 0.35), keyboard input renders results, and mouse
clicks open menus. The Tcl-under-CheerpX fix is the source patch in §2.8
(soldered into the vendored fork + guest image); the LD_PRELOAD shims
(getsockname-fix/xblock/xsync) remain only as diagnostic scaffolding and are
NOT needed for IDLE.

## 4. Working desktop (do not regress)

- No console `login:` prompt: gettys disabled in
  `diskimage/rootfs/etc/inittab`.
- X launched as **root** (Xorg.wrap refuses non-root, non-console users) by
  `desktop.start`: `Xorg :0 -nolisten tcp -noreset -novtswitch -logfile
  /var/log/xorg.log` (VT-less — `startx`'s `-keeptty` VT ioctls hang in the
  VT-less guest); `/tmp/.X11-unix` (1777) created first; the user session runs
  as `user` via `~/.xinitrc` under `dbus-run-session -- i3`.
- Canvas raised above the console overlay: `raiseDisplay()` in
  `webvm/src/lib/WebVM.svelte` (must run before `initCheerpX()`, which never
  returns).
- Non-destructive resize hook (`99-screen-resize.sh`: `xrandr --auto` poll,
  never `--off`) and `10-cheerpx.conf` (ShadowFB software rendering).

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
fixed waits. **Tracing** the Tk hang: see `tests/e2e/capture-trace.mjs` and the
run-mode-based autostart (`diskimage/trace/trace-run.sh`, `run-mode` =
`both`/`syscall`/`x11`/`x11-entry`/`tcl`/`tclsh`).

## 6. Diagnosis tooling — hard-won quirks

- **Guest → page console:** a guest process must write to **`/dev/console`**
  explicitly; stderr/stdout are otherwise lost. Console capture requires
  monkey-patching `term.write` from an init script (the xterm buffer is
  unreliable) — the working pattern is in `tests/e2e/capture-trace.mjs`.
- **No ptrace in CheerpX** (strace impossible): syscalls are captured at libc
  level via `syscall-logger.c`; X11 via `xcall-logger.c`; Tcl/Tk via
  `tcl-logger.c`. Real symbols resolve via `dlopen`+`dlsym`, never `RTLD_NEXT`
  (fails inside the Tk process).
- **Guest file reads from JS do not work** (cheerpOS read globals throw); the
  only read channel is the console.
- The **hang is unkillable from the guest** (no signal delivery; the spin
  starves threads): bound captures by detecting the console stall and tearing
  the VM down page-side.

## 7. Trace artifacts

The diagnostic sources live in `diskimage/trace/`; the captured trace/verify
outputs (`.txt`/`.md`) from the original investigation were removed with the
`/trace` cleanup (the diagnosis is complete and superseded, §2.9). Surviving
artifacts:
- `diskimage/trace/probe.c` — the direct-libc probe that pinned the hang to
  `getsockname()` on non-socket fds (§2.2).
- `diskimage/trace/getsockname-fix.c` — the workaround shim (§2.3).
- `diskimage/trace/xblock-fix.c` — the blocking-X-socket shim, tested and
  negative (§2.4).
- `diskimage/trace/xsync-fix.c` — the sync short-circuit shim, tested and
  negative (§2.6).
- `diskimage/trace/syscall-logger.c` — the libc-interposer syscall logger; the
  capture script is `tests/e2e/capture-trace.mjs`.

## 8. Current repo state

- `third_party/` — vendored Tcl/Tk 8.6.12 fork. The two Tcl fixes (§2.8)
  are APPLIED to `tcl-8.6.12/` and saved as `alpine/tcl-getsockname-guard.patch`
  and `alpine/tcl-notifier-stale-fdset.patch` (plus the 2 stock Alpine
  patches and both APKBUILDs). See `third_party/README.md`.
- `diskimage/trace/libtcl8.6.so.patched` — the built patched library;
  `diskimage/Dockerfile` overrides `/usr/lib/libtcl8.6.so` with it after
  `apk add`.
- `diskimage/config/i3/config` autostarts **`/usr/local/bin/open-file-explorer.sh`**
  (the stdlib Tk **file explorer** on the user's home; the launcher guards
  against a second instance) plus **`/usr/local/bin/keep-file-explorer.sh`**
  (relaunches it whenever the last window closes). IDLE is launched on demand
  from the explorer — *Open with IDLE* / double-click a `.py` launches
  **`/usr/local/bin/idle3.10-launcher`** and **withdraws the explorer** for the
  duration (it reappears, listing refreshed, when IDLE exits).
- `diskimage/rootfs/usr/local/bin/idle3.10-launcher` — conditional IDLE
  launcher (probes `socket.bind("127.0.0.1")`; `-n` iff bind fails).
- `build.sh`'s content fingerprint includes `diskimage/trace/`.
- `reference_images/alpine_20251007.ext2` (1.5 GB) kept for re-inspection;
  safe to gitignore.

## 9. Cross-references

- **Do NOT change the xorg-server/Alpine versions for this bug**: the reference
  image runs the identical stack and renders — version bumps are disproven.
- If you change the guest base or xorg-server, update
  `plans/webvm_implementation.md` §12/8 (pinned versions) and re-verify its
  §12/21 checklist.
