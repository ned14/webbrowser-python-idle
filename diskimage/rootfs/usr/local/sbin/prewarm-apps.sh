#!/bin/sh
# prewarm-apps.sh — Warm the CheerpX emulator's exec/loader path and the
# byte-range block cache for the desktop apps (file explorer, file viewer,
# IDLE) so later launches are fast.
#
# Rationale: the deptree interpose (faccessat-fix.c rc_deptree_update_needed)
# removes openrc's ~20 s "Caching service dependencies" loop, and that loop's
# child-process/exec churn was what incidentally warmed the emulator before
# the X session. Instead of burning CPU pointlessly, warm up what the desktop
# actually uses:
#   * importing a module reads its .pyc blocks once (network fetch from the
#     byte-range server) and serves every later read from cache;
#   * exercising Tk's X connection path before the session means the
#     explorer's first window is not a cold path.
#
# Runs as root (from desktop.start) AFTER Xorg is up, BEFORE the user
# session. Every step is bounded by timeout and failures are non-fatal — this
# must never delay the desktop indefinitely.
set -u
export DISPLAY=:0
export HOME=/home/user
export XDG_RUNTIME_DIR=/run/user/1000
export PYTHONPATH=/usr/local/bin

echo "prewarming desktop apps..." >/dev/console

# 1. Explorer + viewer module set (imports do not need a display). Reads the
#    .pyc blocks for tkinter, PIL, mistune, file_types into the cache.
timeout 15 python3 -c "
import tkinter
from tkinter import font, ttk, simpledialog, filedialog, messagebox
import file_types
from PIL import Image, ImageOps, ImageSequence, ImageTk
import mistune
" >/dev/null 2>&1 || echo "prewarm: explorer/viewer modules skipped" >/dev/console

# 2. IDLE's module set — the heaviest import in the guest (idlelib.pyshell
#    pulls in rpc, tkinter, editor, etc.).
timeout 20 python3 -c "
import idlelib.pyshell
import idlelib.editor
import idlelib.remote
" >/dev/null 2>&1 || echo "prewarm: idlelib modules skipped" >/dev/console

# 3. Warm the Tk -> X connection path with a withdrawn root (no window
#    churn): this is the cold path the explorer hits at session start.
timeout 10 python3 -c "
import tkinter as tk
r = tk.Tk()
r.withdraw()
r.update_idletasks()
r.destroy()
" >/dev/null 2>&1 || echo "prewarm: tk-x-connect skipped" >/dev/console

echo "prewarm done" >/dev/console
exit 0
