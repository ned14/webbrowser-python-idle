import os
import shutil
import subprocess
import threading
import time
import zipfile
import tkinter as tk
from tkinter import font, ttk, simpledialog, filedialog, messagebox

# =========================
# App Setup
# =========================
root = tk.Tk()
root.title("Python File Manager")
# Fit the window to the X screen (>=1024x768 via the KMS canvas): a fixed
# 1400x800 window would be clipped on smaller displays.
root.geometry(f"{min(1400, root.winfo_screenwidth() - 10)}x{min(800, root.winfo_screenheight() - 10)}")

# =========================
# Globals
# =========================
clipboard = []
search_var = tk.StringVar()
sort_var = tk.StringVar(value="Name")
status_var = tk.StringVar(value="Ready")

status_clear_timer = None

def close_window():
    """Close the explorer. The keep-alive daemon relaunches it immediately."""
    root.destroy()

def set_status(msg):
    """Show a status message that clears itself after a few seconds."""
    global status_clear_timer
    status_var.set(msg)
    root.update_idletasks()
    if status_clear_timer is not None:
        try:
            root.after_cancel(status_clear_timer)
        except Exception:
            pass
        status_clear_timer = None
    if threading.current_thread() is threading.main_thread():
        def _clear():
            global status_clear_timer
            status_clear_timer = None
            status_var.set("Ready")
        status_clear_timer = root.after(5000, _clear)

# =========================
# Touch-friendly defaults
# =========================
# The guest only ever receives SYNTHETIC mouse events (the browser canvas maps
# touches to mouse), so this UI must work with taps and long-presses alone:
#   tap          -> select (open via the toolbar "Open" button or double-click)
#   long-press   -> toggle-select + context menu (replaces right-click)
#   right-click  -> context menu (desktop parity)
# No hover, no drag-and-drop (a finger press is indistinguishable from scroll
# intent). Push every control toward the ~48px touch-target floor.
#
# DejaVu Sans is the font actually installed in the guest (font-dejavu);
# "Segoe UI" does not exist there and Tk silently falls back to a smaller
# default, so it is removed.
_tk_default = font.nametofont("TkDefaultFont")
_tk_default.configure(family="DejaVu Sans", size=14)
font.nametofont("TkTextFont").configure(family="DejaVu Sans", size=14)
font.nametofont("TkMenuFont").configure(family="DejaVu Sans", size=13)
# NOTE: do NOT use root.option_add("*Font", ...) here — the option database
# lookup splits the unquoted "DejaVu Sans 13" spec and breaks every ttk widget
# that resolves -font, even when an explicit font is passed.

# Shared font objects: tkinter passes tuples through Tcl word splitting, and a
# spaced family ("DejaVu Sans") breaks that. A Font object is passed as its
# single-word font name, so it works for both tk and ttk widgets and styles.
F_UI = font.Font(family="DejaVu Sans", size=13)
F_ROW = font.Font(family="DejaVu Sans", size=14)
F_BOLD = font.Font(family="DejaVu Sans", size=13, weight="bold")
F_HEAD = font.Font(family="DejaVu Sans", size=14, weight="bold")
F_SMALL = font.Font(family="DejaVu Sans", size=12)
F_CRUMB = font.Font(family="DejaVu Sans", size=12)

_style = ttk.Style()
_style.configure("Crumb.TButton", font=F_CRUMB, padding=(8, 4))
_style.configure("Vertical.TScrollbar", width=24, arrowsize=20)
_style.configure("TEntry", padding=8, font=F_ROW)
_style.configure("TCombobox", padding=6, font=F_UI)
_style.configure("Touch.Treeview", rowheight=44, font=F_ROW)
_style.configure("Touch.Treeview.Heading", font=F_UI)

def touch_button(parent, text, command=None):
    """A reliably tall button. tk.Button (not ttk) so the height comes from the
    widget's own `height` option instead of ttk theme padding, which some
    themes ignore."""
    return tk.Button(parent, text=text, font=F_ROW, height=2, pady=8,
                     command=command, relief="flat", bd=0,
                     highlightthickness=0, activebackground="#a0a0a0",
                     cursor="hand2")

# Gesture parameters for the touch pointer model. Because presses arrive as
# synthetic mouse events, tap / long-press / scroll are told apart by time and
# movement only. The long-press hold is generous (1500 ms): under CheerpX a
# click's release can arrive late, and a too-eager long-press would fire
# spurious context menus on ordinary clicks.
TOUCH_PRESS_MS = 1500  # hold at least this long -> long-press (context menu)
TOUCH_MOVE_TOL = 15    # travel beyond this -> scroll intent, cancels long-press

class TouchTree(ttk.Treeview):
    """Treeview (44px rows) with a touch pointer model.

    tap = select (tap empty space to clear); long-press = toggle-select +
    context menu; double-click / right-click = open / context menu.
    Desktop modifiers are honoured: Shift+click selects a range from the
    anchor, Ctrl+click toggles one item in/out of the selection.
    No drag-and-drop (it cannot be distinguished from scrolling on touch, and
    accidental moves are data loss).
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self._press_t = None
        self._press_y = None
        self._press_xy = None
        self._timer = None
        self._long_pressed = False
        self._popup_xy = (None, None)
        self._anchor = None  # range anchor for Shift+click
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<Button-3>", self._on_ctx)
        self.bind("<Control-a>", self._on_ctrl_a)
        self.bind("<Control-c>", self._on_ctrl_c)
        self.bind("<Control-v>", self._on_ctrl_v)
        self.bind("<Control-o>", self._on_ctrl_o)
        self.bind("<Control-w>", self._on_ctrl_w)

    def _on_ctrl_a(self, e):
        self.event_generate("<<SelectAll>>", when="tail")
        return "break"

    def _on_ctrl_c(self, e):
        self.event_generate("<<Copy>>", when="tail")
        return "break"

    def _on_ctrl_v(self, e):
        self.event_generate("<<Paste>>", when="tail")
        return "break"

    def _on_ctrl_o(self, e):
        self.event_generate("<<OpenWithIdle>>", when="tail")
        return "break"

    def _on_ctrl_w(self, e):
        # Close the explorer; the keep-alive daemon relaunches it.
        close_window()
        return "break"

    def _on_press(self, e):
        self.focus_set()  # keyboard shortcuts apply to the file list
        self._press_t = time.monotonic()
        self._press_y = e.y
        self._press_xy = (e.x_root, e.y_root)
        self._long_pressed = False
        self._cancel_timer()
        self._timer = self.after(TOUCH_PRESS_MS, lambda: self._fire_longpress(e.y))
        return "break"

    def _on_motion(self, e):
        if self._press_xy is None:
            return "break"
        dx = e.x_root - self._press_xy[0]
        dy = e.y_root - self._press_xy[1]
        if dx * dx + dy * dy > TOUCH_MOVE_TOL * TOUCH_MOVE_TOL:
            self._cancel_timer()  # movement means scroll intent
        return "break"

    def _on_release(self, e):
        self._cancel_timer()
        if self._press_t is None:
            return "break"
        press_y = self._press_y
        was_long = self._long_pressed
        self._press_t = None
        self._press_xy = None
        self._long_pressed = False
        if was_long:
            return "break"  # a long-press already handled this gesture
        moved = abs(e.y - press_y) > TOUCH_MOVE_TOL
        if moved:
            return "break"  # scroll gesture: leave the selection untouched
        row = self.identify_row(e.y)
        shift = bool(e.state & 0x0001)  # Shift mask
        ctrl = bool(e.state & 0x0004)   # Control mask
        if ctrl:
            # Ctrl+click: toggle just this item in/out of the selection.
            if row:
                sel = list(self.selection())
                if row in sel:
                    sel.remove(row)
                else:
                    sel.append(row)
                self.selection_set(*sel)
        elif shift:
            # Shift+click: select the contiguous range from the anchor.
            rows = self.get_children()
            if row and rows:
                anchor = self._anchor
                if anchor is None or anchor not in rows:
                    # Stale anchor (e.g. after a refresh): fall back to the
                    # first selected row, or the first row.
                    sel = self.selection()
                    anchor = sel[0] if sel else rows[0]
                try:
                    i0, i1 = rows.index(anchor), rows.index(row)
                except ValueError:
                    i0 = i1 = 0
                lo, hi = (i0, i1) if i0 <= i1 else (i1, i0)
                self.selection_set(*rows[lo:hi + 1])
        else:
            # Plain click: replace the selection and set the range anchor.
            if row:
                self.selection_set(row)
                self._anchor = row
            else:
                self.selection_set()  # tap on empty space clears the selection
                self._anchor = None
        self._notify()
        return "break"

    def _on_double(self, e):
        row = self.identify_row(e.y)
        if row:
            self.selection_set(row)
            self._notify()
            self.event_generate("<<FileOpen>>", when="tail")
        return "break"

    def _on_ctx(self, e):
        row = self.identify_row(e.y)
        if row and row not in self.selection():
            self.selection_set(row)
            self._notify()
        self._popup_xy = (e.x_root, e.y_root)
        self.event_generate("<<FileContext>>", when="tail")
        return "break"

    def _fire_longpress(self, y):
        self._cancel_timer()
        self._long_pressed = True
        row = self.identify_row(y)
        if row:
            sel = list(self.selection())
            # Toggle membership in the selection (never opens on a hold).
            if row in sel:
                sel.remove(row)
            else:
                sel.append(row)
            self.selection_set(*sel)
            self._notify()
        if self._press_xy is not None:
            self._popup_xy = self._press_xy
        self.event_generate("<<FileContext>>", when="tail")

    def _notify(self):
        # The Treeview class binding that would emit <<TreeviewSelect>> is
        # suppressed by our "break", so emit it for the selection-mode toolbar.
        self.event_generate("<<TreeviewSelect>>", when="tail")

    def popup_xy(self):
        return self._popup_xy

    def _cancel_timer(self):
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None


def bind_wheel(widget, yview):
    """Attach mouse-wheel scrolling in all event shapes (Windows/mac delta,
    X11 buttons 4/5). Guest X11 delivers wheel as <Button-4>/<Button-5>."""
    def scroll_units(delta):
        return ("scroll", -1 if delta > 0 else 1, "units")
    widget.bind("<MouseWheel>", lambda e: yview(*scroll_units(e.delta)))
    widget.bind("<Button-4>", lambda e: yview("scroll", -1, "units"))
    widget.bind("<Button-5>", lambda e: yview("scroll", 1, "units"))

# =========================
# Single Panel File Browser
# =========================
class SingleTab(ttk.Frame):
    def __init__(self, master, initial_path):
        super().__init__(master)
        self.current_path = initial_path
        self.folder_sizes = {}
        self.entries_cache = []
        self.displayed_paths = []
        self.displayed_items = []
        self.item_path = {}
        self._load_gen = 0
        self._more_button = None
        self._active_menu = None

        # Root container: the main panel
        split_root = ttk.Frame(self)
        split_root.pack(fill="both", expand=True)

        # ---- Main panel ----
        self.main_panel = ttk.Frame(split_root)
        self.main_panel.pack(side="left", fill="both", expand=True)

        # Toolbar: one context-aware row (selection-mode), see _rebuild_toolbar
        self.toolbar = ttk.Frame(self.main_panel)
        self.toolbar.pack(fill="x", pady=(0, 5))
        for c in range(8):
            self.toolbar.columnconfigure(c, weight=1)
        self._rebuild_toolbar()

        # Breadcrumb navigation bar (tap a segment to jump to it)
        self.crumb_frame = ttk.Frame(self.main_panel)
        self.crumb_frame.pack(fill="x", pady=(0, 5))

        # Search + Sort (top, under the breadcrumb)
        search_frame = ttk.Frame(self.main_panel)
        search_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(search_frame, text="Search:").pack(side="left")
        ttk.Entry(search_frame, textvariable=search_var).pack(
            side="left", fill="x", expand=True, padx=5)
        ttk.Label(search_frame, text="Sort by:").pack(side="left", padx=(10, 0))
        ttk.Combobox(search_frame, textvariable=sort_var,
                     values=["Name", "Type", "Date"],
                     state="readonly", width=10).pack(side="left")

        # File list: a touch-aware Treeview with 44px rows, size + modified
        # columns, and no right pane.
        split = ttk.Frame(self.main_panel)
        split.pack(fill="both", expand=True)
        self.file_list = TouchTree(split, columns=("size", "modified"),
                                   show="tree headings", selectmode="extended",
                                   style="Touch.Treeview")
        self.file_list.heading("#0", text="Name", anchor="w")
        self.file_list.heading("size", text="Size", anchor="e")
        self.file_list.heading("modified", text="Last modified", anchor="w")
        self.file_list.column("#0", anchor="w", stretch=True)
        self.file_list.column("size", width=110, minwidth=70, anchor="e", stretch=False)
        self.file_list.column("modified", width=190, minwidth=170, anchor="w", stretch=False)
        self.file_list.pack(side="left", fill="both", expand=True)
        self.file_list.bind("<<TreeviewSelect>>", lambda e: self._rebuild_toolbar())
        self.file_list.bind("<<FileOpen>>", lambda e: self.open_selected())
        self.file_list.bind("<<FileContext>>", lambda e: self._popup_context())
        self.file_list.bind("<<SelectAll>>", lambda e: self.select_all())
        self.file_list.bind("<<Copy>>", lambda e: self.copy_items())
        self.file_list.bind("<<Paste>>", lambda e: self.paste_items())
        self.file_list.bind("<<OpenWithIdle>>", lambda e: self.open_with_idle())
        scrollbar = ttk.Scrollbar(split, orient="vertical",
                                  command=self.file_list.yview)
        scrollbar.pack(side="left", fill="y")
        self.file_list.config(yscrollcommand=scrollbar.set)
        bind_wheel(self.file_list, self.file_list.yview)

        self._rebuild_breadcrumbs()
        self.load_folder(self.current_path)

    # -------------------------
    # Selection-mode toolbar
    # -------------------------
    def _rebuild_toolbar(self):
        for w in self.toolbar.winfo_children():
            w.destroy()
        self._more_button = None
        # The toolbar is built before the file list exists (during __init__).
        n = len(self.file_list.selection()) if hasattr(self, "file_list") else 0
        if n == 0:
            touch_button(self.toolbar, "Up", self.go_up).grid(
                row=0, column=0, sticky="ew", padx=2, pady=2)
            touch_button(self.toolbar, "New File", self.create_file).grid(
                row=0, column=1, sticky="ew", padx=2, pady=2)
            touch_button(self.toolbar, "New Folder", self.create_folder).grid(
                row=0, column=2, sticky="ew", padx=2, pady=2)
            paste = touch_button(self.toolbar, self._paste_label(), self.paste_items)
            paste.grid(row=0, column=3, sticky="ew", padx=2, pady=2)
            if not clipboard:
                paste.config(state="disabled")
            self._more_button = touch_button(self.toolbar, "More", self._popup_more)
            self._more_button.grid(row=0, column=4, sticky="ew", padx=2, pady=2)
        else:
            touch_button(self.toolbar, f"× {n} selected", self.clear_selection).grid(
                row=0, column=0, sticky="ew", padx=2, pady=2)
            selected = self.get_selected_items()
            single_py = n == 1 and bool(selected) and selected[0].endswith(".py")
            open_label = "Open in IDLE" if single_py else "Open"
            open_cmd = self.open_with_idle if single_py else self.open_selected
            touch_button(self.toolbar, open_label, open_cmd).grid(
                row=0, column=1, sticky="ew", padx=2, pady=2)
            touch_button(self.toolbar, "Copy", self.copy_items).grid(
                row=0, column=2, sticky="ew", padx=2, pady=2)
            paste = touch_button(self.toolbar, self._paste_label(), self.paste_items)
            paste.grid(row=0, column=3, sticky="ew", padx=2, pady=2)
            if not clipboard:
                paste.config(state="disabled")
            rename = touch_button(self.toolbar, "Rename", self.rename_item)
            rename.grid(row=0, column=4, sticky="ew", padx=2, pady=2)
            if n != 1:
                rename.config(state="disabled")
            touch_button(self.toolbar, "Move", self.move_to_folder).grid(
                row=0, column=5, sticky="ew", padx=2, pady=2)
            touch_button(self.toolbar, "Delete", self.delete_items).grid(
                row=0, column=6, sticky="ew", padx=2, pady=2)
            self._more_button = touch_button(self.toolbar, "More", self._popup_more)
            self._more_button.grid(row=0, column=7, sticky="ew", padx=2, pady=2)

    def clear_selection(self):
        self.file_list.selection_set()
        self.file_list.event_generate("<<TreeviewSelect>>", when="tail")

    def _paste_label(self):
        if clipboard:
            label = f"Paste {os.path.basename(clipboard[0])}"
            if len(clipboard) > 1:
                label += f" (+{len(clipboard) - 1})"
            return label
        return "Paste"

    # -------------------------
    # Breadcrumbs
    # -------------------------
    def _rebuild_breadcrumbs(self):
        for w in self.crumb_frame.winfo_children():
            w.destroy()
        parts = []
        p = self.current_path
        while True:
            parts.append(p)
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        parts.reverse()
        shown = parts
        if len(parts) > 4:
            shown = ["…"] + parts[-4:]
        for seg in shown:
            if seg == "…":
                ttk.Button(self.crumb_frame, text="…", style="Crumb.TButton",
                           command=lambda p=parts[0]: self.load_folder(p)).pack(side="left")
                continue
            label = seg if seg == "/" else os.path.basename(seg.rstrip(os.sep)) or seg
            if seg == self.current_path:
                ttk.Label(self.crumb_frame, text=label,
                          font=F_BOLD).pack(side="left", padx=(4, 2))
            else:
                ttk.Button(self.crumb_frame, text=label, style="Crumb.TButton",
                           command=lambda p=seg: self.load_folder(p)).pack(side="left", padx=1)
                ttk.Label(self.crumb_frame, text="›",
                          font=F_UI).pack(side="left", padx=1)

    # -------------------------
    # Menus
    # -------------------------
    def _selection_action_specs(self):
        """(label, command, enabled) for every action that depends on the
        selection — the single source of truth for the context menu and the
        selection-mode More menu."""
        items = self.get_selected_items()
        n = len(items)
        has_py = any(p.endswith(".py") for p in items)
        has_zip = any(zipfile.is_zipfile(p) for p in items)
        return [
            ("Open", self.open_selected, n > 0),
            ("Open with IDLE", self.open_with_idle, has_py),
            ("Copy", self.copy_items, n > 0),
            ("Move to folder…", self.move_to_folder, n > 0),
            ("Rename…", self.rename_item, n == 1),
            ("Delete", self.delete_items, n > 0),
            ("Zip selected", self.zip_selected, n > 0),
            ("Unzip selected", self.unzip_selected, has_zip),
        ]

    def _post_menu(self, menu, x, y):
        self._active_menu = menu  # keep it alive while posted
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _popup_context(self):
        x, y = self.file_list.popup_xy()
        if x is None or y is None:
            return
        menu = self._new_menu()
        if menu is None:
            return
        added = 0
        for label, cmd, enabled in self._selection_action_specs():
            if enabled:
                menu.add_command(label=label, command=cmd)
                added += 1
            if label in ("Open with IDLE", "Delete"):
                if added > 0:
                    menu.add_separator()
                    added = 0
        menu.add_command(label="Select all", command=self.select_all)
        if len(self.file_list.selection()) > 0:
            menu.add_command(label="Clear selection", command=self.clear_selection)
        self._post_menu(menu, x, y)

    @staticmethod
    def _new_menu():
        """Create a popup menu, degrading gracefully when the Tk font engine
        cannot allocate its fonts (a transient CheerpX failure — the menu is
        simply not shown and a status message replaces it)."""
        try:
            return tk.Menu(root, tearoff=0)
        except tk.TclError:
            set_status("Menu unavailable right now")
            return None

    def _popup_more(self):
        if self._more_button is None:
            return
        menu = self._new_menu()
        if menu is None:
            return
        menu.add_command(label="Go to path…", command=self.go_to_path)
        menu.add_command(label="Select all", command=self.select_all)
        sort_menu = tk.Menu(menu, tearoff=0)
        for method in ("Name", "Type", "Date"):
            sort_menu.add_command(label=method,
                                  command=lambda m=method: sort_var.set(m))
        menu.add_cascade(label="Sort by", menu=sort_menu)
        if len(self.file_list.selection()) > 0:
            menu.add_separator()
            for label, cmd, enabled in self._selection_action_specs():
                # Open/Copy/Paste/Move/Rename/Delete are in the toolbar now.
                if label in ("Open", "Copy", "Move to folder…", "Rename…", "Delete"):
                    continue
                if enabled:
                    menu.add_command(label=label, command=cmd)
            menu.add_command(label="Clear selection", command=self.clear_selection)
        x = self._more_button.winfo_rootx()
        y = self._more_button.winfo_rooty() + self._more_button.winfo_height()
        self._post_menu(menu, x, y)

    # -------------------------
    # Load Folder Threaded
    # -------------------------
    def load_folder(self, path):
        self.current_path = path
        self.file_list.selection_set()
        self._rebuild_toolbar()
        self._rebuild_breadcrumbs()
        self._load_gen += 1
        gen = self._load_gen
        set_status(f"Loading {path}...")
        threading.Thread(target=self._thread_load_folder, args=(gen,), daemon=True).start()

    def _thread_load_folder(self, gen):
        try:
            entries = []
            for item in os.listdir(self.current_path):
                if item.startswith("."):
                    continue  # hide dotfiles throughout
                full_path = os.path.join(self.current_path, item)
                try:
                    is_dir = os.path.isdir(full_path)
                    ext = os.path.splitext(item)[1].lower() if not is_dir else ""
                    mtime = os.path.getmtime(full_path)
                    size = 0 if is_dir else os.path.getsize(full_path)
                except OSError:
                    continue
                entries.append({"name": item, "path": full_path, "is_dir": is_dir,
                                "ext": ext, "mtime": mtime, "size": size})
            if gen != self._load_gen:
                return
            self.entries_cache = entries
            self.update_ui()
            # compute folder sizes asynchronously
            for e in entries:
                if e["is_dir"]:
                    threading.Thread(target=self.compute_folder_size, args=(e["path"],), daemon=True).start()
        except OSError as err:
            root.after(0, lambda: messagebox.showerror("Error", str(err)))

    def update_ui(self):
        def _update():
            # Preserve the selection across the rebuild: lazy folder-size
            # computation refreshes the list asynchronously (one thread per
            # directory), and a rebuild must not silently clear what is
            # selected.
            selected_paths = set(self.get_selected_items())
            children = self.file_list.get_children()
            if children:
                self.file_list.delete(*children)
            self.displayed_items = []
            self.item_path = {}
            self.displayed_paths = []
            search_text = search_var.get().lower()
            entries = self.sort_entries(self.entries_cache or [])
            reselect = []
            for e in entries:
                if search_text in e["name"].lower():
                    icon = "📁 " if e["is_dir"] else "📄 "
                    iid = self.file_list.insert("", "end",
                                                text=icon + e["name"],
                                                values=(self._size_str(e),
                                                        self._mtime_str(e["mtime"])))
                    self.displayed_items.append(iid)
                    self.item_path[iid] = e["path"]
                    self.displayed_paths.append(e["path"])
                    if e["path"] in selected_paths:
                        reselect.append(iid)
            if reselect:
                self.file_list.selection_set(*reselect)
            set_status(f"Loaded {len(entries)} items in {self.current_path}")
            self._rebuild_toolbar()
        root.after(0, _update)

    def sort_entries(self, entries):
        method = sort_var.get()
        if method == "Name":
            return sorted(entries, key=lambda x: x["name"].lower())
        elif method == "Type":
            return sorted(entries, key=lambda x: (x["ext"], x["name"].lower()))
        elif method == "Date":
            return sorted(entries, key=lambda x: x["mtime"], reverse=True)
        return entries

    # -------------------------
    # Folder Size
    # -------------------------
    def compute_folder_size(self, folder_path):
        total = 0
        for root_dir, dirs, files in os.walk(folder_path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root_dir, f))
                except:
                    pass
        size_str = self.human_readable_size(total)
        self.folder_sizes[folder_path] = size_str
        self.update_ui()

    @staticmethod
    def human_readable_size(size, decimal_places=2):
        for unit in ['B','KB','MB','GB','TB']:
            if size < 1024.0:
                return f"{size:.{decimal_places}f} {unit}"
            size /= 1024.0
        return f"{size:.{decimal_places}f} PB"

    def _size_str(self, e):
        # Directories show the asynchronously computed folder size once known.
        if e["is_dir"]:
            return self.folder_sizes.get(e["path"], "")
        return self.human_readable_size(e.get("size", 0))

    @staticmethod
    def _mtime_str(mtime):
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
        except (ValueError, OSError, OverflowError):
            return ""

    # -------------------------
    # Navigation & File Operations
    # -------------------------
    def go_up(self):
        self.load_folder(os.path.dirname(self.current_path))

    def go_to_path(self):
        path = simpledialog.askstring("Go to path", "Path:",
                                      initialvalue=self.current_path)
        if path is None:
            return
        if os.path.exists(path):
            self.load_folder(path)
        else:
            messagebox.showerror("Error", "Path does not exist!")

    def get_selected_items(self):
        return [self.item_path[iid] for iid in self.file_list.selection()
                if iid in self.item_path]

    def _open_externally(self, path):
        if hasattr(os, "startfile"):
            os.startfile(path)
            return True
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.Popen([opener, path])
            return True
        # The guest image has no xdg-utils, so fall back to per-type openers.
        ext = os.path.splitext(path)[1].lower()
        if ext == ".py":
            launcher = "/usr/local/bin/idle3.10-launcher"
            if os.path.exists(launcher):
                subprocess.Popen([launcher, path])
                return True
        xterm = shutil.which("xterm")
        if xterm and self._is_text_file(path):
            viewer = shutil.which("less") or shutil.which("vi") or "more"
            subprocess.Popen([xterm, "-e", viewer, path])
            return True
        return False

    @staticmethod
    def _is_text_file(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".txt", ".md", ".log", ".csv", ".ini", ".conf", ".json",
                   ".yaml", ".yml", ".toml", ".xml", ".html", ".css", ".js",
                   ".py", ".pyw", ".sh", ".c", ".h", ".cpp", ".rs", ".go"):
            return True
        if ext:
            return False
        try:
            with open(path, "rb") as f:
                chunk = f.read(512)
        except OSError:
            return False
        if not chunk:
            return True
        return all(b in (9, 10, 13) or 32 <= b < 127 for b in chunk)

    def open_selected(self):
        items = self.get_selected_items()
        py_files = []
        for path in items:
            if os.path.isdir(path):
                self.load_folder(path)
                return
            if path.endswith(".py"):
                py_files.append(path)
                continue
            try:
                if self._open_externally(path):
                    set_status(f"Opened {os.path.basename(path)}")
                else:
                    messagebox.showerror("Error", f"No opener available for {path}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        if py_files:
            self._open_in_idle(py_files)

    def open_with_idle(self):
        items = [p for p in self.get_selected_items() if p.endswith(".py")]
        if not items:
            set_status("Select a .py file to open in IDLE")
            return
        self._open_in_idle(items)

    def _open_in_idle(self, paths):
        """Open Python files in IDLE, replacing this window for the duration.

        IDLE is the only other full-screen app on this desktop. Opening a .py
        therefore withdraws the explorer — the whole screen is handed to IDLE —
        and only restores it once IDLE has exited, reloading the folder so
        anything IDLE created/edited/saved shows up."""
        procs = []
        for path in paths:
            try:
                procs.append(subprocess.Popen(["/usr/local/bin/idle3.10-launcher", path]))
            except Exception as e:
                messagebox.showerror("Error", str(e))
        if not procs:
            set_status("No Python file opened")
            return
        set_status(f"Opened {len(procs)} file(s) in IDLE")
        root.withdraw()
        threading.Thread(target=self._wait_for_idle, args=(procs,), daemon=True).start()

    def _wait_for_idle(self, procs):
        for proc in procs:
            try:
                proc.wait()
            except Exception:
                pass
        root.after(0, self._idle_finished)

    def _idle_finished(self):
        # IDLE may have created/edited/saved files while the explorer was
        # hidden; reload the current folder before reappearing.
        self.load_folder(self.current_path)
        root.deiconify()

    def select_all(self):
        if self.displayed_items:
            self.file_list.selection_set(*self.displayed_items)
            self.file_list.event_generate("<<TreeviewSelect>>", when="tail")
            set_status(f"Selected {len(self.displayed_items)} items")

    def copy_items(self):
        global clipboard
        clipboard = self.get_selected_items()
        self._rebuild_toolbar()  # enable/disable the toolbar Paste button
        set_status(f"Copied {len(clipboard)} items")

    def _resolve_paste_dest(self, item):
        """Destination path that does not collide, prompting for a new name
        when the paste would hit an existing file (including pasting a file
        into its own folder). Returns None if the user cancels."""
        dest = os.path.join(self.current_path, os.path.basename(item))
        base, ext = os.path.splitext(os.path.basename(item))
        while os.path.exists(dest) or os.path.abspath(dest) == os.path.abspath(item):
            default = f"{base} (copy){ext}"
            name = simpledialog.askstring(
                "Name conflict",
                f"'{os.path.basename(item)}' already exists here. "
                "Enter a new name:",
                initialvalue=default)
            if not name:
                return None
            if os.sep in name:
                messagebox.showerror("Error", "Name cannot contain a path separator")
                continue
            dest = os.path.join(self.current_path, name)
        return dest

    def paste_items(self):
        global clipboard
        if not clipboard:
            return
        pasted = 0
        for item in clipboard:
            dest = self._resolve_paste_dest(item)
            if dest is None:
                continue
            try:
                if os.path.isdir(item):
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
                pasted += 1
            except Exception as e:
                messagebox.showerror("Error", str(e))
        self.load_folder(self.current_path)
        set_status(f"Pasted {pasted} items")

    def move_to_folder(self):
        items = self.get_selected_items()
        if not items:
            return
        dest = filedialog.askdirectory(title="Move selected items to…",
                                       initialdir=self.current_path)
        if not dest:
            return
        for item in items:
            try:
                shutil.move(item, os.path.join(dest, os.path.basename(item)))
            except Exception as e:
                messagebox.showerror("Error", str(e))
        self.load_folder(self.current_path)
        set_status(f"Moved {len(items)} items")

    def rename_item(self):
        items = self.get_selected_items()
        if len(items) != 1:
            set_status("Select exactly one item to rename")
            return
        old = items[0]
        new = simpledialog.askstring("Rename", "New name:",
                                     initialvalue=os.path.basename(old))
        if not new or new == os.path.basename(old):
            return
        try:
            os.rename(old, os.path.join(os.path.dirname(old), new))
            self.load_folder(self.current_path)
            set_status(f"Renamed to {new}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # -------------------------
    # Folder/File Operations
    # -------------------------
    def create_folder(self):
        name = simpledialog.askstring("Create Folder", "Enter folder name:")
        if name:
            path = os.path.join(self.current_path, name)
            try:
                os.makedirs(path)
                self.load_folder(self.current_path)
                set_status(f"Created folder {name}")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def create_file(self):
        name = simpledialog.askstring("Create File", "Enter file name:")
        if not name:
            return
        if os.sep in name:
            messagebox.showerror("Error", "File name cannot contain a path separator")
            return
        path = os.path.join(self.current_path, name)
        try:
            with open(path, "w"):
                pass
            self.load_folder(self.current_path)
            set_status(f"Created file {name}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def delete_items(self):
        items = self.get_selected_items()
        if not items:
            return
        if messagebox.askyesno("Confirm Delete", f"Delete {len(items)} items?"):
            for path in items:
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                except Exception as e:
                    messagebox.showerror("Error", str(e))
            self.load_folder(self.current_path)
            set_status("Deleted items")

    def batch_rename(self):
        items = self.get_selected_items()
        if not items:
            return
        pattern = simpledialog.askstring("Batch Rename", "Enter pattern with {num}, e.g., file_{num}.txt")
        if not pattern:
            return
        if "{num}" not in pattern:
            messagebox.showerror("Error", "Pattern must contain {num}")
            return
        renamed = 0
        for i, path in enumerate(items, 1):
            dir_path = os.path.dirname(path)
            ext = os.path.splitext(path)[1]
            new_name = pattern.replace("{num}", str(i))
            if not new_name.endswith(ext):
                new_name += ext
            new_path = os.path.join(dir_path, new_name)
            try:
                os.rename(path, new_path)
                renamed += 1
            except Exception as e:
                messagebox.showerror("Error", str(e))
        self.load_folder(self.current_path)
        set_status(f"Batch renamed {renamed} items")

    def zip_selected(self):
        items = self.get_selected_items()
        if not items:
            return
        zip_path = filedialog.asksaveasfilename(defaultextension=".zip")
        if not zip_path:
            return
        with zipfile.ZipFile(zip_path, "w") as zf:
            for item in items:
                if os.path.isdir(item):
                    for root_dir, dirs, files in os.walk(item):
                        for f in files:
                            full_path = os.path.join(root_dir, f)
                            arcname = os.path.relpath(full_path, os.path.dirname(item))
                            zf.write(full_path, arcname)
                else:
                    zf.write(item, os.path.basename(item))
        self.load_folder(self.current_path)
        set_status(f"Created archive {zip_path}")

    def unzip_selected(self):
        items = self.get_selected_items()
        if not items:
            return
        dest_base = os.path.normpath(self.current_path)
        for item in items:
            if not zipfile.is_zipfile(item):
                continue
            try:
                with zipfile.ZipFile(item, "r") as zf:
                    for member in zf.infolist():
                        target = os.path.normpath(os.path.join(dest_base, member.filename))
                        if target != dest_base and not target.startswith(dest_base + os.sep):
                            messagebox.showerror("Error", f"Unsafe path in archive: {member.filename}")
                            continue
                        zf.extract(member, self.current_path)
            except Exception as e:
                messagebox.showerror("Error", str(e))
        self.load_folder(self.current_path)
        set_status("Unzipped selected archives")

# =========================
# Layout: Single Panel
# =========================
pane = SingleTab(root, os.path.expanduser("~"))
pane.pack(fill="both", expand=True, padx=10, pady=10)

# Search & Sort traces
search_var.trace_add("write", lambda *a: pane.update_ui())
sort_var.trace_add("write", lambda *a: pane.update_ui())

# Status Bar
ttk.Label(root, textvariable=status_var, anchor="w",
          font=F_SMALL).pack(side="bottom", fill="x")

# =========================
# Run
# =========================
root.mainloop()
