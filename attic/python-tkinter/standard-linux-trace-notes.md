# Standard-Linux Tk startup trace — notes

Companion to `standard-linux-trace.txt` (the trace itself) and
`xcall-logger.c` (the LD_PRELOAD X11 logger). Produced 2026-08-12 for the
`display-bug.md` investigation: this is the canonical "known correct" record
of a working `tk.Tk()` startup, to be diffed against the same example run
inside the CheerpX VM.

## 1. Purpose

`python-tkinter/example.py` hangs inside `Tk()` in the CheerpX VM
(`plans/display-bug.md` §"Tk hang deep-dive": `tk.Tk()` never returns, even in
the reference WebVM image). We need a reference for what a *working* Tk
startup does — at the syscall level and at the X11-function level — so the
CheerpX run can be compared call-by-call. This file records that reference and
everything learned while producing it.

## 2. Environment (why this one)

| Item | Value |
|---|---|
| Guest userland | Alpine Linux 3.17.10, i686 (ELF32/i386), musl libc |
| python3 | 3.10.15-r0 |
| python3-tkinter | 3.10.11-r0 (Tk 8.6.12-r1 / Tcl 8.6.12-r1) |
| X server | Xvfb 21.1.8-r0 — **the same xorg-server version as the guest's Xorg** |
| fontconfig / freetype | 2.14.1-r0 / 2.12.1-r0 |
| libx11 / libxft / libdrm | 1.8.7-r0 / 2.3.7-r0 / 2.4.114-r0 |
| Screen | `:99`, 1344x900x24, `-nolisten tcp -ac` (matches the guest canvas size) |
| Execution | i386 userland run under **qemu-i386 user-mode 7.2.22** (linux-user) inside a Docker Desktop arm64 Linux VM; guest `uname`: `Linux 6.12.76-linuxkit ... i686` |
| App | `/trace/example.py` (see §4) |

The guest image of this repo is built from the *same* `docker.io/i386/alpine:3.17`
base with the *same* package set (see `diskimage/Dockerfile`), so the userspace
here is the exact one the CheerpX guest runs — only the execution engine
differs (qemu-user now, CheerpX later). Xvfb is used instead of the guest's
Xorg/modesetting because a framebuffer server is the closest "standard" stand-in;
Xlib client calls do not depend on the server beyond screen size/depth/extensions.

**Why qemu-user instead of a native VM?** A real VM (qemu-system-i386 TCG) was
built and booted, but the app segfaults inside `libfontconfig` during Tk's
Xft/font init under TCG (see §8.1). The identical rootfs runs correctly under
qemu-user. qemu-user is also a closer analog to the later CheerpX run (both are
emulated i386 userlands), making the comparison more meaningful.

## 3. Capture methods and how to reproduce

Syscall trace — qemu-user's built-in tracer (no ptrace needed, so it also works
in emulated environments; note the CheerpX VM has no ptrace at all):

```
qemu-i386-static -L <rootfs> -strace -D syscalls.raw \
  -E DISPLAY=:99 -E HOME=/root -E LC_ALL=C -E PATH=/usr/bin:/bin \
  <rootfs>/usr/bin/python3 /trace/example.py
```

X11-call trace — LD_PRELOAD shim (source kept at `python-tkinter/xcall-logger.c`,
built for i386 musl as `xcall-logger.so`):

```
qemu-i386-static -L <rootfs> \
  -E DISPLAY=:99 -E HOME=/root -E LC_ALL=C -E PATH=/usr/bin:/bin \
  -E LD_PRELOAD=/trace/xcall-logger.so \
  <rootfs>/usr/bin/python3 /trace/example.py 2> xcalls.raw
```

`Xvfb :99 -screen 0 1344x900x24 -nolisten tcp -ac` must be running; its
`/tmp/.X11-unix` is shared into the capture environment at the *literal* path
`/tmp/.X11-unix` because qemu-user does not path-translate AF_UNIX socket
connects (§8.3).

Run until the marker appears, then kill (the app otherwise spins in mainloop).
Both raw files were then cut after the marker (§4) and, for the X11 section,
sorted by the entry timestamp (the logger prints at call *return*; the leading
timestamp is the *entry* time, so sorting recovers true call order).

## 4. The app and the marker

`example.py` is unchanged from the repo version except for a marker block:

```python
window.update()
print("TRACE_MAINLOOP_BEGIN", file=sys.stderr, flush=True)  # stderr write, 21 bytes
with open("/tmp/TRACE_MAINLOOP_BEGIN", "w") as m: m.write("x")
window.mainloop()
```

- The marker is emitted immediately before `mainloop()` — everything before it
  is exactly "up to the mainloop begins" (Tk() constructor, Label, pack, update()).
- Syscall cut: at `open("/tmp/TRACE_MAINLOOP_BEGIN", ...)` (qemu -strace does
  not decode `write(2)` buffers, so the marker *file* is the greppable cut
  point; the 21-byte `write(2)` of the marker print appears just before it).
- X11 cut: at the `TRACE_MAINLOOP_BEGIN` line in the logger's stderr stream.
- The marker file also serves the later CheerpX run, where the console-capture
  will show the stderr print.

Keep `example.py` byte-identical for the CheerpX run so the traces line up.

## 5. What the trace shows — syscall level (Section 1)

11,911 syscalls (44 distinct names; ~10k matched by the name pattern). Notable:

- **Process start**: `set_thread_area`, `set_tid_address`, `brk`, then musl
  loader probing `open("/etc/ld-musl-i386.path")`, `/lib`, `/usr/local/lib`,
  `/usr/lib` for `libpython3.10.so.1.0`; the whole process is dominated by
  musl's mmap/munmap/madvise churn (`mmap2` 2099, `munmap` 1881, `madvise`
  451) — expected for a Python interpreter starting up.
- **X connection**: `socket(PF_UNIX, SOCK_STREAM)` →
  `connect(..., 21-byte sockaddr)` → `ECONNREFUSED` → close → new socket →
  `connect(..., 110-byte sockaddr)` → success. The first attempt uses a compact
  (non-NUL-terminated) `sockaddr_un`; the retry passes the full 110-byte
  structure and succeeds. Then `getpeername`, `uname`, `access("/root/.Xauthority")`
  (absent), and the X socket is set `O_NONBLOCK` before the request/reply
  traffic starts (`poll`, `recvmsg`, `sendmsg`/`writev`, `read`).
- **fontconfig init**: reads `/etc/fonts/fonts.conf`, scans `/etc/fonts/conf.d`,
  then scans font dirs. In `/usr/share/fonts/encodings/*` it emits, **for every
  .enc.gz file**, seven macOS-metadata probe `open`s (`._f`, `%f`,
  `.AppleDouble/f`, `f/..namedfork/rsrc`, `f/rsrc`, `resource.frk/f`,
  `.resource/f`) before opening the file itself — this is stock fontconfig
  behaviour in this build, and it dominates the syscall count.
- The guest CWD is the qemu mount point (`/host/rootfs/...`), so the guest's
  `realpath`/`getcwd` produce a long tail of `readlink` calls (412) walking the
  tree upward — a qemu-user artifact, not app behaviour.
- No fork/exec happens after process start (Python+Tk are single-process here);
  the app never execve's another guest binary.

## 6. What the trace shows — X11 level (Section 2)

5,839 calls, 64 distinct functions, ~0.61 s. Canonical startup phases:

| Phase | First call | Notes |
|---|---|---|
| Threads | `XInitThreads()` | called by Tcl/Tk before anything else |
| Connect | `XOpenDisplay(":99")` | returns the `Display*` |
| (inside XOpenDisplay) | `XSetErrorHandler`, `XSetIOErrorHandler`, `XCreateGC`, `XSynchronize`, `XQueryExtension("XKEYBOARD")`, `XSetLocaleModifiers("")` | libX11-internal calls captured because Alpine's libX11 is not `-Bsymbolic` |
| XIM setup | `XOpenIM` | internally runs the **XStringToKeysym table build** (5,462 calls here; see §7.3), then `XSetIMValues`/`XGetIMValues` (2 va-words each) and `XRegisterIMInstantiateCallback` |
| Interp/Tk window | `XCreateWindow(parent=0x42(root), 1x1, mask=0x2a10)`, `XInternAtom` (`Comm`, `InterpRegistry`, `TK_APPLICATION`) | |
| Toplevel | `XCreateWindow` x2 (mask 0x681a / 0x2a18), `XChangeProperty` (`WM_CLASS`, `WM_NAME`, `_NET_WM_NAME`, `WM_HINTS`, `WM_PROTOCOLS`, `WM_NORMAL_HINTS`, `_NET_WM_STATE`), `XStoreName("tk")`, `XSetWMHints`, `XSetWMNormalHints`, `XResizeWindow`, `XMapWindow` | `XMapWindow` marks the toplevel mapped |
| Label child | `XCreateWindow(parent=0x20000d, 88x19)`, `XMapWindow` | |
| Region/GC/colour | `XCreateRegion`/`XClipBox`/`XSubtractRegion`/`XUnionRectWithRegion`/`XDestroyRegion` (125 XUnionRectWithRegion), `XCreateGC`, `XAllocColor`/`XParseColor` (`#d9d9d9`, black, `#ececec`, `#a3a3a3`), `XCreatePixmap` | expose/clear handling |
| Fonts | `XLoadFont("fixed")` | **Tk uses CORE fonts**; no `Xft*`/`XCreateFontSet` calls in the pre-mainloop section even though RENDER was queried — Alpine Tk does not take the Xft path here |
| update() | `XEventsQueued`, `XNextEvent` x18, `XFilterEvent` x18 (PropertyNotify/ConfigureNotify/MapNotify/Expose), `XSync`, `XFlush` | `window.update()` drains the event queue |

Interpreter events (`XFilterEvent`, `XNextEvent`) return with the event type
resolved to a name, e.g. `event={type=12(Expose)}`.

Atom *values* (`0x27` = WM_NAME, `0xe7` = InterpRegistry, …) are server-local
and **will differ between X servers** (including the later CheerpX run); the
atom *names* logged alongside them are the stable comparison key.

## 7. Caveats and environment-dependence

1. **Determinism**: two cold-cache runs are byte-identical in the X11 section
   and near-identical in the syscall section (diffs are only wall-clock values
   in `clock_gettime64`, random fontconfig `.TMP-XXXX` names, and 2-3
   `recvmsg`/`poll` lines where a non-blocking X read happens to return
   `EAGAIN` instead of data).
2. **fontconfig cache state** changes the syscall trace a lot: a *warm* cache
   run skips the encodings-directory scan (~7k fewer syscalls). The delivered
   trace is a *cold-cache* run (full scan). Clear `/var/cache/fontconfig`
   before reproducing.
3. **The XOpenIM XStringToKeysym loop count is emulator-dependent**: 5,462
   calls under qemu 7.2.22, but **62,849** under Docker Desktop's linux/386
   qemu — same app, same X server, different qemu build. Both runs are
   internally consistent and reach the marker. Expect the CheerpX count to
   differ again; treat it as a magnitude to compare, not an exact constant.
4. **The refused first connect** (§5) is Xlib transport behaviour seen here
   (compact vs full `sockaddr_un`); the successful retry is the canonical
   connect.
5. **The guest CWD artifact** (`/host/...` readlink tail) disappears if the app
   is launched from a guest-native CWD.
6. X11 section lines are *sorted by entry timestamp*; the raw logger output is
   in call-completion order. Both orders are derivable from the file.

## 8. Pitfalls found while producing this (useful for the CheerpX session)

1. **qemu-system-i386 (TCG) crashes the app in libfontconfig** during Tk's
   Xft/font init (`lock incl` writing to a freed heap object, right after
   `XQueryExtension("RENDER")`). Same rootfs works under qemu-user and in the
   i386 container; pure `fc-list`/`import tkinter`/`fc-cache` work in the VM —
   only the Tk-window+font path crashes. Don't burn time on it: use qemu-user
   for the reference.
2. **ptrace/strace do not work in any qemu-emulated userland** (linux-user
   returns `ENOSYS` for `PTRACE_*`). This is also why strace can never be used
   inside the CheerpX guest. qemu-user's own `-strace` is the replacement.
3. **qemu-user path translation is inconsistent**: `-L` is applied to most
   file syscalls (`open`, `access`, …) but NOT to `statx` or AF_UNIX
   socket paths, and NOT to the initial executable path. Consequences seen:
   - guest `execve` of a guest binary resolves on the *host* (so Xvfb's
     `xkbcomp` subprocess failed; the traced app never execs, so this was
     worked around by running Xvfb in a separate i386 container and sharing
     its socket).
   - fontconfig's `statx` of config/font dirs hit the host paths → fixed by
     symlinking `/etc/fonts`, `/usr/share/fonts`, `/usr/share/fontconfig`,
     `/usr/share/X11`, `/var/cache/fontconfig`, `/var/lib/fontconfig` to the
     rootfs inside the capture container.
4. **The X server must be reachable at the literal `/tmp/.X11-unix/X99`** in
   the qemu process's own namespace (socket connects are not translated), so
   the shared Xvfb socket dir is bind-mounted there.
5. **RTLD_NEXT fails inside Tk processes** (from the plan's earlier session):
   the logger resolves real symbols with `dlopen("libX11.so.6")`+`dlsym`, never
   `RTLD_NEXT`.
6. **Xlib varargs functions (`XCreateIC`, `XGetIMValues`, `XSetIMValues`,
   `XGetICValues`, `XSetICValues`, `XVaCreateNestedList`) must be forwarded
   verbatim** — truncating the varargs list makes libX11's *internal* calls
   (which also route through the interposers because libX11 is not
   `-Bsymbolic`) dereference garbage. The logger captures and re-passes the
   va-list (i386: pointer-sized stack slots).
7. **Atom names require a live Display**: `XGetAtomName` is called via the
   real pointer only; some property encodings are logged as raw values when no
   Display is in scope.

## 9. Checklist for the later CheerpX comparison

When re-running this under CheerpX, align on:

1. Same `example.py` (marker included), same `xcall-logger.so`, same DISPLAY
   size 1344x900x24, `HOME=/root`, `LC_ALL=C`.
2. Confirm the marker appears (i.e. `mainloop()` is reached) — if `tk.Tk()`
   hangs, the trace will stop inside `XOpenIM`/window-creation, and the diff
   will show exactly where.
3. Compare the **X11 function sequence** first (it is deterministic): expect
   `XInitThreads → XOpenDisplay → XOpenIM → XCreateWindow → XMapWindow → XSync`
   in that shape; atom *names* and function call order are the stable keys.
4. Compare syscall-level milestones: X socket `connect`, `O_NONBLOCK` set,
   Xlib request/reply reads, fontconfig scan, `write(2)` marker.
5. Watch for the XIM keysym-loop magnitude and the fontconfig scan — both are
   legitimate places where an emulated environment may legitimately differ.
