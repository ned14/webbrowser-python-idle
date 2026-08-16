#!/usr/bin/env python3
"""File-type constants shared by the file explorer and the Tk viewer.

Single source of truth: routing (explorer "Open") and rendering (viewer)
must agree on what is an image, what is text, and what is Markdown — a
divergence between the two copies silently breaks "Open" for whole file
types (e.g. .j2k is supported by the viewer's Pillow path but was missing
from the explorer's router).

.py is deliberately absent from every set: IDLE owns Python files.
"""

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff",
              ".tif", ".ico", ".ppm", ".pgm", ".pbm", ".jp2", ".j2k", ".pcx",
              ".tga"}
MARKDOWN_EXTS = {".md", ".markdown"}
TEXT_EXTS = {".txt", ".log", ".csv", ".ini", ".conf", ".json", ".yaml",
             ".yml", ".toml", ".xml", ".html", ".css", ".js", ".sh", ".c",
             ".h", ".cpp", ".rs", ".go"}
# What the explorer routes to the viewer as text (markdown renders as
# markdown; every other text type renders as plain text).
ALL_TEXT_EXTS = TEXT_EXTS | MARKDOWN_EXTS
