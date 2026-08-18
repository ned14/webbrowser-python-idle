#!/usr/bin/env python3
"""Self-contained test suite for file-explorer.py.

Usage:  python3 file-explorer-tests.py    (needs a display; Xvfb works)

The suite loads file-explorer.py as source, injects a test harness just before
its event loop, and runs every @test function in order. Each test is a function
taking `done`; it schedules steps with the helpers below and calls `done()`
when finished.

Adding a new test:

    @test
    def test_something(done):
        _load(SRC, done)                    # wait for a fixture folder to load
        def step():
            check("does something", pane.something())
            done()
        _steps([(150, step)], done)

The harness stubs dialogs/menus so nothing modal blocks: menus are captured via
pane._post_menu, real opens are counted via the `_open_externally` /
`_open_in_idle` / `_open_in_viewer` stubs (the originals are kept as
_ORIG_EXTERNAL/_ORIG_IDLE/_ORIG_VIEWER and used by the open-path tests),
messagebox is captured (never blocks), folder-size threads are disabled (call
the original via _ORIG_FOLDER_SIZE to test them), and simpledialog/filedialog
can be stubbed per test.
"""

import os
import sys

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file-explorer.py")

HARNESS = r'''
# ==================== injected test harness ====================
import os as _os
import shutil as _shutil
import sys as _sys
import tempfile as _tempfile

# ---- framework -------------------------------------------------------------
TESTS = []
FAILURES = []

def test(fn):
    TESTS.append(fn)
    return fn

def check(name, ok, detail=""):
    # %s on a bare tuple detail would be treated as a format-args tuple, so
    # wrap it: a tuple detail is formatted as its repr inside a single arg.
    if detail:
        detail = "-> %s" % (detail,)
    print(("PASS" if ok else "FAIL"), name, detail, flush=True)
    if not ok:
        FAILURES.append(name)

def _steps(seq, done):
    seq = list(seq)
    def go():
        if not seq:
            done()
            return
        ms, fn = seq.pop(0)
        def step():
            try:
                fn()
            except Exception as e:
                check("step raised", False, repr(e))
            go()
        root.after(ms, step)
    go()

def _wait_until(cond, done, timeout_ms=8000, poll=150):
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

def _load(path, done):
    pane.load_folder(path)
    state = {}
    def poll():
        if pane.current_path != path:
            root.after(150, poll)
            return
        # Ignore stale items from the previous directory.
        if not pane.displayed_paths or not all(
                os.path.dirname(p) == path for p in pane.displayed_paths):
            root.after(150, poll)
            return
        # Wait until the listing stops changing (async load / refreshes done),
        # which also rejects a stale listing of the same directory.
        if state.get("prev") == list(pane.displayed_paths):
            done()
            return
        state["prev"] = list(pane.displayed_paths)
        root.after(150, poll)
    root.after(150, poll)

# ---- global test state ------------------------------------------------------
lb = pane.file_list
OPENS = {"calls": []}        # _open_externally() calls (unknown binary types)
IDLE_OPENS = {"calls": []}   # _open_in_idle() calls (.py files -> IDLE)
VIEWER_OPENS = {"calls": []} # _open_in_viewer() calls (text/image -> viewer)
_LAST_MENU = [None]
_TBASE = [1000]

_ORIG_EXTERNAL = pane._open_externally
_ORIG_IDLE = pane._open_in_idle
_ORIG_VIEWER = pane._open_in_viewer

def _fake_external(path):
    OPENS["calls"].append(path)

def _fake_idle(paths):
    IDLE_OPENS["calls"].append(list(paths))

def _fake_viewer(paths):
    VIEWER_OPENS["calls"].append(list(paths))

pane._open_externally = _fake_external
pane._open_in_idle = _fake_idle
pane._open_in_viewer = _fake_viewer

def _grab_menu(menu, x, y):
    _LAST_MENU[0] = menu
pane._post_menu = _grab_menu

_ORIG_FOLDER_SIZE = pane.compute_folder_size
pane.compute_folder_size = lambda p: None

# messagebox: capture instead of showing modal dialogs
_MSGBOX = {"errors": [], "yesno": True}
def _fake_showerror(title, message, *a, **k):
    _MSGBOX["errors"].append(message)
def _fake_askyesno(*a, **k):
    return _MSGBOX["yesno"]
messagebox.showerror = _fake_showerror
messagebox.askyesno = _fake_askyesno

# ---- gesture helpers --------------------------------------------------------
def _next_t(gap=700):
    _TBASE[0] += gap
    return _TBASE[0]

def _row_y(row_idx):
    b = lb.bbox(lb.get_children()[row_idx])
    return b[1] + b[3] // 2 if b else 22

def _press(row, t=None, state=0):
    if t is None:
        t = _next_t()
    y = _row_y(row)
    lb.event_generate("<ButtonPress-1>", x=60, y=y, rootx=300, rooty=300,
                      time=t, state=state, when="now")
    return t

def _release(row, t, state=0):
    y = _row_y(row)
    lb.event_generate("<ButtonRelease-1>", x=60, y=y, rootx=300, rooty=300,
                      time=t + 50, state=state, when="now")

def _click(row, state=0):
    t = _press(row, state=state)
    _release(row, t, state=state)

def _dblclick(row):
    t = _next_t()
    y = _row_y(row)
    for dt, ev in ((0, "<ButtonPress-1>"), (50, "<ButtonRelease-1>"),
                   (100, "<ButtonPress-1>"), (150, "<ButtonRelease-1>")):
        lb.event_generate(ev, x=60, y=y, rootx=300, rooty=300, time=t + dt, when="now")

def _tap_empty(state=0):
    t = _next_t()
    lb.event_generate("<ButtonPress-1>", x=60, y=400, rootx=300, rooty=300,
                      time=t, state=state, when="now")
    lb.event_generate("<ButtonRelease-1>", x=60, y=400, rootx=300, rooty=300,
                      time=t + 50, state=state, when="now")

def _key(pattern):
    lb.event_generate(pattern, when="now")

def _toolbar():
    return [w.cget("text") for w in pane.toolbar.winfo_children()
            if w.winfo_class() == "Button"]

def _paste_state():
    for w in pane.toolbar.winfo_children():
        if w.winfo_class() == "Button" and str(w.cget("text")).startswith("Paste"):
            return str(w.cget("state"))
    return None

def _paste_text():
    for w in pane.toolbar.winfo_children():
        if w.winfo_class() == "Button" and str(w.cget("text")).startswith("Paste"):
            return str(w.cget("text"))
    return None

def _rename_state():
    for w in pane.toolbar.winfo_children():
        if w.winfo_class() == "Button" and w.cget("text") == "Rename":
            return str(w.cget("state"))
    return None

def _sel_rows():
    rows = lb.get_children()
    return sorted(rows.index(i) for i in lb.selection())

def _visible_names():
    return [os.path.basename(p) for p in pane.displayed_paths]

def _row_of(path_suffix):
    for i, iid in enumerate(lb.get_children()):
        if pane.item_path[iid].endswith(path_suffix):
            return i
    raise ValueError("no row for " + path_suffix)

def _menu_labels(menu):
    out = []
    end = menu.index("end")
    n = (end + 1) if end is not None else 0
    for i in range(n):
        if menu.type(i) in ("command", "cascade"):
            out.append(menu.entrycget(i, "label"))
    return out

# ---- fixtures ---------------------------------------------------------------
_FIX = _os.path.join(_tempfile.mkdtemp(prefix="fe-tests-"))
SRC = _os.path.join(_FIX, "src")       # app.py, visible.txt, subdir/inner.txt
MULTI = _os.path.join(_FIX, "multi")   # a.txt .. e.txt (5 items)
DST = _os.path.join(_FIX, "dest")      # empty
LOTS = _os.path.join(_FIX, "lots")     # f01.txt .. f60.txt (scrollable)

def _make_fixtures():
    for d in (SRC, MULTI, DST, LOTS):
        if _os.path.isdir(d):
            _shutil.rmtree(d)
        _os.makedirs(d)
    with open(_os.path.join(SRC, "app.py"), "w") as f:
        f.write("print('hi')\n")
    with open(_os.path.join(SRC, "visible.txt"), "w") as f:
        f.write("hello\n")
    with open(_os.path.join(SRC, ".hidden.txt"), "w") as f:
        f.write("secret\n")
    _os.makedirs(_os.path.join(SRC, ".hiddendir"))
    _os.makedirs(_os.path.join(SRC, "subdir"))
    with open(_os.path.join(SRC, "subdir", "inner.txt"), "wb") as f:
        f.write(b"x" * 2048)
    for n in "abcde":
        with open(_os.path.join(MULTI, n + ".txt"), "w") as f:
            f.write(n)
    for i in range(1, 61):
        with open(_os.path.join(LOTS, "f%02d.txt" % i), "w") as f:
            f.write(str(i))

_make_fixtures()

# ==================== tests ==================================================

@test
def test_initial_load(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (100, lambda: check("dotfiles hidden",
                            _visible_names() == ["app.py", "subdir", "visible.txt"],
                            _visible_names())),
        (0, lambda: check("row height 44",
                          str(_style.lookup("Touch.Treeview", "rowheight")) == "44")),
        (0, lambda: check("default toolbar",
                          _toolbar() == ["Up", "New File", "New Folder", "Paste", "More"],
                          _toolbar())),
    ], done))

@test
def test_tap_select(done):
    _steps([
        (0, lambda: _click(0)),   # row 0 = app.py (a Python file)
        (150, lambda: check("tap selects row 0", len(lb.selection()) == 1, repr(lb.selection()))),
        (150, lambda: check("tap does not open",
                            len(OPENS["calls"]) == 0 and len(IDLE_OPENS["calls"]) == 0,
                            (OPENS["calls"], IDLE_OPENS["calls"]))),
        (150, lambda: check("selection toolbar (single .py -> Open in IDLE)",
                            _toolbar() == ["× 1 selected", "Open in IDLE", "Copy", "Paste", "Rename",
                                           "Move", "Delete", "More"],
                            _toolbar())),
        (0, lambda: _tap_empty()),
        (150, lambda: _click(2)),   # row 2 = visible.txt (not Python)
        (150, lambda: check("selection toolbar (non-py -> Open)",
                            _toolbar() == ["× 1 selected", "Open", "Copy", "Paste", "Rename",
                                           "Move", "Delete", "More"],
                            _toolbar())),
        (0, lambda: _tap_empty()),
    ], done)

@test
def test_tap_empty_clears(done):
    _steps([
        (0, lambda: _tap_empty()),
        (150, lambda: check("tap empty clears selection", lb.selection() == (), repr(lb.selection()))),
        (150, lambda: check("default toolbar restored",
                            _toolbar() == ["Up", "New File", "New Folder", "Paste", "More"], _toolbar())),
    ], done)

@test
def test_double_click_opens(done):
    before_idle = len(IDLE_OPENS["calls"])
    before_viewer = len(VIEWER_OPENS["calls"])
    _steps([
        (0, lambda: _dblclick(0)),   # row 0 = app.py -> IDLE
        (250, lambda: check("double-click .py opens in IDLE",
                            len(IDLE_OPENS["calls"]) == before_idle + 1, IDLE_OPENS["calls"])),
        (0, lambda: _dblclick(2)),   # row 2 = visible.txt -> viewer
        (250, lambda: check("double-click .txt opens in the viewer",
                            len(VIEWER_OPENS["calls"]) == before_viewer + 1, VIEWER_OPENS["calls"])),
    ], done)

@test
def test_long_press_context(done):
    _steps([
        (0, lambda: _tap_empty()),
        (150, lambda: t_setter(_press(1))),
        (1700, lambda: after_fire()),
    ], lambda: None)
    def t_setter(t):
        holder["t"] = t
    def after_fire():
        rows = lb.get_children()
        sel = lb.selection()
        check("long-press selects row 1", len(sel) == 1 and sel[0] == rows[1], repr(sel))
        check("long-press posts context menu", _LAST_MENU[0] is not None)
        _release(1, holder["t"] + 1700)
        root.after(150, done)
    holder = {}

@test
def test_long_hold_no_open(done):
    before = len(OPENS["calls"]) + len(IDLE_OPENS["calls"])
    holder = {}
    def do_press():
        holder["t"] = _press(0)
    _steps([
        (0, lambda: _tap_empty()),
        (150, do_press),
        (1700, lambda: _release(0, holder["t"] + 1700)),
        (200, lambda: check("long hold does not open",
                            len(OPENS["calls"]) + len(IDLE_OPENS["calls"]) == before,
                            (OPENS["calls"], IDLE_OPENS["calls"]))),
    ], done)

@test
def test_ctrl_click(done):
    _load(SRC, lambda: _steps([
        (0, lambda: _click(0)),
        (150, lambda: check("plain click selects one", _sel_rows() == [0], repr(_sel_rows()))),
        (0, lambda: _click(2, state=0x0004)),
        (150, lambda: check("ctrl+click adds", _sel_rows() == [0, 2], repr(_sel_rows()))),
        (0, lambda: _click(2, state=0x0004)),
        (150, lambda: check("ctrl+click toggles off", _sel_rows() == [0], repr(_sel_rows()))),
        (0, lambda: _click(2, state=0x0004)),
        (150, lambda: check("ctrl+click reselects", _sel_rows() == [0, 2], repr(_sel_rows()))),
        (0, lambda: _tap_empty(state=0x0004)),
        (150, lambda: check("ctrl+click empty keeps selection", _sel_rows() == [0, 2], repr(_sel_rows()))),
    ], done))

@test
def test_shift_click(done):
    _load(MULTI, lambda: _steps([
        (0, lambda: _click(0)),
        (150, lambda: _click(4, state=0x0001)),
        (150, lambda: check("shift+click range 0..4", _sel_rows() == [0, 1, 2, 3, 4], repr(_sel_rows()))),
        (0, lambda: _click(1, state=0x0001)),
        (150, lambda: check("shift+click range 0..1", _sel_rows() == [0, 1], repr(_sel_rows()))),
        (0, lambda: _click(2, state=0x0004)),
        (150, lambda: _click(3, state=0x0001)),
        (150, lambda: check("ctrl then shift (anchor 0)", _sel_rows() == [0, 1, 2, 3], repr(_sel_rows()))),
        (0, lambda: _tap_empty()),
        (150, lambda: check("empty click clears", lb.selection() == (), repr(lb.selection()))),
        (0, lambda: _click(3, state=0x0001)),
        (150, lambda: check("shift with no anchor -> 0..3", _sel_rows() == [0, 1, 2, 3], repr(_sel_rows()))),
    ], done))

@test
def test_selection_preserved(done):
    _load(SRC, lambda: _steps([
        (0, lambda: _click(1)),
        (150, lambda: holder.__setitem__("sel", set(pane.get_selected_items()))),
        (0, lambda: pane.update_ui()),
        (150, lambda: check("selection preserved across refresh",
                            set(pane.get_selected_items()) == holder["sel"],
                            "%s -> %s" % (sorted(holder["sel"]), sorted(pane.get_selected_items())))),
    ], done))
    holder = {}

@test
def test_search(done):
    _load(SRC, lambda: _steps([
        (0, lambda: search_var.set("visible")),
        (250, lambda: check("search filters", _visible_names() == ["visible.txt"], _visible_names())),
        (0, lambda: search_var.set("")),
        (250, lambda: check("search cleared", len(pane.displayed_paths) == 3, len(pane.displayed_paths))),
    ], done))

@test
def test_select_all_clear(done):
    _load(SRC, lambda: _steps([
        (0, lambda: pane.select_all()),
        (150, lambda: check("select all selects everything",
                            len(lb.selection()) == len(lb.get_children()), repr(lb.selection()))),
        (150, lambda: check("selection toolbar shows count", "× 3 selected" in _toolbar(), _toolbar())),
        (0, lambda: pane.clear_selection()),
        (150, lambda: check("clear selection restores default toolbar",
                            _toolbar() == ["Up", "New File", "New Folder", "Paste", "More"], _toolbar())),
    ], done))

@test
def test_columns(done):
    _load(SRC, lambda: _steps([
        (100, lambda: check_columns()),
        (0, lambda: check("headers (and no tree-column expand boxes)",
                          lb.heading("size")["text"] == "Size"
                          and lb.heading("modified")["text"] == "Last modified"
                          and [str(x) for x in lb.cget("show")] == ["headings"],
                          (lb.heading("size")["text"], lb.heading("modified")["text"], lb.cget("show")))),
        (0, lambda: lazy_size()),
    ], lambda: None))
    def check_columns():
        ok = True
        for iid in lb.get_children():
            path = pane.item_path[iid]
            if os.path.isdir(path):
                continue
            got = tuple(lb.item(iid, "values"))
            exp = ("\u2022 " + os.path.basename(path),
                   pane.human_readable_size(os.path.getsize(path)),
                   pane._mtime_str(os.path.getmtime(path)))
            if got != exp:
                ok = False
                print("  mismatch", path, got, exp, flush=True)
        check("name + size + modified columns match fs", ok)
    def sub_iid():
        for i in pane.displayed_items:
            if pane.item_path[i].endswith("/subdir"):
                return i
        return None
    def lazy_size():
        sub = sub_iid()
        _ORIG_FOLDER_SIZE(pane.item_path[sub])   # run the lazy size computation
        _wait_until(lambda: (lambda i: i is not None and lb.item(i, "values")[1] == "2.00 KB")(sub_iid()),
                    lambda: (check("folder size fills lazily",
                                   (lambda i: i is not None and lb.item(i, "values")[1] == "2.00 KB")(sub_iid())),
                             done()))

@test
def test_menus(done):
    _load(SRC, lambda: _steps([
        (0, lambda: lb.selection_set(lb.get_children()[0])),
        (100, lambda: setattr(pane.file_list, "_popup_xy", (300, 300))),
        (0, lambda: pane._popup_context()),
        (50, lambda: check("context menu (.py)",
                           "Open" in _menu_labels(_LAST_MENU[0])
                           and "Open with IDLE" in _menu_labels(_LAST_MENU[0])
                           and "Add bookmark" not in _menu_labels(_LAST_MENU[0])
                           and "Clear selection" in _menu_labels(_LAST_MENU[0]),
                           _menu_labels(_LAST_MENU[0]))),
        (0, lambda: lb.selection_set(lb.get_children()[2])),
        (100, lambda: pane._popup_context()),
        (50, lambda: check("context menu (.txt) omits IDLE",
                           "Open with IDLE" not in _menu_labels(_LAST_MENU[0])
                           and "Unzip selected" not in _menu_labels(_LAST_MENU[0]),
                           _menu_labels(_LAST_MENU[0]))),
        (0, lambda: lb.selection_set(lb.get_children()[0])),  # back to a .py file
        (100, lambda: pane._popup_more()),
        (50, lambda: check("more menu only keeps non-toolbar actions",
                           "Go to path…" in _menu_labels(_LAST_MENU[0])
                           and "Open Terminal (xterm)" in _menu_labels(_LAST_MENU[0])
                           and "Sort by" in _menu_labels(_LAST_MENU[0])
                           and "Select all" in _menu_labels(_LAST_MENU[0])
                           and "Open with IDLE" in _menu_labels(_LAST_MENU[0])
                           and "Zip selected" in _menu_labels(_LAST_MENU[0])
                           and "Clear selection" in _menu_labels(_LAST_MENU[0])
                           and not any(l in _menu_labels(_LAST_MENU[0])
                                       for l in ("Paste", "Copy", "Move to folder…",
                                                 "Rename…", "Delete")),
                           _menu_labels(_LAST_MENU[0]))),
    ], done))

@test
def test_keyboard_shortcuts(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: _click(_row_of("visible.txt"))),
        (150, lambda: check("tap focuses file list", lb.focus_displayof() == lb, repr(lb.focus_displayof()))),
        (0, lambda: _key("<Control-c>")),
        (100, lambda: check("Ctrl-C copies selection",
                            clipboard == [os.path.join(SRC, "visible.txt")], repr(clipboard))),
        (0, lambda: pane.load_folder(DST)),
        (300, lambda: _key("<Control-v>")),
        (300, lambda: check("Ctrl-V pastes file", os.path.exists(os.path.join(DST, "visible.txt")),
                            repr(os.listdir(DST)))),
        (0, lambda: _key("<Control-a>")),
        (100, lambda: check("Ctrl-A selects all",
                            len(lb.selection()) == len(lb.get_children()), repr(lb.selection()))),
    ], done))

@test
def test_new_file(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: setattr(simpledialog, "askstring", lambda *a, **k: "newfile.txt")),
        (0, lambda: pane.create_file()),
        (200, lambda: check("New File creates empty file",
                            os.path.exists(os.path.join(SRC, "newfile.txt")), repr(os.listdir(SRC)))),
    ], done))

@test
def test_paste_name_conflict(done):
    _make_fixtures()
    holder = {}
    def ask_conflict(*a, **k):
        holder["default"] = k.get("initialvalue")
        return holder.get("answer")
    _load(SRC, lambda: _steps([
        (0, lambda: _click(_row_of("visible.txt"))),
        (100, lambda: _key("<Control-c>")),
        (100, lambda: check("clipboard primed", clipboard == [os.path.join(SRC, "visible.txt")], repr(clipboard))),
        (0, lambda: (holder.__setitem__("answer", "visible (copy).txt"),
                     setattr(simpledialog, "askstring", ask_conflict))),
        (0, lambda: _key("<Control-v>")),   # paste into the same folder
        (400, lambda: check("conflict prompts with default", holder.get("default") == "visible (copy).txt",
                            repr(holder.get("default")))),
        (0, lambda: check("same-folder paste creates a renamed copy",
                          os.path.exists(os.path.join(SRC, "visible (copy).txt"))
                          and os.path.exists(os.path.join(SRC, "visible.txt")),
                          repr(os.listdir(SRC)))),
        (0, lambda: holder.__setitem__("answer", None)),
        (0, lambda: _key("<Control-v>")),   # cancel the conflict prompt
        (400, lambda: check("cancel skips the paste",
                            not os.path.exists(os.path.join(SRC, "visible (copy) (copy).txt"))
                            and os.path.exists(os.path.join(SRC, "visible (copy).txt")),
                            repr(os.listdir(SRC)))),
    ], done))

@test
def test_paste_button_state(done):
    _make_fixtures()
    clipboard.clear()
    _load(SRC, lambda: _steps([
        (100, lambda: check("Paste disabled when clipboard empty",
                            _paste_state() == "disabled" and _paste_text() == "Paste",
                            repr((_paste_state(), _paste_text())))),
        (0, lambda: _click(_row_of("visible.txt"))),
        (150, lambda: _key("<Control-c>")),
        (100, lambda: check("selection toolbar shows Paste",
                            any(t.startswith("Paste") for t in _toolbar()), _toolbar())),
        (100, lambda: check("Paste enabled while selected",
                            _paste_state() == "normal", repr(_paste_state()))),
        (100, lambda: check("Paste label names the file",
                            _paste_text() == "Paste visible.txt", repr(_paste_text()))),
        (0, lambda: pane.clear_selection()),
        (150, lambda: check("Paste enabled after copy",
                            _paste_state() == "normal", repr(_paste_state()))),
        (100, lambda: check("Paste label persists on default bar",
                            _paste_text() == "Paste visible.txt", repr(_paste_text()))),
    ], done))

@test
def test_rename_button_state(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: _click(0)),
        (150, lambda: check("Rename enabled for single selection",
                            _rename_state() == "normal", repr(_rename_state()))),
        (0, lambda: _click(1, state=0x0004)),
        (150, lambda: check("Rename disabled for multi selection",
                            _rename_state() == "disabled", repr(_rename_state()))),
    ], done))

@test
def test_move(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: setattr(filedialog, "askdirectory", lambda *a, **k: DST)),
        (0, lambda: _click(_row_of("visible.txt"))),
        (150, lambda: pane.move_to_folder()),
        (250, lambda: check("Move relocates selected item",
                            os.path.exists(os.path.join(DST, "visible.txt"))
                            and not os.path.exists(os.path.join(SRC, "visible.txt")),
                            repr(os.listdir(SRC)))),
    ], done))

@test
def test_smoke(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: pane.select_all()),
        (100, lambda: pane.copy_items()),
        (50, lambda: check("copy sets clipboard", len(clipboard) == 3, repr(clipboard))),
    ], done))

@test
def test_open_externally_fallback(done):
    _make_fixtures()
    calls = []
    orig_which = shutil.which
    orig_popen = subprocess.Popen
    orig_exists = os.path.exists
    def fake_which(name):
        if name == "xdg-open":
            return None
        if name in ("xterm", "less", "vi"):
            return "/usr/bin/" + name
        return orig_which(name)
    def fake_popen(args, **kw):
        calls.append(list(args))
        return None
    def fake_exists(p):
        # The test host has no IDLE launcher; pretend the guest does.
        if p == "/usr/local/bin/idle3.10-launcher":
            return True
        return orig_exists(p)
    shutil.which = fake_which
    subprocess.Popen = fake_popen
    os.path.exists = fake_exists
    try:
        txt = os.path.join(SRC, "visible.txt")
        py = os.path.join(SRC, "app.py")
        _ORIG_EXTERNAL(txt)
        _ORIG_EXTERNAL(py)
    finally:
        shutil.which = orig_which
        subprocess.Popen = orig_popen
        os.path.exists = orig_exists
    txt_ok = calls[0] == ["/usr/bin/xterm", "-e", "/usr/bin/less", txt]
    py_ok = calls[1] == ["/usr/local/bin/idle3.10-launcher", py]
    check("text file falls back to xterm+less", txt_ok, repr(calls))
    check("py file falls back to IDLE launcher", py_ok, repr(calls))
    done()

@test
def test_human_readable_size(done):
    check("0 bytes", pane.human_readable_size(0) == "0.00 B", pane.human_readable_size(0))
    check("KB", pane.human_readable_size(1024) == "1.00 KB", pane.human_readable_size(1024))
    check("MB", pane.human_readable_size(1024 ** 2) == "1.00 MB", pane.human_readable_size(1024 ** 2))
    check("GB", pane.human_readable_size(1024 ** 3) == "1.00 GB", pane.human_readable_size(1024 ** 3))
    done()

@test
def test_mtime_str(done):
    t = 1600000000
    expect = time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    check("formats a valid timestamp", pane._mtime_str(t) == expect, pane._mtime_str(t))
    check("out-of-range timestamp returns empty",
          pane._mtime_str(10 ** 30) == "", pane._mtime_str(10 ** 30))
    done()

@test
def test_is_text_file(done):
    _make_fixtures()
    txt = os.path.join(SRC, "visible.txt")
    py = os.path.join(SRC, "app.py")
    binary = os.path.join(_FIX, "bin.dat")
    with open(binary, "wb") as f:
        f.write(b"\x00\x01\x02\xff")
    empty = os.path.join(_FIX, "empty")
    with open(empty, "wb") as f:
        pass
    check(".py is text", pane._is_text_file(py) is True)
    check(".txt is text", pane._is_text_file(txt) is True)
    check("binary blob is not text", pane._is_text_file(binary) is False)
    check("unknown-extension file is not text", pane._is_text_file(binary) is False)
    check("extensionless empty file is text", pane._is_text_file(empty) is True)
    done()

@test
def test_sort_entries(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: sort_var.set("Type")),
        (250, lambda: check("sort by Type groups by extension",
                            _visible_names() == ["subdir", "app.py", "visible.txt"],
                            _visible_names())),
        (0, lambda: sort_var.set("Name")),
        (250, lambda: check("sort by Name restores",
                            _visible_names() == ["app.py", "subdir", "visible.txt"],
                            _visible_names())),
        (0, lambda: _set_mtimes()),
        (0, lambda: pane.load_folder(SRC)),  # rebuild the cache with the new mtimes
        (500, lambda: sort_var.set("Date")),
        (250, lambda: check("sort by Date is newest-first",
                            _visible_names() == ["subdir", "app.py", "visible.txt"],
                            _visible_names())),
        (0, lambda: sort_var.set("Name")),   # reset for later tests
    ], done))
    def _set_mtimes():
        os.utime(os.path.join(SRC, "visible.txt"), (100, 100))   # oldest
        os.utime(os.path.join(SRC, "app.py"), (200, 200))
        os.utime(os.path.join(SRC, "subdir"), (300, 300))        # newest

@test
def test_wheel_scrolls(done):
    _make_fixtures()
    _load(LOTS, lambda: _steps([
        (0, lambda: check("list starts at top", float(lb.yview()[0]) == 0.0, repr(lb.yview()))),
        (0, lambda: scroll(5)),
        (250, lambda: check("Button-5 scrolls down", float(lb.yview()[0]) > 0.0, repr(lb.yview()))),
        (0, lambda: scroll(-5)),
        (250, lambda: check("Button-4 scrolls back up", float(lb.yview()[0]) == 0.0, repr(lb.yview()))),
    ], done))
    def scroll(n):
        ev = "<Button-4>" if n < 0 else "<Button-5>"
        for _ in range(abs(n)):
            lb.event_generate(ev, when="now")

@test
def test_breadcrumb_jumps(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: crumb_button()),
        (250, lambda: check("crumb jumps to parent",
                            pane.current_path == _FIX, pane.current_path)),
    ], done))
    def crumb_button():
        target = os.path.basename(_FIX.rstrip(os.sep))
        for w in pane.crumb_frame.winfo_children():
            if w.winfo_class() == "TButton" and w.cget("text") == target:
                w.invoke()
                return
        raise ValueError("no crumb button for " + target)

@test
def test_go_up(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: pane.go_up()),
        (250, lambda: check("go_up goes to parent", pane.current_path == _FIX, pane.current_path)),
    ], done))

@test
def test_go_to_path(done):
    _make_fixtures()
    holder = {}
    _load(SRC, lambda: _steps([
        (0, lambda: setattr(simpledialog, "askstring", lambda *a, **k: holder.get("answer", MULTI))),
        (0, lambda: pane.go_to_path()),
        (250, lambda: check("go_to_path navigates", pane.current_path == MULTI, pane.current_path)),
        (0, lambda: holder.__setitem__("answer", "/nonexistent/xyz")),
        (0, lambda: _MSGBOX["errors"].clear()),
        (0, lambda: pane.go_to_path()),
        (250, lambda: check("invalid path shows an error and stays",
                            pane.current_path == MULTI and len(_MSGBOX["errors"]) == 1,
                            (_MSGBOX["errors"], pane.current_path))),
        (0, lambda: holder.__setitem__("answer", None)),
        (0, lambda: pane.go_to_path()),
        (250, lambda: check("cancel keeps the folder", pane.current_path == MULTI, pane.current_path)),
    ], done))

@test
def test_open_selected_dir(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: _dblclick(_row_of("subdir"))),
        (250, lambda: check("double-click dir navigates",
                            pane.current_path == os.path.join(SRC, "subdir"), pane.current_path)),
    ], done))

@test
def test_open_selected_routes_to_viewer(done):
    # "Open" on text/image files must launch the Tk viewer (batched into one
    # process); .py files still go to IDLE.
    _make_fixtures()
    img = os.path.join(SRC, "photo.png")
    with open(img, "w"):
        pass  # routing is extension-based; no real image needed
    before = len(VIEWER_OPENS["calls"])
    before_idle = len(IDLE_OPENS["calls"])
    def select_rows(names):
        rows = [i for i in lb.get_children()
                if os.path.basename(pane.item_path[i]) in names]
        lb.selection_set(*rows)
    _load(SRC, lambda: _steps([
        (0, lambda: select_rows(("visible.txt", "photo.png"))),
        (0, lambda: pane.open_selected()),
        (50, lambda: check("text+image batch to the viewer",
                           len(VIEWER_OPENS["calls"]) == before + 1 and
                           VIEWER_OPENS["calls"][-1] == [
                               os.path.join(SRC, "photo.png"),
                               os.path.join(SRC, "visible.txt")],
                           repr(VIEWER_OPENS["calls"][before:]))),
        (0, lambda: select_rows(("app.py", "visible.txt"))),
        (0, lambda: pane.open_selected()),
        (50, lambda: check("text to viewer, py to IDLE",
                           VIEWER_OPENS["calls"][-1] ==
                           [os.path.join(SRC, "visible.txt")] and
                           IDLE_OPENS["calls"][-1] ==
                           [os.path.join(SRC, "app.py")],
                           repr((VIEWER_OPENS["calls"][before:],
                                 IDLE_OPENS["calls"][before_idle:])))),
    ], done))

@test
def test_open_in_viewer_replaces_screen(done):
    # The viewer swap mirrors the IDLE swap: the explorer withdraws while the
    # viewer runs and reappears once it exits.
    _make_fixtures()
    calls = []
    class FakePopen:
        def __init__(self, args, **kw):
            if args and args[0] != "/usr/local/bin/wm-clients.py":  # ignore the watcher's wm-clients probes
                calls.append(list(args))
            self._end = time.time() + 0.6
        def poll(self):
            return None if time.time() < self._end else 0
    orig_popen = subprocess.Popen
    orig_open = pane._viewer_window_open
    subprocess.Popen = FakePopen
    # No real viewer window is mapped under the test Xvfb (FakePopen swallows
    # the launcher), so pin the window probe to "open": the watcher must
    # notice the viewer PROCESS exiting and bring the explorer back.
    pane._viewer_window_open = lambda: True
    txt = os.path.join(SRC, "visible.txt")
    _ORIG_VIEWER([txt])
    _steps([
        (100, lambda: check("viewer launched for text",
                            calls == [["/usr/local/bin/file-viewer.py", txt]],
                            repr(calls))),
        (100, lambda: check("explorer withdrawn while viewer runs",
                            str(root.state()) == "withdrawn", root.state())),
        # The watcher polls for the viewer's exit at 3 s cadence, so the
        # reappear needs that budget plus margin (as in the lingering test).
        (3500, lambda: check("explorer reappears once viewer exits",
                             str(root.state()) == "normal", root.state())),
        (100, lambda: check("folder reloaded after viewer",
                            pane.current_path == SRC and pane.displayed_paths,
                            pane.current_path)),
    ], lambda: (
        subprocess.__setattr__("Popen", orig_popen),
        pane.__setattr__("_viewer_window_open", orig_open),
        done(),
    ))

@test
def test_viewer_window_close_returns_when_process_lingers(done):
    # A lingering viewer process must not keep the explorer hidden: the
    # watcher returns when the viewer WINDOW disappears from the WM client list.
    _make_fixtures()
    calls = []
    class FakePopen:
        def __init__(self, args, **kw):
            if args and args[0] != "/usr/local/bin/wm-clients.py":  # ignore the watcher's wm-clients probes
                calls.append(list(args))
            self.pid = 424243  # process never exits on its own
        def poll(self):
            return None
    orig_popen = subprocess.Popen
    orig_open = pane._viewer_window_open
    holder = {"open": True}
    def fake_open():
        return holder["open"]
    subprocess.Popen = FakePopen
    pane._viewer_window_open = fake_open
    txt = os.path.join(SRC, "visible.txt")
    _ORIG_VIEWER([txt])
    _steps([
        (200, lambda: check("explorer withdrawn while viewer runs",
                            str(root.state()) == "withdrawn", root.state())),
        (0, lambda: holder.__setitem__("open", False)),  # viewer window closes
        (3500, lambda: check("explorer reappears once viewer's window is gone",
                             str(root.state()) == "normal", root.state())),
    ], lambda: (
        subprocess.__setattr__("Popen", orig_popen),
        pane.__setattr__("_viewer_window_open", orig_open),
        done(),
    ))

@test
def test_rename_item(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: _click(_row_of("visible.txt"))),
        (150, lambda: setattr(simpledialog, "askstring", lambda *a, **k: "renamed.txt")),
        (0, lambda: pane.rename_item()),
        (250, lambda: check("rename renames the file",
                            os.path.exists(os.path.join(SRC, "renamed.txt"))
                            and not os.path.exists(os.path.join(SRC, "visible.txt")),
                            repr(os.listdir(SRC)))),
    ], done))

@test
def test_create_folder(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: setattr(simpledialog, "askstring", lambda *a, **k: "newdir")),
        (0, lambda: pane.create_folder()),
        (250, lambda: check("create_folder makes the directory",
                            os.path.isdir(os.path.join(SRC, "newdir")), repr(os.listdir(SRC)))),
    ], done))

@test
def test_delete_items(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: _click(_row_of("visible.txt"))),
        (150, lambda: _click(_row_of("app.py"), state=0x0004)),
        (150, lambda: pane.delete_items()),
        (250, lambda: check("delete removes selected items",
                            not os.path.exists(os.path.join(SRC, "visible.txt"))
                            and not os.path.exists(os.path.join(SRC, "app.py"))
                            and os.path.isdir(os.path.join(SRC, "subdir")),
                            repr(os.listdir(SRC)))),
    ], done))

@test
def test_batch_rename(done):
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (0, lambda: _click(_row_of("visible.txt"))),
        (150, lambda: _click(_row_of("app.py"), state=0x0004)),
        (150, lambda: setattr(simpledialog, "askstring", lambda *a, **k: "renamed_{num}")),
        (0, lambda: pane.batch_rename()),
        (300, lambda: check("batch rename applies the pattern",
                            sorted(os.listdir(SRC)) == [".hidden.txt", ".hiddendir",
                                                        "renamed_1.py", "renamed_2.txt", "subdir"],
                            repr(sorted(os.listdir(SRC))))),
    ], done))

@test
def test_zip_and_unzip(done):
    _make_fixtures()
    zip_path = os.path.join(_FIX, "out.zip")
    _load(SRC, lambda: _steps([
        (0, lambda: _click(_row_of("app.py"))),
        (150, lambda: _click(_row_of("visible.txt"), state=0x0004)),
        (150, lambda: setattr(filedialog, "asksaveasfilename", lambda *a, **k: zip_path)),
        (0, lambda: pane.zip_selected()),
        (300, lambda: check("zip_selected creates an archive",
                            zipfile.is_zipfile(zip_path), zip_path)),
        (300, lambda: check("archive contains the selected files",
                            _zip_names(zip_path) == ["app.py", "visible.txt"], _zip_names(zip_path))),
        (0, lambda: _shutil.copy(zip_path, os.path.join(DST, "out.zip"))),
        (0, lambda: pane.load_folder(DST)),
        (500, lambda: _click(_row_of("out.zip"))),
        (150, lambda: pane.unzip_selected()),
        (500, lambda: check("unzip_selected extracts the archive",
                            os.path.exists(os.path.join(DST, "app.py"))
                            and os.path.exists(os.path.join(DST, "visible.txt")),
                            repr(os.listdir(DST)))),
    ], done))
    def _zip_names(p):
        with zipfile.ZipFile(p) as zf:
            return sorted(zf.namelist())

@test
def test_set_status(done):
    # set_status is a module-level function (not a SingleTab method).
    set_status("working")
    check("set_status updates the bar", status_var.get() == "working", status_var.get())
    set_status("again")
    check("set_status replaces without error", status_var.get() == "again", status_var.get())
    done()

@test
def test_open_with_idle_refuses_non_py(done):
    _make_fixtures()
    before = len(IDLE_OPENS["calls"])
    _load(SRC, lambda: _steps([
        (0, lambda: _click(_row_of("visible.txt"))),
        (150, lambda: pane.open_with_idle()),
        (100, lambda: check("non-.py selection is refused",
                            len(IDLE_OPENS["calls"]) == before
                            and status_var.get().startswith("Select a .py"),
                            (IDLE_OPENS["calls"], status_var.get()))),
    ], done))

@test
def test_slow_release_is_a_tap(done):
    # A release between the tap threshold and the long-press hold must still
    # select. Under CheerpX synthetic mouse events can arrive late (400-600ms
    # after the press), and the old code silently dropped such releases.
    _make_fixtures()
    holder = {}
    _load(SRC, lambda: _steps([
        (0, lambda: _tap_empty()),
        (150, lambda: holder.__setitem__("t", _press(0))),
        (500, lambda: _release(0, holder["t"] + 450)),
        (200, lambda: check("late release still selects (no dropped click)",
                            _sel_rows() == [0], repr(_sel_rows()))),
    ], done))

@test
def test_open_with_idle_shortcut(done):
    _make_fixtures()
    before = len(IDLE_OPENS["calls"])
    _load(SRC, lambda: _steps([
        (0, lambda: _click(_row_of("app.py"))),
        (150, lambda: _key("<Control-o>")),
        (200, lambda: check("Ctrl-O opens the selected .py in IDLE",
                            len(IDLE_OPENS["calls"]) == before + 1
                            and IDLE_OPENS["calls"][-1] == [os.path.join(SRC, "app.py")],
                            IDLE_OPENS["calls"])),
    ], done))

@test
def test_ctrl_w_closes(done):
    # Ctrl+W closes the explorer (the keep-alive relaunches it in the real
    # desktop). Stub close_window so the harness keeps running.
    global close_window
    _make_fixtures()
    holder = {}
    orig = close_window
    close_window = lambda: holder.__setitem__("closed", True)
    try:
        lb.event_generate("<Control-w>", when="now")
        root.update_idletasks()
        check("Ctrl-W invokes close_window", holder.get("closed") is True, repr(holder))
    finally:
        close_window = orig
    done()

@test
def test_toolbar_buttons_look_like_buttons(done):
    # The toolbar buttons must read as buttons (raised relief + visible
    # border), not as bare text.
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (100, lambda: check("toolbar buttons have a visible raised border",
                            all(str(w.cget("relief")) == "raised"
                                and int(w.cget("borderwidth")) >= 1
                                for w in pane.toolbar.winfo_children()
                                if w.winfo_class() == "Button"),
                            [(str(w.cget("relief")), int(w.cget("borderwidth")))
                             for w in pane.toolbar.winfo_children()
                             if w.winfo_class() == "Button"])),
    ], done))

@test
def test_row_icons_are_guest_font_glyphs(done):
    # The row icons must be glyphs the guest font (DejaVu Sans) actually
    # ships — emoji (📁/📄) are absent from it and render as boxes.
    _make_fixtures()
    _load(SRC, lambda: _steps([
        (100, lambda: check("no emoji icons (would render as boxes)",
                            all(not tuple(lb.item(i, "values"))[0].startswith(("\U0001f4c1", "\U0001f4c4"))
                                for i in lb.get_children()),
                            [tuple(lb.item(i, "values"))[0] for i in lb.get_children()])),
        (0, lambda: check("folders get the ▸ glyph, files the • glyph",
                          any(tuple(lb.item(i, "values"))[0].startswith("\u25b8")
                              for i in lb.get_children())
                          and any(tuple(lb.item(i, "values"))[0].startswith("\u2022")
                                  for i in lb.get_children()),
                          [tuple(lb.item(i, "values"))[0] for i in lb.get_children()])),
    ], done))

@test
def test_open_with_idle_replaces_screen(done):
    _make_fixtures()
    calls = []
    class FakePopen:
        def __init__(self, args, **kw):
            if args and args[0] != "/usr/local/bin/wm-clients.py":  # ignore the watcher's wm-clients probes
                calls.append(list(args))
            self._end = time.time() + 0.6
        def poll(self):
            return None if time.time() < self._end else 0
    orig_popen = subprocess.Popen
    orig_open = pane._idle_window_open
    subprocess.Popen = FakePopen
    # No real IDLE window is mapped under the test Xvfb (FakePopen swallows
    # the launcher), so pin the window probe to "open": the watcher must
    # notice the IDLE PROCESS exiting and bring the explorer back.
    pane._idle_window_open = lambda basenames: True
    try:
        py = os.path.join(SRC, "app.py")
        _ORIG_IDLE([py])
        _steps([
            (100, lambda: check("IDLE launcher invoked for the .py",
                                calls == [["/usr/local/bin/idle3.10-launcher", py]], repr(calls))),
            (100, lambda: check("explorer withdrawn while IDLE runs",
                                str(root.state()) == "withdrawn", root.state())),
            # The watcher polls for IDLE's exit at 0.5 s cadence: the 0.6 s
            # fake process lifetime plus one poll, with a margin.
            (1500, lambda: check("explorer reappears once IDLE exits",
                                 str(root.state()) == "normal", root.state())),
            (100, lambda: check("folder reloaded after IDLE",
                                pane.current_path == SRC and pane.displayed_paths,
                                pane.current_path)),
        ], done)
    finally:
        subprocess.Popen = orig_popen
        pane._idle_window_open = orig_open

@test
def test_idle_window_close_returns_when_process_lingers(done):
    # Closing IDLE can leave the idle3.10 process alive (it waits on its shell
    # subprocess, kept busy by a running game). The explorer must detect the
    # IDLE window disappearing and return to the file manager anyway.
    _make_fixtures()
    calls = []
    class FakePopen:
        def __init__(self, args, **kw):
            if args and args[0] != "/usr/local/bin/wm-clients.py":  # ignore the watcher's wm-clients probes
                calls.append(list(args))
            self.pid = 424242  # process never exits on its own
        def poll(self):
            return None
    orig_popen = subprocess.Popen
    orig_open = pane._idle_window_open
    holder = {"open": True}
    def fake_open(basenames):
        return holder["open"]
    subprocess.Popen = FakePopen
    pane._idle_window_open = fake_open
    py = os.path.join(SRC, "app.py")
    _ORIG_IDLE([py])
    _steps([
        (200, lambda: check("explorer withdrawn while IDLE runs",
                            str(root.state()) == "withdrawn", root.state())),
        (0, lambda: holder.__setitem__("open", False)),  # user closes IDLE
        (3500, lambda: check("explorer reappears once IDLE's window is gone",
                             str(root.state()) == "normal", root.state())),
    ], lambda: (
        subprocess.__setattr__("Popen", orig_popen),
        pane.__setattr__("_idle_window_open", orig_open),
        done(),
    ))

@test
def test_kill_idle_tree(done):
    # A game running in IDLE's shell subprocess must be killed when IDLE is
    # closed — even if the launcher process itself is still alive.
    sub = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(60)"])
    launcher = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pane._kill_idle_tree(launcher, [sub.pid])
        rc_sub = sub.wait(timeout=5)
        rc_launcher = launcher.wait(timeout=5)
        check("shell subprocess killed", rc_sub == -signal.SIGKILL, rc_sub)
        check("launcher killed", rc_launcher == -signal.SIGKILL, rc_launcher)
    finally:
        for p in (sub, launcher):
            if p.poll() is None:
                p.kill()
    done()

@test
def test_shell_subprocess_discovery(done):
    # The watcher finds IDLE's Python-shell subprocess (where programs run) by
    # scanning /proc for a direct child whose command line mentions idlelib.run.
    launcher = subprocess.Popen([_sys.executable, "-c",
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', 'idlelib.run'])\n"
        "time.sleep(60)"])
    child_pid = None
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            found = pane._shell_subprocesses(launcher)
            if found:
                child_pid = found[0]
                break
            time.sleep(0.2)
        check("discovers the idlelib.run shell subprocess",
              child_pid is not None, found if child_pid is None else child_pid)
        if child_pid:
            pane._kill_idle_tree(launcher, [child_pid])
            dead = False
            for _ in range(20):
                try:
                    with open("/proc/%d/stat" % child_pid, "rb") as f:
                        stat = f.read()
                    rp = stat.rfind(b")")
                    if rp < 0 or stat[rp + 2:].split()[0] == b"Z":
                        dead = True
                        break
                except OSError:
                    dead = True
                    break
                time.sleep(0.25)
            check("shell subprocess killed with the tree", dead,
                  "dead" if dead else "still alive")
    finally:
        if child_pid:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except OSError:
                pass
        if launcher.poll() is None:
            launcher.kill()
    done()

# ==================== run =====================================================

def _run_all():
    idx = [0]
    def next_test():
        if idx[0] >= len(TESTS):
            print("RESULT:", "FAIL " + repr(FAILURES) if FAILURES else "PASS ALL", flush=True)
            _os._exit(1 if FAILURES else 0)
        fn = TESTS[idx[0]]
        idx[0] += 1
        fn(next_test)
    next_test()

root.after(1200, _run_all)
'''

def main():
    with open(APP) as f:
        src = f.read()
    if "root.mainloop()" not in src:
        sys.exit("file-explorer.py: root.mainloop() not found")
    src = src.replace("root.mainloop()", HARNESS + "\nroot.mainloop()", 1)
    exec(compile(src, APP, "exec"), {"__name__": "__main__", "__file__": APP})

if __name__ == "__main__":
    main()
