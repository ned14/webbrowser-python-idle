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
   window — making paste work into IDLE, xterm, pcmanfm, etc.
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
