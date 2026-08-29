# Future Feature Ideas

Ideas that are **not part of the authoritative implementation plan**
(`plans/webvm_implementation.md`) but are worth tracking. Each entry is a
self-contained proposal; implement only when the need is real and the approach
is validated against the current CheerpX version (see the plan's §12/21
version-dependence checklist — verify before pinning).

## Clipboard bridging between the browser and the guest (2026-08-13)

**Status: host→guest paste IMPLEMENTED (2026-08-28) — see
`plans/clipboard-paste.md`.** guest→host copy is still open.

**Shipped design (what was built, in one paragraph):** the sidebar Clipboard
panel sends the text as a `CXCLIP <len> <base64>` frame over the console tty;
the guest `paste-typer.sh`
(`diskimage/rootfs/usr/local/bin/paste-typer.sh`, launched by
`desktop.start`) types it into the X-input-focus window via the **XTEST
extension directly** (`XTestFakeKeyEvent` through the tiny `xsendkeys`
backend, compiled in the Dockerfile `xsendkeys-build` stage) and
answers `CXACK <len>`. The page refuses anything that cannot be typed out as
keys (non-ASCII, control chars) with a diagnostic, and the panel supports
Open file… / drag-and-drop plus a length/typing-time warning. **xdotool was
explicitly rejected and is BANNED** (it breaks this image completely —
AGENTS.md): the XTEST calls are made directly, and the one real gotcha found
during validation is that the X server drops every FakeInput after the first
unless the client `XSync`s once per character.

**Why the page itself cannot inject X keys (verified against CheerpX 1.3.8):**
the runtime's X key path is driven by the capture textarea's VALUE — only
real keystrokes produce EV_KEY, synthetic events yield zero. A guest-side X
selection owner (xsel `--input`) traps the CheerpX core. The console tty
input channel (V1) works and XTEST fake input works cleanly when the target
window holds the X input focus.

**Still open (higher effort): guest→host copy.** That needs an X11 selection
manager + a bridge to the browser clipboard (read the X PRIMARY/CLIPBOARD
selection and call `navigator.clipboard.writeText()`), which is a larger
feature; the current design deliberately never touches the host clipboard
API in either direction.
