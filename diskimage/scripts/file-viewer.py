#!/usr/bin/env python3
"""Tk file viewer: images (Pillow), text, and Markdown (mistune).

Usage:  file-viewer.py FILE [FILE ...]

Shows one file at a time with Previous/Next buttons when several files are
given. The file explorer's "Open" launches this for every non-Python file and
replaces itself on screen until the viewer window closes (see
file-explorer.py _open_in_viewer / keep-file-explorer.sh).

Tk-only by design (plans/display-bug.md: Tk is the debugged toolkit under the
patched X stack; GTK is not usable). Pillow (py3-pillow) and mistune
(py3-mistune) are the only extra guests; both come from the Alpine v3.24 apk
repos and the viewer degrades gracefully without them (markdown -> plain
text; image -> error).

The viewer window reports class "FileViewer" and a "<name> — Viewer" title so
the explorer's WM-client-list watcher can detect it.
"""

import io
import os
import sys
import threading
import tkinter as tk
from tkinter import font, messagebox, ttk

from file_types import IMAGE_EXTS, MARKDOWN_EXTS, TEXT_EXTS

try:
    from PIL import Image, ImageOps, ImageSequence, ImageTk
    HAVE_PILLOW = True
except Exception:
    HAVE_PILLOW = False

try:
    import mistune
    # mistune 2.x ships AstRenderer; mistune 3.x (3.1+) dropped it — the
    # token-list output is then `mistune.Markdown(renderer=None)` (parse
    # returns the parsed tokens when renderer is None). Both are supported.
    HAVE_MISTUNE = (getattr(mistune, "AstRenderer", None) is not None
                    or getattr(mistune, "Markdown", None) is not None)
except Exception:
    HAVE_MISTUNE = False


def looks_like_text(path):
    """True when the first bytes look like plain text (no control chars).
    Local helper: the explorer's _is_text_file has different semantics
    (extension-first), so only the extension sets are shared via file_types."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(512)
    except OSError:
        return False
    if not chunk:
        return True
    return all(b in (9, 10, 13) or 32 <= b < 127 for b in chunk)

MAX_TEXT_BYTES = 4 * 1024 * 1024  # 4 MiB cap; bigger files are truncated
MAX_ZOOM = 8.0
MIN_ZOOM = 0.05
# Cap on rendered pixels: zooming a huge photo to MAX_ZOOM would allocate
# hundreds of megapixels (hundreds of MB) for the resize — OOM territory in
# the memory-limited WASM guest. Beyond this the canvas pans instead.
MAX_RENDER_PIXELS = 16 * 1024 * 1024

# Arrow-key pan step for a zoomed image, in canvas pixels (Tk auto-repeats
# held keys, so holding an arrow scrolls continuously).
PAN_STEP_PX = 60

DRAFT_MAX = (2048, 2048)  # fast-decode ceiling for JPEG/TIFF before display

# Hard cap on DECODED pixels: draft() limits only JPEG/TIFF, so PNG/BMP/WebP
# etc. would otherwise fully decode at native resolution (a crafted ~100 MP
# PNG allocates ~400 MB+ in the guest, doubled by exif_transpose) before the
# render cap could ever apply. Refuse before load()/transpose — this is a
# decompression-bomb guard, not a display limit.
MAX_DECODE_PIXELS = 32 * 1024 * 1024


class MarkdownToTk:
    """Walk a mistune AST (renderer='ast') into a tk.Text-like sink,
    emitting tag-ranged text in document order.

    The streaming renderer API cannot be used for direct emission (children
    are rendered to strings before the parent method runs), so the document
    is parsed to an AST first and walked here: inline leaves are emitted with
    the currently-open tag stack, block containers wrap them in tags.

    Token shapes accepted are BOTH mistune 2.0.x AstRenderer AND mistune
    3.x (Markdown(renderer=None)) — the two differ in:
      * leaf text is `raw` in 3.x vs `text` in 2.x (the _text helper reads
        either),
      * heading level, link url and list ordering moved into an `attrs`
        dict in 3.x (the _attrs/_level/_url/_ordered helpers read either),
      * image alt lives in the first child's text in 3.x vs `alt` in 2.x,
      * 3.x emits blank_line block tokens between blocks and linebreak
        inline tokens (2.x: newline) — both are handled.
    Shapes (2.x): heading(children, level), paragraph/strong/emphasis
    (children), codespan(text), link(link, children, title), image(src, alt,
    title), list(children, ordered, level, start?), list_item(children,
    level), block_code(text, info), block_quote/block_text(children),
    thematic_break, newline, block_html/block_error (text)."""

    def __init__(self, sink):
        self.sink = sink
        self._pending_text = ""
        self._pending_tags = None
        self._pending_len = 0

    @staticmethod
    def _attrs(tok):
        return tok.get("attrs") or {}

    @staticmethod
    def _text(tok):
        # mistune 3.x leaves carry `raw`, 2.x carried `text`.
        return tok.get("raw", tok.get("text", ""))

    @staticmethod
    def _level(tok):
        return tok.get("level") or MarkdownToTk._attrs(tok).get("level", 1)

    @staticmethod
    def _url(tok):
        return tok.get("link") or MarkdownToTk._attrs(tok).get("url", "")

    @staticmethod
    def _ordered(tok):
        return tok.get("ordered", MarkdownToTk._attrs(tok).get("ordered", False))

    def render(self, tokens):
        for tok in tokens:
            self._block(tok)
        self._flush()

    def _flush(self):
        if not self._pending_text:
            return
        self.sink.insert("end", self._pending_text, self._pending_tags)
        self._pending_text = ""
        self._pending_len = 0
        self._pending_tags = None

    def _emit(self, text, *tags):
        if not text:
            return
        tags_key = tags or None
        # Batch consecutive emissions with IDENTICAL tags into one insert:
        # per-token Tcl calls on a large document are very slow on the
        # emulated CPU (and the tag ranges stay exact).
        if tags_key == self._pending_tags and self._pending_len + len(text) <= 65536:
            self._pending_text += text
            self._pending_len += len(text)
            return
        self._flush()
        self._pending_text = text
        self._pending_len = len(text)
        self._pending_tags = tags_key

    # -- inline ------------------------------------------------------------
    def _inline_tokens(self, tokens, tags=()):
        for tok in tokens:
            t = tok.get("type")
            if t == "text":
                self._emit(self._text(tok), *tags)
            elif t == "strong":
                self._inline_tokens(tok.get("children") or [], tags + ("b",))
            elif t == "emphasis":
                self._inline_tokens(tok.get("children") or [], tags + ("i",))
            elif t == "codespan":
                self._emit(self._text(tok), *(tags + ("code",)))
            elif t == "link":
                self._inline_tokens(tok.get("children") or [], tags + ("link",))
            elif t == "image":
                # No image embedding in v1: show the alt text in link style.
                # mistune 3.x carries the alt as the first child's text;
                # 2.x as the `alt` key.
                alt = ""
                if tok.get("children"):
                    alt = self._text(tok["children"][0])
                else:
                    alt = tok.get("alt") or ""
                self._emit(alt, *(tags + ("link",)))
            elif t in ("linebreak", "softbreak"):
                self._emit("\n", *tags)
            elif t == "inline_html":
                self._emit(self._text(tok), *tags)
            else:
                # Unknown inline construct: render its children as plain text.
                self._inline_tokens(tok.get("children") or [], tags)

    # -- block -------------------------------------------------------------
    def _block(self, tok, tags=()):
        t = tok.get("type")
        if t == "heading":
            self._inline_tokens(tok.get("children") or [],
                                tags + ("h%d" % min(self._level(tok), 4),))
            self._emit("\n\n", *tags)
        elif t == "paragraph":
            self._inline_tokens(tok.get("children") or [], tags)
            self._emit("\n\n", *tags)
        elif t == "block_text":
            self._inline_tokens(tok.get("children") or [], tags)
        elif t == "list":
            self._list(tok, tags)
        elif t == "list_item":
            for child in tok.get("children") or []:
                self._block(child, tags)
            self._emit("\n", *tags)
        elif t == "block_code":
            self._emit("\n", *tags)
            self._emit(self._text(tok).rstrip("\n"),
                       *(tags + ("codeblock",)))
            self._emit("\n\n", *tags)
        elif t == "block_quote":
            for child in tok.get("children") or []:
                self._block(child, tags + ("quote",))
        elif t == "thematic_break":
            self._emit("─" * 48 + "\n\n", *(tags + ("hr",)))
        elif t in ("newline", "blank_line"):
            pass
        elif t == "block_html":
            self._emit(self._text(tok), *tags)
        elif t == "block_error":
            self._emit(self._text(tok), *tags)
        else:
            # Unknown block construct: walk its children with the tag stack.
            for child in tok.get("children") or []:
                self._block(child, tags)

    def _list(self, tok, tags=()):
        ordered = self._ordered(tok)
        start = tok.get("start") or self._attrs(tok).get("start") or 1
        for i, item in enumerate(tok.get("children") or [], start=start):
            bullet = ("%d. " % i) if ordered else "• "
            self._emit(bullet, *(tags + ("li",)))
            for child in item.get("children") or []:
                self._block(child, tags + ("li",))
            self._emit("\n", *tags)


class FileViewer(tk.Tk):
    """Single-window Tk viewer. One instance per viewer session."""

    def __init__(self, paths):
        super().__init__()
        self._paths = list(paths)
        self._index = 0
        self._mode = None
        self._load_token = 0      # bumped on every navigation; stale loads drop
        self._zoom = 1.0          # 1.0 = fit-to-window
        self._pil = None          # full-res image (exif-transposed)
        self._anim_frame = None   # current animated frame, if animating
        self._anim_it = None
        self._anim_after = None
        self._photo = None        # must stay referenced (Tk GC pitfall)
        self._photo_item = None
        self._pan = None           # active B1 drag state (image panning)
        self._canvas = None
        self._text = None
        self._imagetk_ok = HAVE_PILLOW

        self.title((f"{os.path.basename(paths[0])} — Viewer") if paths
                   else "File Viewer")
        # WM_CLASS cannot be set here: this Tk 8.6 build's `wm` command has
        # no `class` subcommand. The explorer's WM-client-list watcher
        # therefore detects the viewer by its "<name> — Viewer" title (and by
        # class "FileViewer" where newer Tk builds allow it).
        self.geometry(f"{min(1400, self.winfo_screenwidth() - 10)}x"
                      f"{min(800, self.winfo_screenheight() - 10)}")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda e: self.destroy())

        # Touch-friendly defaults, same font rules as the file explorer
        # (DejaVu Sans is what font-dejavu actually installs; do NOT use
        # root.option_add("*Font", ...) — it breaks ttk font resolution).
        _tk_default = font.nametofont("TkDefaultFont")
        _tk_default.configure(family="DejaVu Sans", size=14)
        font.nametofont("TkTextFont").configure(family="DejaVu Sans", size=14)
        font.nametofont("TkMenuFont").configure(family="DejaVu Sans", size=13)
        font.nametofont("TkFixedFont").configure(family="DejaVu Sans Mono", size=13)

        self._f_ui = font.Font(family="DejaVu Sans", size=13)
        self._f_bold = font.Font(family="DejaVu Sans", size=13, weight="bold")
        self._f_italic = font.Font(family="DejaVu Sans", size=13, slant="italic")
        self._f_mono = font.Font(family="DejaVu Sans Mono", size=13)
        self._f_h1 = font.Font(family="DejaVu Sans", size=22, weight="bold")
        self._f_h2 = font.Font(family="DejaVu Sans", size=18, weight="bold")
        self._f_h3 = font.Font(family="DejaVu Sans", size=15, weight="bold")
        self._f_h4 = self._f_bold

        self._status = tk.StringVar(value="")
        self._build_toolbar()
        self._body = tk.Frame(self)
        self._body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Show after the window is mapped so fit-to-window sees real sizes.
        self.after(50, lambda: self._show(0))

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------
    def _build_toolbar(self):
        bar = tk.Frame(self)
        bar.pack(fill="x", padx=10, pady=10)

        self._prev_btn = self._touch_button(bar, "◀ Prev",
                                            lambda: self._show(self._index - 1))
        self._prev_btn.pack(side="left", padx=(0, 6))
        self._next_btn = self._touch_button(bar, "Next ▶",
                                            lambda: self._show(self._index + 1))
        self._next_btn.pack(side="left", padx=(0, 12))

        self._zoom_out_btn = self._touch_button(bar, "−",
                                                lambda: self._zoom_by(0.8))
        self._zoom_out_btn.pack(side="left", padx=(0, 4))
        self._fit_btn = self._touch_button(bar, "Fit", self._zoom_fit)
        self._fit_btn.pack(side="left", padx=(0, 4))
        self._zoom_in_btn = self._touch_button(bar, "+",
                                               lambda: self._zoom_by(1.25))
        self._zoom_in_btn.pack(side="left")

        # Close sits at the far right edge; the status label fills the
        # space between the zoom buttons and it. The lambda keeps the lookup
        # live (a bound method captured here would bypass later patches).
        self._close_btn = self._touch_button(bar, "✕ Close",
                                             lambda: self.destroy())
        self._close_btn.pack(side="right")
        ttk.Label(bar, textvariable=self._status, anchor="e",
                  font=self._f_ui).pack(side="right", fill="x", expand=True)
        self._update_nav()

    def _touch_button(self, parent, text, command):
        return tk.Button(parent, text=text, font=self._f_ui, height=1, pady=6,
                         command=command, relief="raised", bd=2,
                         highlightthickness=0, activebackground="#a0a0a0",
                         cursor="hand2")

    def _update_nav(self):
        multi = len(self._paths) > 1
        self._prev_btn.config(
            state="normal" if (multi and self._index > 0) else "disabled")
        self._next_btn.config(
            state="normal" if (multi and self._index < len(self._paths) - 1) else "disabled")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _show(self, i):
        if not 0 <= i < len(self._paths):
            return
        self._index = i
        self._stop_anim()
        self._load_token += 1
        self._photo = None
        self._pil = None
        self._anim_frame = None
        self._anim_it = None
        self._zoom = 1.0
        self._clear_body()
        path = self._paths[i]
        self.title(f"{os.path.basename(path)} — Viewer")
        kind = self._kind(path)
        if kind == "image":
            self._show_image(path)
        elif kind == "markdown":
            self._show_text(path, markdown=True)
        elif kind == "text":
            self._show_text(path)
        else:
            self._status.set("Cannot view this file type")
            messagebox.showerror("File viewer",
                                 f"No viewer for this file type:\n{path}")
            self.destroy()
            return
        self._update_nav()

    @staticmethod
    def _kind(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXTS:
            return "image" if HAVE_PILLOW else "unknown"
        if ext in MARKDOWN_EXTS:
            return "markdown"
        if ext in TEXT_EXTS:
            return "text"
        return "text" if looks_like_text(path) else "unknown"

    def _clear_body(self):
        for w in self._body.winfo_children():
            w.destroy()
        self._canvas = None
        self._text = None
        self._photo_item = None
        self._pan = None

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------
    def _show_image(self, path):
        self._mode = "image"
        self._build_image_canvas()
        self._status.set("Loading…")
        token = self._load_token

        def work():
            try:
                img = Image.open(path)
                # Decompression-bomb guard (MAX_DECODE_PIXELS): check BEFORE
                # load()/exif_transpose() — draft() only helps JPEG/TIFF, and
                # the copy made by exif_transpose doubles the allocation.
                if img.size[0] * img.size[1] > MAX_DECODE_PIXELS:
                    raise ValueError(
                        f"Image too large to decode ({img.size[0]}x{img.size[1]} px; "
                        f"limit {MAX_DECODE_PIXELS} px)")
                # exif_transpose() copies the image and the copy loses
                # is_animated/n_frames, so capture the animation info from
                # the opener first (and skip transposing animated GIFs —
                # they carry no EXIF anyway).
                animated = bool(getattr(img, "is_animated", False)) and \
                    getattr(img, "n_frames", 1) > 1
                if img.format in ("JPEG", "TIFF"):
                    img.draft("RGB", DRAFT_MAX)
                if not animated:
                    img = ImageOps.exif_transpose(img)
                img.load()
            except Exception as e:
                # The except clause deletes `e` when it exits, but the
                # lambda below runs later (after the mainloop picks up the
                # after() callback) — capture into a plain local first.
                err = e
                self.after(0, lambda: self._image_failed(err, token))
                return
            self.after(0, lambda: self._image_loaded(img, token, animated))

        threading.Thread(target=work, daemon=True).start()

    def _build_image_canvas(self):
        wrap = tk.Frame(self._body)
        wrap.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(wrap, bg="#d0d0d0",
                                 highlightthickness=0, cursor="hand2")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self._canvas.yview)
        hsb = ttk.Scrollbar(wrap, orient="horizontal", command=self._canvas.xview)
        self._canvas.config(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self._canvas.bind("<Button-4>", lambda e: self._zoom_by(1.25))
        self._canvas.bind("<Button-5>", lambda e: self._zoom_by(0.8))
        # Drag to pan: once the image is zoomed past the viewport, hold the
        # left button and drag to move around it (the wheel is taken by zoom).
        self._canvas.bind("<ButtonPress-1>", self._pan_begin)
        self._canvas.bind("<B1-Motion>", self._pan_move)
        self._canvas.bind("<ButtonRelease-1>", self._pan_end)
        self._canvas.bind("<Leave>", self._pan_end)
        self._canvas.bind("<Configure>",
                          lambda e: self.after_idle(self._re_fit))
        # Arrow keys pan the zoomed image too. The canvas never takes
        # keyboard focus, so bind at the toplevel and guard on image mode
        # (the text view already scrolls with arrows natively).
        for _ks in ("<Left>", "<Right>", "<Up>", "<Down>"):
            self.bind(_ks, self._pan_key)

    def _re_fit(self):
        # Screen resizes (99-screen-resize.sh) and window changes re-fit the
        # image; a user zoom is respected until the next navigation.
        if self._mode == "image" and self._canvas is not None:
            self._render_image()

    def _image_loaded(self, img, token, animated):
        if token != self._load_token or self._mode != "image":
            return
        self._pil = img
        self._anim_frame = None
        self._anim_it = None
        if animated:
            self._anim_it = iter(ImageSequence.Iterator(img))
        self._render_image()
        if animated:
            self._gif_tick()

    def _image_failed(self, e, token):
        if token != self._load_token or self._mode != "image":
            return
        self._stop_anim()
        path = self._paths[self._index]
        if looks_like_text(path):
            # Drop the dead image canvas first, or the text view is squeezed
            # beside it in a split window.
            self._clear_body()
            self._show_text(path)
            return
        messagebox.showerror("Cannot open image", str(e))
        self.destroy()

    def _to_photo(self, img):
        """ImageTk with a PNG-bytes fallback (I3 contingency: if the
        _imagingtk extension misbehaves under the patched Tcl, feed raw PNG
        bytes to tk.PhotoImage — no C extension involved)."""
        if self._imagetk_ok:
            try:
                return ImageTk.PhotoImage(img)
            except Exception:
                self._imagetk_ok = False
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return tk.PhotoImage(data=buf.getvalue())

    def _render_image(self):
        if self._pil is None or self._canvas is None:
            return
        img = self._anim_frame or self._pil
        w, h = img.size
        cw = max(self._canvas.winfo_width(), 80) - 4
        ch = max(self._canvas.winfo_height(), 80) - 4
        fit = min(1.0, float(cw) / w, float(ch) / h) if w and h else 1.0
        eff = max(MIN_ZOOM, min(MAX_ZOOM, fit * self._zoom))
        disp_w = max(1, int(round(w * eff)))
        disp_h = max(1, int(round(h * eff)))
        # Zoom guard: never allocate more than MAX_RENDER_PIXELS for the
        # resize — beyond it the canvas pans instead of zooming further.
        if disp_w * disp_h > MAX_RENDER_PIXELS:
            k = (float(MAX_RENDER_PIXELS) / (disp_w * disp_h)) ** 0.5
            disp_w = max(1, int(disp_w * k))
            disp_h = max(1, int(disp_h * k))
        thumb = img
        if (disp_w, disp_h) != img.size:
            thumb = img.resize((disp_w, disp_h), Image.LANCZOS)
        try:
            photo = self._to_photo(thumb)
        except Exception as e:
            self._status.set(f"Cannot render image: {e}")
            return
        self._photo = photo
        if self._photo_item is not None:
            try:
                self._canvas.delete(self._photo_item)
            except Exception:
                pass
        self._photo_item = self._canvas.create_image(0, 0, anchor="nw",
                                                     image=photo)
        self._canvas.config(scrollregion=self._canvas.bbox("all"))
        self._status.set(f"{w}×{h}px  {int(round(100.0 * disp_w / w))}%")

    def _zoom_by(self, factor):
        if self._mode != "image" or self._pil is None:
            return
        self._zoom = max(MIN_ZOOM, min(MAX_ZOOM, self._zoom * factor))
        self._render_image()

    def _zoom_fit(self):
        if self._mode == "image" and self._pil is not None:
            self._zoom = 1.0
            self._render_image()

    def _pan_begin(self, e):
        # Record the drag origin and the view fraction AT PRESS: each motion
        # computes the total pixel delta from the origin, so a drag never
        # drifts regardless of how many B1-Motion events fire.
        self._pan = (e.x, e.y, self._canvas.xview()[0], self._canvas.yview()[0])
        self._canvas.configure(cursor="fleur")

    def _pan_move(self, e):
        pan = self._pan
        if pan is None:
            return
        sx, sy, fx, fy = pan
        vw = max(self._canvas.winfo_width(), 1)
        vh = max(self._canvas.winfo_height(), 1)
        try:
            x0, y0, x1, y1 = (int(v) for v in
                              self._canvas.cget("scrollregion").split())
        except (ValueError, tk.TclError):
            return
        # The pan range is the scrollregion minus the visible viewport; a
        # drag toward an edge moves the view toward that edge (dragging right
        # reveals content that was to the left).
        rx = (x1 - x0) - vw
        ry = (y1 - y0) - vh
        if rx > 0:
            nf = min(1.0, max(0.0, fx - (e.x - sx) / float(rx)))
            self._canvas.xview_moveto(nf)
        if ry > 0:
            nf = min(1.0, max(0.0, fy - (e.y - sy) / float(ry)))
            self._canvas.yview_moveto(nf)

    def _pan_end(self, e):
        self._pan = None
        self._canvas.configure(cursor="hand2")

    def _pan_key(self, e):
        # Standard scroll semantics: "Down" reveals content further down (the
        # view fraction increases), the opposite of grab-panning, where the
        # content follows the pointer.
        if self._mode != "image" or self._canvas is None:
            return
        try:
            x0, y0, x1, y1 = (int(v) for v in
                              self._canvas.cget("scrollregion").split())
        except (ValueError, tk.TclError):
            return
        vw = max(self._canvas.winfo_width(), 1)
        vh = max(self._canvas.winfo_height(), 1)
        dx = dy = 0
        if e.keysym == "Left":
            dx = -PAN_STEP_PX
        elif e.keysym == "Right":
            dx = PAN_STEP_PX
        elif e.keysym == "Up":
            dy = -PAN_STEP_PX
        elif e.keysym == "Down":
            dy = PAN_STEP_PX
        else:
            return
        rx = (x1 - x0) - vw
        ry = (y1 - y0) - vh
        if dx and rx > 0:
            nf = min(1.0, max(0.0, self._canvas.xview()[0] + dx / float(rx)))
            self._canvas.xview_moveto(nf)
        if dy and ry > 0:
            nf = min(1.0, max(0.0, self._canvas.yview()[0] + dy / float(ry)))
            self._canvas.yview_moveto(nf)

    def _gif_tick(self):
        if self._anim_it is None or self._mode != "image":
            return
        try:
            self._anim_frame = next(self._anim_it)
        except StopIteration:
            try:
                self._anim_it = iter(ImageSequence.Iterator(self._pil))
                self._anim_frame = next(self._anim_it)
            except StopIteration:
                return
        self._render_image()
        delay = int(self._pil.info.get("duration") or 100)
        self._anim_after = self.after(max(20, delay), self._gif_tick)

    def _stop_anim(self):
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except Exception:
                pass
            self._anim_after = None
        self._anim_it = None
        self._anim_frame = None

    # ------------------------------------------------------------------
    # Text / Markdown
    # ------------------------------------------------------------------
    def _show_text(self, path, markdown=False):
        self._mode = "markdown" if markdown else "text"
        wrap = tk.Frame(self._body)
        wrap.pack(fill="both", expand=True)
        text = tk.Text(wrap, wrap="word", font=self._f_mono,
                       relief="sunken", bd=1, padx=8, pady=8,
                       undo=False, exportselection=True)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
        text.config(yscrollcommand=vsb.set)
        text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._text = text
        self._configure_text_tags(text)
        # NOTE: do NOT set state=disabled here — Tk 8.6's disabled Text
        # silently DROPS programmatic inserts; the widget is locked only
        # around the async parse window and disabled after content lands.

        try:
            with open(path, "rb") as f:
                data = f.read(MAX_TEXT_BYTES)
            truncated = os.path.getsize(path) > len(data)
        except OSError as e:
            self._status.set(f"Cannot read file: {e}")
            return

        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            content = data.decode("latin-1")

        if markdown and HAVE_MISTUNE:
            # Parse OFF the main thread: mistune on a large document blocks
            # the Tk mainloop for a long time on the emulated CPU (the window
            # sits blank). The walker still runs on the main thread (Tk
            # calls). Lock the widget during the parse so the user cannot
            # type into the not-yet-rendered window.
            text.config(state="disabled")
            self._status.set("Rendering…")
            token = self._load_token
            def work():
                try:
                    if getattr(mistune, "AstRenderer", None) is not None:
                        # mistune 2.x
                        md = mistune.create_markdown(renderer=mistune.AstRenderer())
                    else:
                        # mistune 3.x: no AstRenderer — parse with a
                        # renderer-less Markdown, which returns the token
                        # list directly (verified against 3.2.1).
                        md = mistune.Markdown(renderer=None)
                    toks = md(content)
                except Exception:
                    toks = None
                self.after(0, lambda: self._markdown_ready(toks, content,
                                                           truncated, token))
            threading.Thread(target=work, daemon=True).start()
            return

        self._fill_plain_text(text, content, truncated)

    def _markdown_ready(self, toks, content, truncated, token):
        if token != self._load_token or self._mode != "markdown" \
                or self._text is None:
            return
        text = self._text
        text.config(state="normal")  # re-enable for the programmatic inserts
        if toks is None:
            # mistune failed: show the raw text instead.
            self._fill_plain_text(text, content, truncated)
            return
        try:
            MarkdownToTk(text).render(toks)
            if truncated:
                text.insert("end", "\n\n[…] file truncated at %d bytes, "
                                   "showing the beginning" % MAX_TEXT_BYTES)
            text.config(state="disabled")
            self._status.set(f"Markdown — {len(content)} chars")
        except Exception:
            text.delete("1.0", "end")
            self._fill_plain_text(text, content, truncated)

    def _fill_plain_text(self, text, content, truncated):
        if truncated:
            content += ("\n\n[…] file truncated at %d bytes, showing the "
                        "beginning" % MAX_TEXT_BYTES)
        text.insert("1.0", content)
        text.config(state="disabled")
        self._status.set(f"Text — {len(content)} chars")

    def _configure_text_tags(self, text):
        text.tag_configure("h1", font=self._f_h1, spacing1=6, spacing3=6)
        text.tag_configure("h2", font=self._f_h2, spacing1=4, spacing3=4)
        text.tag_configure("h3", font=self._f_h3, spacing1=4, spacing3=4)
        text.tag_configure("h4", font=self._f_h4)
        text.tag_configure("b", font=self._f_bold)
        text.tag_configure("i", font=self._f_italic)
        text.tag_configure("code", font=self._f_mono,
                           background="#e8e8e8")
        text.tag_configure("codeblock", font=self._f_mono,
                           background="#f0f0f0", lmargin1=8, lmargin2=8,
                           spacing1=4, spacing3=4)
        text.tag_configure("quote", font=self._f_italic,
                           foreground="#555555", lmargin1=12, lmargin2=12)
        text.tag_configure("link", foreground="#0000ee", underline=True)
        text.tag_configure("li", lmargin1=20, lmargin2=20)
        text.tag_configure("hr", foreground="#999999")

    def destroy(self):
        self._stop_anim()
        self._load_token += 1
        super().destroy()


def main(argv):
    # PID file for the keep-alive daemon: while the viewer is up, the explorer
    # is withdrawn, and the keep-alive must not treat that as a stuck desktop.
    # pgrep -f is unusable in the guest (the CheerpX core traps on
    # /proc/<pid>/cmdline reads of processes still being set up — see
    # faccessat-fix.c), so liveness is tracked via this file.
    try:
        with open("/tmp/viewer.pid", "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    paths = [p for p in argv if os.path.isfile(p)]
    if not paths:
        print("usage: file-viewer.py FILE [FILE ...]", file=sys.stderr)
        return 2
    app = FileViewer(paths)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
