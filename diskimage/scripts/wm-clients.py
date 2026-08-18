#!/usr/bin/env python3
"""List the window manager's client windows, with their class and title.

Openbox is an EWMH-compliant window manager but (unlike i3) has no
`i3-msg -t get_tree`-style IPC to enumerate windows, so the keep-alive daemon
and the file explorer used to rely on i3's tree now use this helper instead.
A window manager that is EWMH-conformant maintains the `_NET_CLIENT_LIST`
root property: the window IDs of every client window it manages. A withdrawn
(unmapped) window — e.g. the file explorer while IDLE/the viewer is shown —
is dropped from that list, exactly as it used to disappear from the i3 tree.

This helper reads that list and, for each window, its WM_CLASS (instance/
class) and its UTF-8 title (`_NET_WM_NAME`, falling back to `WM_NAME`), so
callers can count windows or match by class/title just as they matched the i3
tree. It shells out to `xprop` (from the xorg-xprop / xprop package) because
that is the smallest available X tool on this minimal guest.

Usage:
    wm-clients.py --count        # print the number of client windows
    wm-clients.py --json         # print [{id, instance, class, name}, ...]
    wm-clients.py                # same as --json

On any X/`xprop` failure this prints nothing useful and exits non-zero (for
--count it prints nothing, mirroring the old `i3-msg` failure contract, which
the keep-alive treats as "do not relaunch"). A window list that cannot be
read is reported as an error, never as an empty desktop.
"""

import json
import re
import subprocess
import sys

WIN_ATOMS = ("_NET_WM_NAME", "WM_NAME", "_NET_CLIENT_LIST")


def _xprop(*args):
    """Run xprop and return its stdout text, or None on failure."""
    try:
        return subprocess.check_output(["xprop", *args],
                                       stderr=subprocess.DEVNULL).decode(
                                           "utf-8", "replace")
    except Exception:
        return None


def _parse_atoms(text):
    """Parse xprop -id output into {ATOM: raw-value} dict.

    Each line looks like one of:
        ATOM(STRING) = "value"
        ATOM(UTF8_STRING) = "value"
        ATOM(WINDOW) = 0x2a00001
        ATOM(CARDINAL) = 12345

    The value after the '=' is kept RAW (quotes intact), because WM_CLASS
    carries TWO quoted tokens ("instance", "class") that a single generic
    unquoter would mangle. Callers unquote/parse the specific atoms they need.
    A UTF8 title that xprop wraps across two lines is joined here (the
    continuation line has no 'ATOM(TYPE) =' prefix, so it is appended to the
    previous value).
    """
    result = {}
    if not text:
        return result
    last_atom = None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"([A-Za-z_0-9]+)\(([^)]*)\)\s*=\s*(.*)$", line)
        if m:
            last_atom = m.group(1)
            result[last_atom] = m.group(3).strip()
        elif last_atom is not None and line:
            # Continuation of a multi-line UTF8 string value.
            result[last_atom] = result.get(last_atom, "") + "\n" + line
    return result


def _unquote(value):
    """Strip one pair of surrounding double quotes from an xprop value."""
    value = value.strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    return value


def _parse_class(value):
    """Extract (instance, class) from an xprop WM_CLASS value.

    Handles "instance", "class" and instance,class (unquoted) forms.
    """
    value = value.strip()
    m = re.match(r'\s*"([^"]*)"\s*,\s*"([^"]*)"', value)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"\s*([^,\s]+)\s*,\s*([^,\s]+)", value)
    if m:
        return m.group(1), m.group(2)
    return "", ""


def _client_list():
    """Return the list of managed window IDs (ints), or None on failure.

    Mirrors the old i3 contract: when the WM is NOT running (the
    `_NET_CLIENT_LIST` root property is absent — xprop reports "not found"),
    None is returned so callers treat it as "cannot read the tree" rather
    than as a legitimate empty desktop. A present-but-empty list (a WM with
    no clients) still yields [].
    """
    text = _xprop("-root", "_NET_CLIENT_LIST")
    if text is None:
        return None
    low = text.lower()
    if "not found" in low or "no such atom" in low or not text.strip():
        return None
    ids = []
    for m in re.finditer(r"0x[0-9a-fA-F]+", text):
        try:
            ids.append(int(m.group(0), 16))
        except ValueError:
            continue
    return ids


def main(argv):
    want_count = "--count" in argv
    ids = _client_list()
    if ids is None:
        return 1

    windows = []
    for wid in ids:
        info = _parse_atoms(_xprop("-id", "0x%x" % wid) or "")
        name = _unquote(info.get("_NET_WM_NAME") or info.get("WM_NAME") or "") or ""
        instance, cls = _parse_class(info.get("WM_CLASS", ""))
        windows.append({"id": wid, "instance": instance,
                        "class": cls, "name": name})

    if want_count:
        print(len(windows))
        return 0

    print(json.dumps(windows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
