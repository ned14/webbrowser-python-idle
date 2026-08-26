#!/usr/bin/env python3
"""Self-contained test suite for file-viewer.py.

Usage:  python3 file-viewer-tests.py    (needs a display; Xvfb works)

The suite loads file-viewer.py as source (without running main) and drives
the FileViewer class directly: each @test function takes `done`, schedules
steps, and calls `done()` when finished. messagebox is captured (never
blocks). All tests share ONE FileViewer root (Tk allows a single root per
mainloop); the destructive tests (the viewer closes itself on binary/broken
input) record the destroy call instead of executing it, so the shared root
survives and the thread→main after() handoffs keep working (they require a
running mainloop).

Adding a new test:

    @test
    def test_something(done):
        _open([TXT])                    # point the shared root at fixtures
        check("text shown", root._text is not None)
        done()
"""

import os
import sys
import tempfile
import time
from types import SimpleNamespace

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file-viewer.py")

with open(APP) as f:
    src = f.read()
_ns = {"__name__": "file_viewer", "__file__": APP}
exec(compile(src, APP, "exec"), _ns)

FileViewer = _ns["FileViewer"]
# Pillow loads lazily in the viewer now; resolve it for real so the image
# tests run exactly when the shipped viewer would use Pillow.
HAVE_PILLOW = _ns["_ensure_pillow"]()
if HAVE_PILLOW:
    from PIL import Image

# ---- framework -------------------------------------------------------------
TESTS = []
FAILURES = []

def test(fn):
    TESTS.append(fn)
    return fn

def check(name, ok, detail=""):
    if detail:
        detail = "-> %s" % (detail,)
    print(("PASS" if ok else "FAIL"), name, detail, flush=True)
    if not ok:
        FAILURES.append(name)

def _wait_until(root, cond, done, timeout_ms=15000, poll=100):
    waited = [0]
    def go():
        try:
            ok = cond()
        except Exception:
            ok = False
        if ok:
            done()
        elif waited[0] >= timeout_ms:
            check("condition timed out", False)
            done()
        else:
            waited[0] += poll
            root.after(poll, go)
    go()

# ---- messagebox captured (never blocks) -------------------------------------
_MSGBOX = {"errors": []}
def _fake_showerror(title, message):
    _MSGBOX["errors"].append(message)
_ns["messagebox"].showerror = _fake_showerror

# ---- fixtures ---------------------------------------------------------------
_FIX = tempfile.mkdtemp(prefix="file-viewer-test-")

def _write(name, content):
    p = os.path.join(_FIX, name)
    with open(p, "wb" if isinstance(content, bytes) else "w") as f:
        f.write(content)
    return p

TXT = _write("note.txt", "Hello, viewer!\nSecond line.\n")
MD = _write("doc.md",
            "# Title\n\nSome **bold**, *italic* and `code`.\n\n"
            "- one\n- two\n\n```py\nprint(1)\n```\n\n"
            "> quoted\n\n---\n\n[link](https://example.invalid)\n")
BIG_TXT = _write("big.txt", "x" * 70000)  # multi-screen, exercises scrolling
BIN = _write("data.bin", b"\x00\x01\x02\xff\x00binary")
BAD = _write("broken.png", b"\x00\x01\x02 definitely not a png")
FAKE_PNG_TEXT = _write("fake.png", "this is text, not an image")
# ~200 KB of markdown: exercises the async render path (threaded mistune
# parse + batched inserts) — the old synchronous path froze the window blank.
BIG_MD = _write("big.md",
                "# Big document\n\n" +
                ("A paragraph with **bold**, *italic* and `code` words.\n\n" * 4000))
if HAVE_PILLOW:
    PNG = os.path.join(_FIX, "photo.png")
    Image.new("RGB", (64, 48), (200, 30, 30)).save(PNG)
    BIG = os.path.join(_FIX, "big.png")
    Image.new("RGB", (2048, 2048), (10, 10, 10)).save(BIG)
    GIF = os.path.join(_FIX, "anim.gif")
    frames = [Image.new("RGB", (32, 32), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(GIF, save_all=True, append_images=frames[1:],
                   duration=100, loop=0)
else:
    PNG = GIF = BIG = None

# Shared root for the non-destructive tests (empty path list: the initial
# _show(0) no-ops). Destructive tests create their own instance.
root = FileViewer([])

def _open(paths):
    root._paths = list(paths)
    root._show(0)

# ---- tests ------------------------------------------------------------------

@test
def test_text_view(done):
    _open([TXT])
    check("text widget created", root._text is not None)
    check("text content shown",
          root._text.get("1.0", "end").startswith("Hello, viewer!"),
          repr(root._text.get("1.0", "end")[:40]))
    check("text status", "Text" in root._status.get(), root._status.get())
    done()

@test
def test_markdown_view(done):
    _open([MD])
    # The mistune parse runs on a worker thread now: wait for the render.
    def ready():
        return root._text is not None and bool(root._text.tag_ranges("h1"))
    def step1():
        check("markdown mode", root._mode == "markdown", root._mode)
        check("h1 heading tag applied", bool(root._text.tag_ranges("h1")))
        check("bold tag applied", bool(root._text.tag_ranges("b")))
        check("italic tag applied", bool(root._text.tag_ranges("i")))
        check("code tag applied", bool(root._text.tag_ranges("code")))
        check("codeblock tag applied", bool(root._text.tag_ranges("codeblock")))
        check("quote tag applied", bool(root._text.tag_ranges("quote")))
        check("content includes title", "Title" in root._text.get("1.0", "end"))
        check("content includes link",
              "link" in root._text.get("1.0", "end") or
              "example.invalid" in root._text.get("1.0", "end"))
        done()
    _wait_until(root, ready, step1)

@test
def test_large_markdown_renders(done):
    # A large markdown file must render (async, batched) instead of freezing
    # the window blank on the synchronous parse.
    _open([BIG_MD])
    def ready():
        t = root._text
        return t is not None and len(t.get("1.0", "end")) > 100000
    def step1():
        check("large markdown fully rendered",
              len(root._text.get("1.0", "end")) > 100000,
              len(root._text.get("1.0", "end")))
        check("markdown status", "Markdown" in root._status.get(),
              root._status.get())
        done()
    _wait_until(root, ready, step1, timeout_ms=60000)

@test
def test_prev_next(done):
    _open([TXT, MD])
    check("first file is text", root._mode == "text", root._mode)
    check("title follows file", "note.txt — Viewer" in root.title(), root.title())
    root._show(1)
    check("next shows markdown", root._mode == "markdown", root._mode)
    check("title follows next file", "doc.md — Viewer" in root.title(), root.title())
    root._show(0)
    check("prev returns to text", root._mode == "text", root._mode)
    done()

@test
def test_multi_line_text_scrolls(done):
    _open([BIG_TXT])
    check("large text inserted fully", len(root._text.get("1.0", "end")) > 70000,
          len(root._text.get("1.0", "end")))
    root._text.yview_moveto(1.0)
    check("text scrolls to bottom", float(root._text.yview()[1]) == 1.0,
          repr(root._text.yview()))
    done()

@test
def test_image_view(done):
    if not HAVE_PILLOW:
        print("SKIP image view (no Pillow)", flush=True)
        done()
        return
    _open([PNG])
    def done2():
        done()
    _wait_until(root, lambda: root._photo is not None, lambda: (
        check("image photo displayed", root._photo is not None),
        check("image status shows dimensions", "64×48" in root._status.get(),
              root._status.get()),
        done2(),
    ))

@test
def test_zoom_buttons(done):
    if not HAVE_PILLOW:
        print("SKIP zoom (no Pillow)", flush=True)
        done()
        return
    _open([PNG])
    def step1():
        check("zoom starts at fit", root._zoom == 1.0, root._zoom)
        root._zoom_by(2.0)
        check("zoom in changes zoom", root._zoom == 2.0, root._zoom)
        root._zoom_fit()
        check("fit restores zoom", root._zoom == 1.0, root._zoom)
        done()
    _wait_until(root, lambda: root._photo is not None, step1)

@test
def test_drag_pans_zoomed_image(done):
    # Zooming past the viewport must let the user pan with a mouse drag: a
    # B1 drag moves the view over the scrollregion and the cursor switches to
    # a pan cursor while dragging (restored on release).
    if not HAVE_PILLOW:
        print("SKIP drag pan (no Pillow)", flush=True)
        done()
        return
    _open([BIG])
    def step1():
        root._zoom = 8.0
        root._render_image()
        x0, y0, x1, y1 = (int(v) for v in
                          root._canvas.cget("scrollregion").split())
        vw = max(root._canvas.winfo_width(), 1)
        vh = max(root._canvas.winfo_height(), 1)
        check("zoomed image overflows the canvas",
              (x1 - x0) > vw and (y1 - y0) > vh,
              (x1 - x0, vw, y1 - y0, vh))
        vx0 = root._canvas.xview()[0]
        vy0 = root._canvas.yview()[0]
        root._pan_begin(SimpleNamespace(x=200, y=150))
        root._pan_move(SimpleNamespace(x=150, y=100))  # drag 50 px up-left
        check("drag pans the view",
              root._canvas.xview()[0] > vx0 and root._canvas.yview()[0] > vy0,
              (vx0, root._canvas.xview()[0], vy0, root._canvas.yview()[0]))
        root._pan_end(SimpleNamespace(x=150, y=100))
        check("pan cursor restored after drag",
              str(root._canvas.cget("cursor")) == "hand2",
              root._canvas.cget("cursor"))
        done()
    _wait_until(root, lambda: root._photo is not None, step1)

@test
def test_arrow_keys_pan_zoomed_image(done):
    # Arrow keys must pan a zoomed image with standard scroll semantics:
    # Right/Down move the view toward that edge (the fraction increases).
    if not HAVE_PILLOW:
        print("SKIP arrow-key pan (no Pillow)", flush=True)
        done()
        return
    _open([BIG])
    def step1():
        root._zoom = 8.0
        root._render_image()
        vx0 = root._canvas.xview()[0]
        vy0 = root._canvas.yview()[0]
        root._pan_key(SimpleNamespace(keysym="Right"))
        root._pan_key(SimpleNamespace(keysym="Down"))
        check("arrow keys pan the view",
              root._canvas.xview()[0] > vx0 and root._canvas.yview()[0] > vy0,
              (vx0, root._canvas.xview()[0], vy0, root._canvas.yview()[0]))
        done()
    _wait_until(root, lambda: root._photo is not None, step1)

@test
def test_animated_gif(done):
    if not HAVE_PILLOW:
        print("SKIP animated gif (no Pillow)", flush=True)
        done()
        return
    _open([GIF])
    def step1():
        check("gif animation started", root._anim_it is not None)
        done()
    _wait_until(root, lambda: root._anim_it is not None, step1)

@test
def test_zoom_capped_at_max_pixels(done):
    # MAX_ZOOM on a large photo must not allocate unbounded pixel buffers
    # (OOM risk in the WASM guest): the rendered size is clamped and the
    # canvas pans instead.
    if not HAVE_PILLOW:
        print("SKIP zoom cap (no Pillow)", flush=True)
        done()
        return
    _open([BIG])
    def step1():
        root._zoom = 8.0
        root._render_image()
        px = root._photo.width() * root._photo.height()
        check("zoom render capped at 16 MP",
              px <= 16 * 1024 * 1024, px)
        done()
    _wait_until(root, lambda: root._photo is not None, step1)

@test
def test_broken_image_with_text_falls_back(done):
    # A file with an image extension that fails to decode but sniffs as text
    # falls back to the text view — and the dead image canvas must be
    # dropped, not left splitting the window.
    if not HAVE_PILLOW:
        print("SKIP broken-image fallback (no Pillow)", flush=True)
        done()
        return
    _open([FAKE_PNG_TEXT])
    def step1():
        check("fell back to text view", root._mode == "text", root._mode)
        check("image canvas cleared", root._canvas is None)
        check("text content shown",
              root._text.get("1.0", "end").startswith("this is text"),
              repr(root._text.get("1.0", "end")[:30]))
        done()
    _wait_until(root, lambda: root._mode == "text" and root._canvas is None,
                step1)

@test
def test_close_button(done):
    # The toolbar's ✕ Close button must close the viewer (the explorer's
    # watcher then re-enables the file manager).
    closed = []
    orig_destroy = root.destroy
    root.destroy = lambda: closed.append(True)
    root._close_btn.invoke()
    root.destroy = orig_destroy
    check("close button present and closes the viewer",
          root._close_btn is not None and bool(closed))
    done()

# ---- destructive-behavior tests (last; the destroy call is recorded
# instead of executed so the shared root survives) ----------------------------

@test
def test_unknown_binary_closes(done):
    _MSGBOX["errors"] = []
    closed = []
    orig_destroy = root.destroy
    root.destroy = lambda: closed.append(True)
    # _show() runs synchronously: error box + destroy recorded. The patch is
    # restored right after (NOT in a finally — the synchronous done() chain
    # would let this test's finally clobber the next test's patch).
    _open([BIN])
    root.destroy = orig_destroy
    check("error reported for binary file", bool(_MSGBOX["errors"]),
          repr(_MSGBOX["errors"]))
    check("viewer closes itself for binary", bool(closed))
    done()

@test
def test_broken_image_closes(done):
    if not HAVE_PILLOW:
        print("SKIP broken image (no Pillow)", flush=True)
        done()
        return
    _MSGBOX["errors"] = []
    closed = []
    orig_destroy = root.destroy
    root.destroy = lambda: closed.append(True)
    # NOTE: the patch must stay active until the load thread's failure
    # callback fires (the decode error arrives asynchronously via after()),
    # so it is restored inside poll(), not in a finally.
    _open([BAD])
    def poll(waited=[0]):
        if closed or waited[0] >= 15000:
            root.destroy = orig_destroy
            check("error reported for broken image",
                  bool(_MSGBOX["errors"]), repr(_MSGBOX["errors"]))
            check("viewer closes itself for broken image", bool(closed))
            done()
        else:
            waited[0] += 100
            root.after(100, poll)
    root.after(100, poll)

@test
def test_oversized_image_refused(done):
    # MAX_DECODE_PIXELS guard: an image beyond the decode budget must be
    # refused BEFORE decoding (decompression-bomb protection in the
    # memory-limited WASM guest — draft() only limits JPEG/TIFF, so the
    # budget applies to every format). The viewer reports the error and
    # closes, exactly like the broken-image path.
    if not HAVE_PILLOW:
        print("SKIP oversized image (no Pillow)", flush=True)
        done()
        return
    _MSGBOX["errors"] = []
    closed = []
    orig_destroy = root.destroy
    root.destroy = lambda: closed.append(True)
    HUGE = os.path.join(_FIX, "huge.png")
    Image.new("RGB", (6000, 6000), (5, 5, 5)).save(HUGE)  # 36 MP > 32 MP budget
    _open([HUGE])
    def poll(waited=[0]):
        if closed or waited[0] >= 15000:
            root.destroy = orig_destroy
            check("error reported for oversized image",
                  bool(_MSGBOX["errors"]) and "too large" in _MSGBOX["errors"][-1],
                  repr(_MSGBOX["errors"]))
            check("viewer closes itself for oversized image", bool(closed))
            done()
        else:
            waited[0] += 100
            root.after(100, poll)
    root.after(100, poll)

# ==================== run =====================================================

def _run_all():
    idx = [0]
    def next_test():
        if idx[0] >= len(TESTS):
            print("RESULT:", "FAIL " + repr(FAILURES) if FAILURES else "PASS ALL",
                  flush=True)
            os._exit(1 if FAILURES else 0)
        fn = TESTS[idx[0]]
        idx[0] += 1
        fn(next_test)
    next_test()

root.after(500, _run_all)
root.mainloop()
print("RESULT: FAIL suite ended without _run_all", flush=True)
os._exit(1)
