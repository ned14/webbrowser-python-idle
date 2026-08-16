# Future Feature Ideas

Ideas that are **not part of the authoritative implementation plan**
(`plans/webvm_implementation.md`) but are worth tracking. Each entry is a
self-contained proposal; implement only when the need is real and the approach
is validated against the current CheerpX version (see the plan's §12/21
version-dependence checklist — verify before pinning).

## Clipboard bridging between the browser and the guest (2026-08-13)

**Status:** not implemented. Today there is **no** copy/paste path between the
host browser and the guest desktop, nor any host→guest paste.

**Current behavior (verified by tracing the codebase):**
- `webvm/src/lib/WebVM.svelte` renders a CheerpX KMS `<canvas id="display">`
  with **no** `paste`/`onpaste` handler; the only `navigator.clipboard` use is
  `webvm/src/lib/network.js` (copies the Tailscale IP for the sidebar button),
  unrelated to the VM.
- The self-hosted CheerpX runtime (`webvm/cheerpx/`, pinned 1.3.7,
  `cxbridge.js`/`cx.esm.js`) has **no** clipboard/paste handling and exposes no
  `/dev/clipboard` device.
- The guest image (`diskimage/Dockerfile`) installs **no** `xclip`/`xsel`/
  `xdotool`; the CheerpX `mounts` config has no clipboard device.

Result: host→guest Ctrl+V does nothing (the canvas isn't editable and the
runtime doesn't capture paste); guest→host copy doesn't reach the browser. Only
**guest-internal** X11 selections work (Tk/xterm use the X PRIMARY selection),
which never reaches the browser.

**Proposed design (host→guest paste only, minimal scope):**
1. Host: add a `paste` listener on the `#display` canvas
   (`webvm/src/lib/WebVM.svelte`), read `navigator.clipboard.readText()`.
2. Guest: install `xdotool` (`diskimage/Dockerfile` apk add) and ship a tiny
   helper the host invokes (via the CheerpX `WebDevice`/`DataDevice` or the
   console path) to run `xdotool type --delay 1 "<text>"` on the focused X
   window — making paste work into IDLE, xterm, the file explorer, etc.
3. Caveats: keyboard layout / special chars (quotes, newlines) must be escaped
   for `xdotool type`; only targets the currently-focused window; needs the
   display canvas to actually hold focus for the paste event.

**Not covered (higher effort):** guest→host copy. That needs an X11 selection
manager + a bridge to the browser clipboard (e.g., read the X PRIMARY/CLIPBOARD
selection and call `navigator.clipboard.writeText()`), which is a larger
feature.

**Verification before building:** confirm CheerpX 1.3.7 exposes any
clipboard/input API (it exposes none in this runtime's glue today), and confirm
`xdotool` works under the patched X stack (see `plans/display-bug.md`). Update
the plan's §12/21 checklist if any version-dependent claim changes.

## Tk file viewer for images, text and Markdown (2026-08-16)

**Status: IMPLEMENTED 2026-08-16** (see the research section below and
`plans/webvm_implementation.md` §12/21). `diskimage/scripts/file-viewer.py`
is in the guest image with `py3-pillow` + `py3-mistune`; the file manager's
"Open" routes every non-Python file to it (IDLE-style full-screen swap, kept
alive by the keep-alive daemon like IDLE); the in-guest Xvfb suites and the
rootfs smoke all pass (`==> rootfs smoke PASS` on `browser` and `none`).

**Verification results (2026-08-16, in-guest under Xvfb):**
- `PIL.ImageTk` + `_imagingtk` work under the patched `libtcl8.6.so` —
  exercised by the image-view test (JPEG-class rendering, zoom, animated
  GIF via `ImageSequence`).
- **This Tk 8.6 build has NO `wm class` subcommand** — WM_CLASS cannot be
  set; the explorer watcher detects the viewer by its `<name> — Viewer`
  title instead.
- `ImageOps.exif_transpose()` returns a COPY that loses `is_animated`/
  `n_frames` — the viewer captures animation info from the opener first.
- mistune 2.0.4 streaming-renderer API cannot emit directly (children render
  before parents) — the viewer walks the `AstRenderer` AST instead.
- The viewer's thread→main handoff uses `after()` from the load thread,
  which requires a running `mainloop()` (tkinter raises "main thread is not
  in main loop" under bare `update()` pumping — tests run on a real
  mainloop).

**Current behavior (verified by tracing the codebase):**
- `diskimage/scripts/file-explorer.py` routes every non-Python "Open" through
  `_open_externally()` (`file-explorer.py:705`): `xdg-open` isn't installed in
  the guest, `.py` files go to IDLE, and text files (`.txt`, `.md`, `.log`,
  `.csv`, `.json`, …) open as `xterm -e less/vi` (`file-explorer.py:720-724`).
- Everything else — images (`.png`, `.jpg`, `.gif`, …), unknown binaries —
  hits `messagebox.showerror("Error", "No opener available for …")`
  (`file-explorer.py:759`). There is no image viewer in the guest image.
- Pillow is **not** installed in the guest image (not in `diskimage/Dockerfile`).

**Constraint (why Tk):** the viewer must be implemented in Tk. Tk is already
debugged and working under the patched X stack (see `plans/display-bug.md`),
whereas GTK caused compatibility problems and the GTK file managers were
removed from the image (`plans/webvm_implementation.md` §12, 2026-08-14 entry).
No GTK or other toolkit dependencies.

**Proposed design (minimal scope):**
1. Add a new `diskimage/scripts/file-viewer.py` — a pure-Tk app (frameless or a
   simple `Toplevel`), opened on top of the file manager like IDLE is today:
   - **Images:** render any Pillow-supported format (`.png`, `.jpg`/`.jpeg`,
     `.gif`, `.bmp`, `.webp`, `.tiff`) by loading with Pillow and displaying
     via `ImageTk.PhotoImage` (from `PIL.ImageTk`); fit to window, keep aspect
     ratio, scrollbars/zoom; animated GIFs if cheap to add.
   - **Text:** read the file into a read-only `tk.Text` widget with scrollbars
     and monospace font; cap the size for huge files.
   - **Markdown:** ideally render basic formatting (headings, bold/italic,
     lists, code blocks) as styled text in a `tk.Text` widget via a small
     hand-rolled renderer — no external markdown library needed.
   - Extend `_open_externally()` in `file-explorer.py` to launch the viewer
     (`subprocess.Popen([viewer_script, path])`) for image extensions and
     text/Markdown files, before the xterm/less fallback.
2. Add Pillow to the guest image (`diskimage/Dockerfile` apk add, e.g.
   `py3-pillow`), verify it runs under the guest's Python 3.10.

**Not covered:** PDFs (needs a PDF rendering lib — out of scope), audio/video
playback, editing (view-only).

**Verification before building:** confirm the Alpine Pillow package matches the
guest Python version and that `ImageTk.PhotoImage` renders correctly under the
patched X stack; confirm the viewer opens and returns to the file manager
cleanly (same keep-alive pattern as IDLE, `file-explorer.py:801-833`). Update
the plan's §12/21 checklist if any version-dependent claim changes.

## Tk file viewer — implementation research (2026-08-16)

Research for the entry above. **Verified facts first** (checked against
pkgs.alpinelinux.org and PyPI on 2026-08-16):

- **`py3-pillow` 9.3.0-r0 exists in Alpine v3.17 community for x86 (i386)** and
  ships both `PIL/ImageTk.py` and the compiled
  `PIL/_imagingtk.cpython-310-i386-linux-gnu.so` — so
  `ImageTk.PhotoImage` works from the apk package alone, no pip. 2.6 MiB
  installed. Deps (all in v3.17): libjpeg-turbo, libtiff, libwebp, openjpeg,
  lcms2, libimagequant, freetype, zlib, py3-olefile.
- **`py3-mistune` 2.0.4-r0 exists in Alpine v3.17 community for x86** — pure
  Python CommonMark parser, 268 KiB installed, BSD-3-Clause.
- **Tk 8.6 `PhotoImage` natively reads only PPM/PGM, GIF and PNG** — no
  JPEG/WebP/TIFF without a converter.
- **`tkhtmlview` 0.3.2 (PyPI, 2026-02-12) requires Pillow ≥ 11 + requests.**
  Pillow 11 has no i686 wheels (Pillow ≥ 10 dropped 32-bit wheels), so it
  would need a source build on this i386 guest, and it mismatches the
  v3.17 apk Pillow 9.3. The HTML-widget route is therefore the weakest option.

### Images — options

- **I1 (recommended): Pillow + `ImageTk.PhotoImage`.** Full format coverage
  (JPEG, PNG, GIF, WebP, TIFF, BMP, ICO, JPEG 2000…). Only new guest package
  is `py3-pillow`. Decode runs on the emulated i386 CPU, so for large photos
  use `Image.draft()` (JPEG/TIFF fast-decode) then `Image.thumbnail()` to
  display size, and `ImageOps.exif_transpose()` for EXIF rotation. Keep the
  `PhotoImage` reference on the widget (classic Tk GC pitfall).
- **I2: Tk-native `PhotoImage` only.** Zero deps but PNG/GIF/PPM only —
  insufficient for the idea's "at least the common formats" requirement.
  Usable as an unconditional fallback if Pillow is missing.
- **I3 (contingency): Pillow → in-memory PPM/PNG → `tk.PhotoImage(data=…)`.**
  Avoids the `_imagingtk` C extension entirely. Slightly more code than I1;
  keep in back pocket if `ImageTk` misbehaves under the patched
  `libtcl8.6.so` (display-bug.md §2.8) — the compiled extension is exercised
  only by I1.
- Animated GIF: `ImageSequence.Iterator` + `root.after()` frame swap; fits I1
  or I3. Optional for v1.

### Markdown — options

- **M1 (recommended): `py3-mistune` with a custom `Renderer` subclass that
  emits text + tag ranges into a read-only `tk.Text`** (headings, bold/italic,
  code spans, fenced blocks, lists, links, blockquotes; mistune 2 plugins add
  tables/strikethrough/task lists). Robust CommonMark parsing for 268 KiB —
  no HTML pipeline, no styling layer to fight.
- **M2: hand-rolled subset renderer** (~150–250 lines, headings/emphasis/
  code/lists/links/blockquotes/hr) — keeps the desktop client stdlib-only
  (the explorer's current style), but a *subset* parser only; edge cases
  (nested emphasis, escapes) are on us. Fine if keeping deps at zero is the
  priority.
- **M3 (possible, weakest): md → HTML (`markdown`/`markdown2`) →
  `tkhtmlview` widget.** Blocked at the latest version by the Pillow ≥ 11
  requirement; older tkhtmlview (0.3.0/0.3.1) is pip-installable as a pure
  wheel at build time (guest has no runtime internet), but still drags in
  `requests` (only for remote images — useless offline), gives less styling
  control, and is the only option that breaks the "no pip runtime deps" pattern.
- **M4 (out of scope): pandoc / wkhtmltopdf / PyQt / GTK / WebKit** — not
  packaged for i386 Alpine 3.17, or violates the Tk-only constraint.

### Text — options

Read-only `tk.Text` + scrollbar, monospace (DejaVu Sans Mono, already in the
image via font-dejaVu); UTF-8 with latin-1 fallback on decode errors; binary
sniffing reused from the explorer's `_is_text_file()` (`file-explorer.py:727`);
size cap (~2–4 MiB, showing a notice) or lazy chunked insert. No highlighting
in v1. `xterm -e less` stays as the fallback for unrecognized types.

### Integration with the file manager — options

- **J1 (recommended): IDLE-style full-screen swap.** New `open_in_viewer()`
  mirroring `_open_in_idle()` (`file-explorer.py:772`): `Popen([viewer, path])`
  → `root.withdraw()` → watcher thread polls the i3 window tree for the
  viewer's window (viewer sets a distinctive `root.title("… — Viewer")` /
  `wm_class`) → on close, restore + reload the folder (viewer edits text via
  copy only, but folder may have changed). **The keep-alive daemon needs no
  change**: while the viewer is up there is 1 window (healthy); if the viewer
  is stuck windowless, the existing 30 s force-kill path
  (`keep-file-explorer.sh:79-87`) restores the desktop. Optionally add a
  `viewer_running()` guard next to `idle_running()` for symmetry.
- **J2 (minimal): viewer as a second window, explorer stays visible.** No
  watcher/withdraw logic; but i3 tiles both windows on the ~1024×768 canvas
  and multi-open gets cluttered. Only if J1 proves problematic.
- Viewer takes one path argument; multi-select open could open one viewer with
  Prev/Next navigation (nice-to-have, explorer already supports multi-select).

### Packaging

Add `py3-pillow` (and optionally `py3-mistune`) to the existing `apk add` in
`diskimage/Dockerfile:41`; `COPY scripts/file-viewer.py` alongside
`file-explorer.py` (`Dockerfile:145`). Image grows ~2.6 MiB + the shared lib
deps (libjpeg-turbo etc.). No pip installs at build or runtime.

### Recommendation

I1 (Pillow via apk + `ImageTk.PhotoImage`, with I3 as contingency) + M1
(mistune renderer, M2 if the zero-dep rule wins) + plain `tk.Text` for text +
J1 (IDLE-style swap). This keeps every dependency in the v3.17 apk repos, uses
only already-debugged Tk under the patched X stack, and reuses the proven
withdraw/watch/restore pattern.

### Additional verification for §12/21 checklist

- `import PIL.ImageTk` + `PhotoImage` under the patched `libtcl8.6.so` and the
  CheerpX X stack (this exercises `_imagingtk`, a compiled extension not used
  by any current guest app).
- JPEG/WebP decode latency on emulated i386 with `draft()`+`thumbnail()` for a
  multi-megapixel test image.
- Viewer window map/unmap + explorer restore cycle under the patched X stack;
  add an in-guest Xvfb test to the rootfs smoke suite (pattern:
  `file-explorer-tests.py`, generate a PNG with Pillow, open in the viewer,
  assert the i3 tree shows the viewer window, close, assert the explorer
  returns).
