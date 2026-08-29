# Clipboard paste (host → guest) — shipped design (2026-08-28)

Host → guest text paste, "as if typed by keyboard". The CheerpX runtime
implements no `/dev/clipboard`, the page cannot inject X keys, and a guest X
selection owner traps the core (see `plans/future-feature-ideas.md` for the
full rejected-alternatives rationale). What ships is the one lane proven
clean: the guest types the text itself via **XTEST**.

## Design

```
Clipboard panel (PasteTab.svelte)
   │  type / browser-paste / Open file… / drag-drop
   ▼
WebVM.svelte  — validate typability + length (refuse with a diagnostic),
   │           send  CXCLIP <len> <base64>\n  over the console input tty
   ▼
guest console (stdin of paste-typer.sh, raw mode)
   ▼
paste-typer.sh + xsendkeys — XTestFakeKeyEvent (XTEST) into the
   │              X-input-focus window; XSync after EVERY command (required:
   │              the X server drops every FakeInput after the first
   │              otherwise — verified under Xvfb 2026-08-28)
   ▼
CXACK <len> / CXFAIL <reason> on the console → WebVM.svelte releases the
single in-flight throttle and updates the panel status
```

### Page side (`webvm/src/lib/WebVM.svelte`, `PasteTab.svelte`, `clipboard.js`)

- Panel: textarea + Paste button + status line; **Open file…** link (hidden
  `<input type="file">`, read as text) and **drag-and-drop onto the box**;
  live **length warning** ("1,234 chars — ~12s to type") above 300 chars at
  the guest's ~100 chars/s typing rate, hard cap 10 000. The note is
  computed by a reactive statement (`$: pasteLengthNote =
  lengthNote($pasteText.length)`) — a bare `lengthNote()` call in the
  template would not track the store read inside the function body and the
  warning would never update on typing or file-open (fixed 2026-08-29;
  regression test "length warning updates immediately while typing and
  after opening a file").
- The panel's draft text lives in a STORE (`clipboard.js` `pasteText`), so
  closing/reopening the panel never resets content.
- **File-picker guard (2026-08-29):** the sidebar auto-closes on hover-away
  (400 ms), and moving the mouse over to the native file dialog fires that
  mouseleave — closing the panel mid-pick and dropping the chosen file.
  `PasteTab` sets `filePickerActive` before opening the picker and clears
  it ONLY when the picker is KNOWN closed: the input's `change`/`cancel`,
  or the first page `pointerdown` (native pickers are modal, so a page
  pointer event proves the dialog closed — covers browsers without
  `cancel`). Deliberately NOT a timer (expires while the user finds the
  file — v1 bug) and NOT window `focus` (Chrome fires `focus` while the
  dialog is still open — v2 bug, cleared the guard during the wait and the
  later mouseleave closed the panel; reported 2026-08-29, fixed by the
  event-driven reset). `SideBar.hideInfo()` suppresses the auto-close while
  set. Regression tests: `tests/e2e/tests/paste.spec.js` — "Open file…
  does not close the paste pane…" (short dwell), "file dialog open + mouse
  moved away for a long dwell keeps the panel open…" (1.5 s dwell THEN the
  mouseleave), and "a window focus event while the file dialog is open
  must not clear the picker guard…" (simulates the Chrome focus churn with
  a dispatched `focus` event while the dialog is open, then the
  mouseleave); all assert the panel stays open and the content survives,
  and that a genuine hover-away still closes it afterwards.
- `pasteUntypableReason()`: printable ASCII + `\n \t \b` only — anything else
  (non-ASCII, control chars) is refused page-side with the offending
  character named; nothing is sent.
- Single in-flight paste; the ack timeout scales with length (`5000 + len*20`
  ms) and is released early by CXACK/CXFAIL scanned off the console stream in
  `writeData` (lines reassembled across chunks; only CX-prefixed fragments are
  held back).

### Guest side (`diskimage/rootfs/usr/local/bin/paste-typer.sh` + `diskimage/xsendkeys.c`)

- Implemented in SHELL (2026-08-29): `paste-typer.sh` owns the framing,
  the ASCII gate and the US-keymap char → keysym-name translation (an awk
  program fed by `od`); `xsendkeys` (a tiny C binary compiled in the
  Dockerfile `xsendkeys-build` stage, XTestFakeKeyEvent through libXtst)
  is the XTEST backend. They talk over a FIFO the script holds open
  (`exec 9<>`), so there is NO spawn per paste — xsendkeys starts once, at
  boot. Per-paste applets are base64/od/awk/wc (tiny busybox). xte from
  xautomation was considered and rejected: the package is not in Alpine.
- Launched by `desktop.start` after the X socket exists, with stdin/stdout
  on `/dev/console` and `DISPLAY=:0`; the tty is put in raw mode (`stty
  raw -echo`) so large frames are not canonicalized or echoed.
- Parses `CXCLIP <len> <base64>` frames; second gate on typability (`CXFAIL
  untypable`), oversize (`CXFAIL toolarge`). Trailing newlines survive
  because the decode/translate is a pipe, not a command substitution.
- Types via `xsendkeys`: per-char press/release with Shift_L held for
  uppercase/symbols per the US keymap (the guest is setxkbmap us),
  `\n \t \b` → Return/Tab/BackSpace keysyms, `usleep 10000` pacing
  (~100 chars/s). **`XSync` after EVERY xsendkeys command is mandatory**
  (verified under Xvfb 2026-08-28: without a round-trip the server
  processes only the first FakeInput and drops the rest).

## Files

- `webvm/src/lib/PasteTab.svelte`, `webvm/src/lib/clipboard.js`,
  `webvm/src/lib/WebVM.svelte` (paste block + CXACK scanner)
- `diskimage/rootfs/usr/local/bin/paste-typer.sh` (shell daemon),
  `diskimage/xsendkeys.c` + the `xsendkeys-build` Dockerfile stage
- `diskimage/Dockerfile` (`libxtst`), `diskimage/rootfs/etc/local.d/desktop.start`
- Tests: `tests/unit/test_paste_typer.py` (runs the SHELL script with a
  fake xsendkeys backend: protocol, refusals, keysym/shift command stream,
  full printable-ASCII round trip), `tests/rootfs/smoke.sh` (paste into a
  focused Tk Entry under Xvfb, read back in-guest + CXACK),
  `tests/e2e/tests/paste.spec.js` (panel refusal, end-to-end delivery into
  the explorer Search box via canvas pixel signals, Ctrl+V-during-boot,
  file open/drop/length warning, file-picker guard regressions)

## Known limitations (accepted)

- Paste lands in the **focused guest window** (same as any VM/remote desktop).
- **ASCII-only** — enforced by refusal, never typed wrong.
- Slow for long text (~100 chars/s) — the panel warns; 10 000 char cap.
- **No guest → host copy** (open idea in `plans/future-feature-ideas.md`).
- The page never touches `navigator.clipboard`; in-VM Ctrl+C/V stay native.
