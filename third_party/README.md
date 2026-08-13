# Vendored Tcl/Tk fork (for the CheerpX Tk-hang workaround)

A local, patchable copy of Tcl 8.6.12 and Tk 8.6.12 — the exact versions the
guest ships (`apk` `tcl-8.6.12-r1`, `tk-8.6.12-r1`, Alpine 3.17). The Tcl
fixes are APPLIED to `tcl-8.6.12/` and shipped in the guest image
(`diskimage/trace/libtcl8.6.so.patched` overrides `/usr/lib/libtcl8.6.so`);
see `plans/display-bug.md` §2.8.

## Layout

- `tcl-8.6.12/` — Tcl 8.6.12 source WITH the two CheerpX fixes applied
  (`unix/tclUnixChan.c` getsockname guard, `unix/tclUnixNotfy.c` stale-fdset
  select fix).
- `tk-8.6.12/` — pristine upstream Tk 8.6.12 source (no patches needed).
- `alpine/` — Alpine 3.17 aports build metadata + all patches:
  - `tcl-APKBUILD`, `tk-APKBUILD` (from `main/tcl`, `main/tk` @
    `3.17-stable`; `pkgrel=1`).
  - `tcl-stat64.patch`, `restore-fp-control-word.patch` — the two stock
    Alpine Tcl patches (Tk ships with none).
  - `tcl-getsockname-guard.patch`, `tcl-notifier-stale-fdset.patch` — OUR
    CheerpX fixes (plans/display-bug.md §2.8). All four apply cleanly with
    `patch -p1` from the `tcl-8.6.12/` root.

## Provenance / fidelity notes

- Versions are pinned to what the guest image actually contains (verified
  against the ext2's `/lib/apk/db/installed`). Do not bump without a §12/21
  verification pass — Alpine 3.17 builds with `--disable-64bit` on i386, and
  the Tcl build drops the bundled `pkgs/sqlite3*`.
- The upstream tarballs are NOT committed (they are ~15 MB); the extracted
  trees above are the fork. `tcl8.6.12-src.tar.gz`/`tk8.6.12-src.tar.gz`
  download URLs are in the APKBUILDs for re-extraction.

## Rebuilding

The patched `libtcl8.6.so` is built i386 with Alpine's exact configure:

```
gcc/musl-dev/make/zlib-dev/sqlite-dev/patch  (Alpine 3.17)
cd tcl8.6.12
for p in tcl-stat64.patch restore-fp-control-word.patch \
         tcl-getsockname-guard.patch tcl-notifier-stale-fdset.patch; do
    patch -p1 -i ../alpine/$p
done
rm -r pkgs/sqlite3*
cd unix
./configure --build=x86_64-alpine-linux-musl --host=i586-alpine-linux-musl \
    --prefix=/usr --sysconfdir=/etc --mandir=/usr/share/man \
    --localstatedir=/var --with-system-sqlite --disable-64bit
make -j2        # -> libtcl8.6.so
```

Copy the result to `diskimage/trace/libtcl8.6.so.patched`; the guest
Dockerfile overrides the apk library with it.

## Why the fork exists

The two fixes (§2.8) make Tcl run correctly under CheerpX:
1. `tclUnixChan.c` — only call `getsockname()` on real sockets
   (`fstat`/`S_ISSOCK` guard); CheerpX's `getsockname()` hangs on non-sockets,
   which froze Tcl's standard-channel setup and IDLE's startup.
2. `tclUnixNotfy.c` — honour `select()`'s return value by clearing the fd
   sets on a <= 0 return; CheerpX leaves them populated, so the notifier
   falsely reported the X socket readable and `window.update()` spun forever.

The full E2E desktop test (`tests/e2e/tests/desktop.spec.js`) passes with
these fixes: IDLE boots, renders, and accepts keyboard + mouse input.

