# Vendored Tcl/Tk fork (for the CheerpX Tk-hang workaround)

A local, patchable copy of Tcl 8.6.17 and Tk 8.6.17 — the EXACT versions the
guest ships (`apk` `tcl-8.6.17-r1` / `tk-8.6.17-r1`, Alpine 3.24). The Tcl fix
is applied to `tcl-8.6.17/` and shipped in the guest image
(`diskimage/trace/libtcl8.6.so.patched` overrides `/usr/lib/libtcl8.6.so`);
see `plans/display-bug.md` §2.8. The override library MUST be built from the
exact apk version: the apk `init.tcl` demands `package require -exact Tcl
8.6.17`, and the first attempt built from 8.6.18 sources aborted tkinter on a
version conflict (2026-08-18, plans/update-to-latest.md §9.2.1). The in-guest
Xvfb tkinter suites validate it.

## Layout

- `tcl-8.6.17/` — Tcl 8.6.17 source WITH the CheerpX notifier fix applied
  (`unix/tclUnixNotfy.c` stale-fdset select fix).
- `tk-8.6.17/` — pristine upstream Tk 8.6.17 source (no patches needed).
- `alpine/` — Alpine 3.24 aports build metadata + the patch:
  - `tcl-APKBUILD`, `tk-APKBUILD` — verbatim from `main/tcl`, `main/tk` @
    `3.24-stable` (verified byte-identical to the aports files on
    2026-08-21; the fork uses the stock recipe unchanged).
  - `tcl-notifier-stale-fdset.patch` — OUR CheerpX fix
    (plans/display-bug.md §2.8), applied with `patch -p1` from the
    `tcl-8.6.17/` root (already applied in the committed tree).
  - The 8.6.12-era patches are GONE: the stock aports
    `tcl-stat64.patch` + `restore-fp-control-word.patch` (Alpine 3.24
    builds tcl 8.6.17 with no patches at all) and our
    `tcl-getsockname-guard.patch` (UPSTREAMED in 8.6.17 — verified in the
    sources: `Tcl_MakeFileChannel` has the `fstat`/`S_ISSOCK` guard in
    `unix/tclUnixChan.c`, and `MakeLowPrecisionDouble` in
    `generic/tclStrToD.c` has the volatile-retval FP control-word reset).

## Provenance / fidelity notes

- The fork tracks what the guest image actually contains (verified against
  the ext2's `/lib/apk/db/installed`). Do not bump without a §12/21
  verification pass — Alpine x86 builds with `--disable-64bit`, and the Tcl
  build drops the bundled `pkgs/sqlite3*`.
- The upstream tarballs are NOT committed (they are ~12 MB); the extracted
  trees above are the fork. `tcl8.6.17-src.tar.gz`/`tk8.6.17-src.tar.gz`
  download URLs + sha512sums are in the APKBUILDs for re-extraction.

## Rebuilding

The patched `libtcl8.6.so` is built i386 with Alpine's configure, INSIDE a
`docker.io/i386/alpine:3.24` container (x86 emulation; the aports 3.17-era
recipe cross-built from amd64 with `--build=x86_64-... --host=i586-...`,
which is unnecessary and unusable on arm64 hosts):

```
docker run --rm --platform=linux/i386 -v "$PWD":/src -w /src/tcl-8.6.17 \
    i386/alpine:3.24 sh -c '
        apk add --no-cache gcc musl-dev make zlib-dev sqlite-dev
        patch -p1 -i ../alpine/tcl-notifier-stale-fdset.patch   # already applied in the committed tree
        rm -rf pkgs/sqlite3*
        cd unix
        ./configure --prefix=/usr --sysconfdir=/etc --mandir=/usr/share/man \
            --localstatedir=/var --with-system-sqlite --disable-64bit
        make -j2        # -> libtcl8.6.so
    '
```

Copy the result to `diskimage/trace/libtcl8.6.so.patched`; the guest
Dockerfile overrides the apk library with it.

## Why the fork exists

The fix makes Tcl run correctly under CheerpX:
1. `tclUnixChan.c` — only call `getsockname()` on real sockets
   (`fstat`/`S_ISSOCK` guard); CheerpX's `getsockname()` hangs on non-sockets,
   which froze Tcl's standard-channel setup and IDLE's startup. **UPSTREAMED
   in 8.6.17** — no longer our patch; kept here as context.
2. `tclUnixNotfy.c` — honour `select()`'s return value by clearing the fd
   sets on a <= 0 return (STILL OUR PATCH in 8.6.17); CheerpX leaves them
   populated, so the notifier falsely reported the X socket readable and
   `window.update()` spun forever.

The full E2E desktop test (`tests/e2e/tests/desktop.spec.js`) passes with
these fixes: IDLE boots, renders, and accepts keyboard + mouse input.
