#!/usr/bin/env python3
"""WebVM guest home-sync agent.

Backends: container WebDAV (stdlib urllib + PROPFIND) or Samba (pysmb).

Flow (started by /etc/local.d/desktop.start as the `user` account):

  pull   — boot pull: non-destructive, per-file manifest vs last-push record.
           Retries waiting for the tailnet up to ~90s (the guest network comes
           up only after the browser Tailscale client connects), then returns
           regardless so X is never blocked indefinitely. The pull is
           LEASE-GUARDED: a second session that holds the backend lease is
           refused (only the persistent session may pull).
  daemon — write-triggered push loop: scans ~/ every ~5s, pushes a debounced
           (~2s) delta after changes, holds the backend lease (heartbeat ~15s,
           expiry ~90s), and does a final best-effort push + lease release on
           SIGTERM (tab close / shutdown).

The pull and the daemon share one session id (persisted in the overlay home),
so the pull's lease hands over to the daemon with no gap, and an ephemeral
fallback tab (fresh overlay -> fresh id) is refused by both stages. Once a
session has held the lease it marks `.sync-owned` in its overlay; the daemon
then RETRIES after a refusal (self-healing against stale leases from crashed
sessions) instead of permanently giving up, while a session that never owned
the lease (ephemeral) yields on the first refusal and never becomes a writer.

Sync correctness (clock-safe):
  * The manifest records, per file, the BACKEND mtime observed at last push
    plus the LOCAL mtime observed at last push.
  * Pull overwrites a file only when the current backend mtime is newer than
    the backend mtime recorded at last push — same clock (the backend), so
    guest/browser vs backend clock skew cannot cause wrong overwrites.
  * Push uploads a file only when the current local mtime is newer than the
    local mtime recorded at last push.
  * Deletions are not propagated (documented limitation).

Config precedence: /opt/syncrc (runtime-injected by the page via a DataDevice)
> /home/user/.syncrc > /root/.syncrc (baked at image build).
"""

import argparse
import email.utils
import json
import os
import posixpath
import signal
import tarfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SYNC_AGENT_VERSION = 1

MANIFEST_FILE = ".sync-manifest.json"
SNAPSHOT_FILE = "snapshot.tar.gz"
LEASE_FILE = "webvm.lock"
NODE_ID_FILE = ".sync-node-id"
OWNED_FILE = ".sync-owned"
# Crash-report safety net written by the background daemon (also excluded
# from the sync — see EXCLUDE_NAMES).
CRASH_FILE = "_daemon-error.log"
LEASE_HEARTBEAT_S = 15
LEASE_EXPIRY_S = 90
LEASE_RETRY_S = 15
# Sessions that have never owned the lease (first boot, and ephemeral tabs)
# retry for about one lease-expiry window, then give up. An established owner
# (`.sync-owned` marker) retries indefinitely.
LEASE_RETRY_ATTEMPTS = 6  # 6 * LEASE_RETRY_S = 90s ~= LEASE_EXPIRY_S
POLL_S = 5
# Debounce disabled: every guest-side wait primitive is unreliable under
# CheerpX (time.sleep never fires; subprocess sleep is flaky; busy-waits
# starve the guest clock; socket-timeout sleeps hang too — verified
# 2026-08-15, plans/networking-bug.md §16). A first sync has nothing to tear
# (the home is what it is), and the push loop re-checks mtimes each cycle.
DEBOUNCE_S = 0
TAILNET_WAIT_ATTEMPTS = 12  # 12 * (3s ping + 5s) ~= 96s; keep the boot pull bounded
TAILNET_WAIT_S = 5

# Never sync volatile/private state. The .ssh keypair stays in the guest.
EXCLUDE_NAMES = {
    ".cache",
    ".ssh",
    ".Xauthority",
    ".ICEauthority",
    ".xsession-errors",
    ".pulse",
    MANIFEST_FILE,
    SNAPSHOT_FILE,
    LEASE_FILE,
    NODE_ID_FILE,
    OWNED_FILE,
    ".syncrc",
    # Crash-report safety net written by the background daemon.
    CRASH_FILE,
}


class ConfigError(Exception):
    pass


class LeaseRefused(Exception):
    pass


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

def parse_config(text):
    cfg = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cfg[key.strip()] = value.strip()
    return cfg


def load_config():
    for path in ("/opt/syncrc", "/home/user/.syncrc", "/root/.syncrc"):
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = parse_config(fh.read())
        except OSError:
            continue
        if cfg.get("backend"):
            return cfg
    return {}


def build_transport(cfg):
    backend = cfg.get("backend")
    if backend == "webdav":
        url = cfg.get("url")
        if not url:
            raise ConfigError("webdav sync config is missing 'url'")
        return WebDAVTransport(url, cfg.get("user") or "", cfg.get("password") or "")
    if backend == "samba":
        host = cfg.get("host")
        share = cfg.get("share")
        if not host or not share:
            raise ConfigError("samba sync config is missing 'host'/'share'")
        return SMBTransport(host, share, cfg.get("user") or "", cfg.get("password") or "")
    raise ConfigError("unknown sync backend: %r" % backend)


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def empty_manifest():
    return {"version": SYNC_AGENT_VERSION, "files": {}}


def load_manifest(home):
    try:
        manifest = json.loads((home / MANIFEST_FILE).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
            return empty_manifest()
        return manifest
    except (OSError, ValueError):
        return empty_manifest()


def save_manifest(home, manifest):
    tmp = home / (MANIFEST_FILE + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=0), encoding="utf-8")
    _chown_home(home, tmp)
    os.replace(tmp, home / MANIFEST_FILE)
    _chown_home(home, home / MANIFEST_FILE)


# --------------------------------------------------------------------------
# WebDAV transport (stdlib urllib; PROPFIND per-file manifest)
# --------------------------------------------------------------------------

PROPFIND_BODY = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:">'
    "<d:prop><d:getlastmodified/><d:getcontentlength/></d:prop>"
    "</d:propfind>"
).encode("utf-8")


class _SameOriginAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that drops the Authorization header when the target
    is not on the same scheme+host as the request's original URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if not req.has_header("Authorization"):
            return new
        try:
            same = urllib.parse.urlsplit(new.full_url)[:2] == urllib.parse.urlsplit(req.full_url)[:2]
        except ValueError:
            same = False
        if not same:
            new.remove_header("Authorization")
        return new


def _parse_http_date(value):
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


class WebDAVTransport:
    def __init__(self, url, user="", password=""):
        self.base = url.rstrip("/") + "/"
        self.user = user
        self.password = password
        # Never forward Basic credentials to a redirect target on a different
        # scheme/host: urllib's default redirect handler copies request
        # headers verbatim, so a redirecting (or compromised) WebDAV server
        # could leak the sync password elsewhere.
        self._opener = urllib.request.build_opener(_SameOriginAuthRedirectHandler())

    def _open(self, req, timeout=30):
        if self.user:
            import base64

            token = base64.b64encode(
                ("%s:%s" % (self.user, self.password)).encode("utf-8")
            ).decode("ascii")
            req.add_header("Authorization", "Basic " + token)
        try:
            return self._opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 405):
                raise FileNotFoundError(req.full_url)
            raise

    def _href_to_rel(self, href):
        # href is an absolute or relative URL; strip scheme/host and the
        # server-side base path (e.g. /webdav/), then leading slashes.
        path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
        base_path = urllib.parse.urlsplit(self.base).path.rstrip("/")
        if base_path and path.startswith(base_path + "/"):
            path = path[len(base_path) + 1 :]
        elif base_path and path == base_path:
            return None  # the collection root itself
        rel = path.lstrip("/")
        if not rel:
            return None
        # Reject anything that could escape the synced tree: a remote WebDAV
        # listing is untrusted input (a malicious/buggy server could advertise
        # "../../…" paths).
        parts = rel.split("/")
        if ".." in parts or "" in parts:
            return None
        return rel

    def listdir(self, path=""):
        req = urllib.request.Request(
            self.base + path, data=PROPFIND_BODY, method="PROPFIND"
        )
        # Recursive listing (Depth: infinity) so files in subdirectories are
        # part of the per-file manifest; directory entries (hrefs ending in
        # "/") are skipped below.
        req.add_header("Depth", "infinity")
        req.add_header("Content-Type", "application/xml")
        try:
            body = self._open(req).read()
        except FileNotFoundError:
            return {}
        ns = {"d": "DAV:"}
        entries = {}
        root = ET.fromstring(body)
        for response in root.findall("d:response", ns):
            href = response.findtext("d:href", "", ns)
            rel = self._href_to_rel(href)
            if rel is None or rel.endswith("/"):
                continue
            props = {}
            for propstat in response.findall("d:propstat", ns):
                prop = propstat.find("d:prop", ns)
                if prop is None:
                    continue
                lm = prop.findtext("d:getlastmodified", "", ns)
                cl = prop.findtext("d:getcontentlength", "", ns)
                if lm:
                    props["mtime"] = _parse_http_date(lm)
                if cl:
                    try:
                        props["size"] = int(cl)
                    except ValueError:
                        pass
            entries[rel] = {"mtime": props.get("mtime", 0.0), "size": props.get("size", 0)}
        return entries

    def get(self, path):
        req = urllib.request.Request(self.base + path, method="GET")
        return self._open(req).read()

    def put(self, path, data):
        req = urllib.request.Request(self.base + path, data=data, method="PUT")
        self._open(req).read()

    def mkdir(self, path):
        # MKCOL (WebDAV) — creates one collection; callers create ancestors
        # first. Ignore "already exists" (405/301) and 404-style races.
        req = urllib.request.Request(self.base + path, method="MKCOL")
        try:
            self._open(req).read()
        except (FileNotFoundError, urllib.error.HTTPError):
            pass

    def delete(self, path):
        req = urllib.request.Request(self.base + path, method="DELETE")
        try:
            self._open(req).read()
        except (FileNotFoundError, urllib.error.HTTPError):
            pass

    def ping(self):
        # Short timeout: the tailnet-wait loop calls this up to 18 times and
        # must give up quickly when the guest's data path is unusable (the
        # CheerpX guest network is currently broken upstream — a connect()
        # to a tailnet IP hangs rather than failing — see
        # plans/networking-bug.md §15). 5s keeps the boot pull bounded.
        req = urllib.request.Request(self.base, method="PROPFIND")
        req.add_header("Depth", "0")
        try:
            self._open(req, timeout=3).read()
            return True
        except Exception:
            return False


# --------------------------------------------------------------------------
# Samba transport (pysmb)
# --------------------------------------------------------------------------

class SMBTransport:
    def __init__(self, host, share, user="", password=""):
        from smb.SMBConnection import SMBConnection

        self.host = host
        self.share = share
        self.user = user
        self.conn = SMBConnection(
            user or "guest", password, "webvm", "webvm-guest", use_ntlm_v2=True, is_direct_tcp=True
        )
        self.conn.connect(host, 445, timeout=30)

    def listdir(self, path=""):
        import io

        entries = {}
        # Recursive walk so subdirectory files are part of the manifest.
        stack = [""]
        while stack:
            cur = stack.pop()
            try:
                items = self.conn.listPath(self.share, "/" + cur)
            except Exception:
                continue
            for item in items:
                if item.filename in (".", ".."):
                    continue
                rel = posixpath.join(cur, item.filename) if cur else item.filename
                if item.isDirectory:
                    stack.append(rel)
                    continue
                entries[rel] = {"mtime": item.last_write_time.timestamp(), "size": item.file_size}
        return entries

    def get(self, path):
        import io

        buf = io.BytesIO()
        self.conn.retrieveFile(self.share, "/" + path, buf)
        return buf.getvalue()

    def put(self, path, data):
        import io

        self.conn.storeFile(self.share, "/" + path, io.BytesIO(data))

    def mkdir(self, path):
        try:
            self.conn.createDirectory(self.share, "/" + path)
        except Exception:
            pass

    def delete(self, path):
        try:
            self.conn.deleteFiles(self.share, "/" + path)
        except Exception:
            pass

    def ping(self):
        return self.listdir("")


# --------------------------------------------------------------------------
# Backend lease
# --------------------------------------------------------------------------

def session_node_id(home):
    """Per-session node id, persisted in the (overlay) home directory.

    The boot pull and the push daemon are two processes, but must present ONE
    identity so the pull can hand the lease to the daemon without a window in
    which a second session could steal it. The id is persisted in the overlay
    home: a persistent tab keeps the same id across reloads (shared overlay),
    while an ephemeral tab (random overlay cacheId) gets a fresh id — so its
    pull/daemon are refused by the persistent session's lease.
    """
    path = home / NODE_ID_FILE
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    import uuid

    ident = uuid.uuid4().hex
    try:
        path.write_text(ident, encoding="utf-8")
        _chown_home(home, path)
    except OSError:
        # The overlay home is not writable (disk full / permissions). Fall back
        # to the guest hostname so the pull and the daemon (separate processes)
        # still share ONE identity and the lease handoff works — a per-process
        # uuid here would deterministically lock the daemon out of its own
        # pull's lease. (Tabs share a hostname, but a read-only home is already
        # a degenerate state; this restores the pre-session-id behaviour.)
        return os.uname().nodename
    return ident


def session_owned(home):
    """True once this session has successfully held the backend lease.

    The marker persists in the overlay home: a persistent tab that has ever
    synced keeps it across reloads, while an ephemeral fallback tab (fresh
    overlay) never has it. It decides whether the daemon may RETRY after a
    lease refusal (persistent, self-healing) or must yield immediately
    (ephemeral — it must never become the writer).
    """
    return (home / OWNED_FILE).exists()


def acquire_lease(transport, node):
    """Acquire (or refresh) the backend lease for `node`.

    A lease held by the SAME node is refreshed rather than refused: the boot
    pull acquires the lease and deliberately leaves it in place so the push
    daemon (same session id) can take it over without a gap. A lease held by
    ANY other node (a second tab, ephemeral fallback tab, or another machine)
    is refused while it is fresh.
    """
    try:
        raw = transport.get(LEASE_FILE)
        state = json.loads(raw.decode("utf-8"))
        if state.get("node") == node:
            transport.put(
                LEASE_FILE, json.dumps({"ts": time.time(), "node": node}).encode("utf-8")
            )
            return
        age = time.time() - float(state.get("ts", 0))
        if age < LEASE_EXPIRY_S:
            raise LeaseRefused(
                "backend lease held by %s (%ds old)" % (state.get("node", "?"), int(age))
            )
    except (FileNotFoundError, ValueError):
        pass
    transport.put(LEASE_FILE, json.dumps({"ts": time.time(), "node": node}).encode("utf-8"))


def refresh_lease(transport, node):
    """Refresh the lease ONLY while we still own it.

    An unconditional PUT could overwrite a lease that another session acquired
    after ours lapsed (e.g. a long-hidden tab whose guest timers stalled past
    LEASE_EXPIRY_S), silently creating two writers. If the stored lease is no
    longer ours, raise LeaseRefused so the push loop stops and re-acquires
    instead of writing without the lease.
    """
    try:
        raw = transport.get(LEASE_FILE)
        state = json.loads(raw.decode("utf-8"))
        if state.get("node") != node:
            raise LeaseRefused(
                "backend lease taken over by %s" % state.get("node", "?")
            )
    except FileNotFoundError:
        # Lease vanished (e.g. deleted by the previous owner's shutdown): we no
        # longer own it — re-acquire rather than blind-write.
        raise LeaseRefused("backend lease disappeared during refresh") from None
    except ValueError:
        pass  # corrupt lease: overwrite it below (unreadable anyway)
    transport.put(LEASE_FILE, json.dumps({"ts": time.time(), "node": node}).encode("utf-8"))


def release_lease(transport):
    transport.delete(LEASE_FILE)


# --------------------------------------------------------------------------
# File walking
# --------------------------------------------------------------------------

def _home_owner(home):
    """The uid/gid of the home directory owner (the agent may run as root
    under CheerpX; files it creates must stay owned by the user session)."""
    try:
        st = home.stat()
        return st.st_uid, st.st_gid
    except OSError:
        return None, None


def _chown_home(home, path):
    """chown a path (and its existing parent dirs) to the home owner,
    best-effort — the agent may run as root, so files it creates must stay
    owned by the `user` session."""
    uid, gid = _home_owner(home)
    if uid is None:
        return
    for p in [path] + [c for c in path.parents if c != home.parent]:
        try:
            if p.exists():
                os.chown(p, uid, gid)
        except OSError:
            pass


def scan_local(home):
    """Return {relative_path: local_mtime_epoch} for synced files.

    Symlinks are SKIPPED: the agent may run as root (CheerpX process quirks),
    so following a symlink could upload files outside the home tree — the
    synced tree must never escape /home/user.
    """
    entries = {}

    def walk(directory):
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if child.name in EXCLUDE_NAMES:
                continue
            if child.is_symlink():
                continue
            rel = child.relative_to(home).as_posix()
            if not child.is_dir():
                try:
                    entries[rel] = child.stat().st_mtime
                except OSError:
                    continue
            else:
                walk(child)

    walk(home)
    return entries


def read_excluded(target):
    parts = target.parts
    return any(name in EXCLUDE_NAMES for name in parts)


def is_safe_rel(rel):
    """True when rel is a clean relative path inside the synced tree."""
    parts = rel.replace("\\", "/").split("/")
    return not rel.startswith("/") and ".." not in parts and "" not in parts


def write_local(home, rel, data):
    if not is_safe_rel(rel):
        raise ValueError("unsafe sync path: %r" % rel)
    target = home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    _chown_home(home, target)
    return target.stat().st_mtime


def ensure_remote_parents(transport, rel):
    """MKCOL the parent collections of a remote path (WebDAV/SMB require the
    parent collection to exist before a PUT — a missing parent is a 409)."""
    parent = posixpath.dirname(rel)
    if not parent:
        return
    cur = ""
    for part in parent.split("/"):
        cur = posixpath.join(cur, part) if cur else part
        transport.mkdir(cur)


def make_snapshot(home):
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for child in sorted(home.iterdir()):
            if child.name in EXCLUDE_NAMES:
                continue
            tf.add(child, arcname=child.name, recursive=True)
    return buf.getvalue()


def extract_snapshot(home, data, skip_existing=False):
    import io

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for member in tf.getmembers():
            name = member.name.replace("\\", "/")
            parts = name.split("/")
            if (
                name.startswith("/")
                or ".." in parts
                or read_excluded(Path(member.name))
                # Never materialize symlink/hardlink members: the backend
                # could plant links that escape the synced home directory.
                or member.issym()
                or member.islnk()
            ):
                continue
            if skip_existing and (home / name).exists():
                continue
            tf.extract(member, home)
            _chown_home(home, home / name)


# --------------------------------------------------------------------------
# Pull (non-destructive)
# --------------------------------------------------------------------------

def compute_pull_plan(remote_entries, manifest, local_existing=frozenset()):
    """Which remote files to fetch. Pure decision (unit-testable).

    A file is pulled when it has no last-push record and does not exist
    locally (fresh restore), or when the backend copy is newer than the
    backend mtime recorded at last push (same clock). Existing local files
    with no record are never overwritten (first-sync protection).
    """
    plan = []
    files = manifest.get("files", {})
    for rel in sorted(remote_entries):
        if rel in (SNAPSHOT_FILE, LEASE_FILE):
            continue
        info = remote_entries[rel]
        record = files.get(rel)
        if record and float(info["mtime"]) <= float(record.get("backend_mtime", 0)):
            continue
        if not record and rel in local_existing:
            continue
        plan.append(rel)
    return plan


def pull_home(home, transport, manifest, log=print):
    remote = transport.listdir("")
    remote_files = {
        # Skip special files AND anything that would escape the synced tree or
        # touch excluded/volatile state (the remote listing is untrusted).
        rel: info
        for rel, info in remote.items()
        if rel not in (SNAPSHOT_FILE, LEASE_FILE)
        and is_safe_rel(rel)
        and not read_excluded(Path(rel))
    }

    # Files that existed BEFORE any restore — the only ones a first sync must
    # never clobber (they may be user edits made while sync was unavailable).
    local_before = set(scan_local(home))

    # First sync: restore the full snapshot tarball if there is no manifest
    # yet (non-clobbering — never overwrite those pre-existing files). The
    # per-file pass below then pulls the latest per-file copies over the
    # (possibly stale) snapshot and records the manifest — a snapshot is only
    # refreshed on the very first push, so per-file copies are newer.
    if SNAPSHOT_FILE in remote and not manifest["files"]:
        log("pull: restoring initial snapshot (non-clobbering)")
        extract_snapshot(home, transport.get(SNAPSHOT_FILE), skip_existing=True)

    local = scan_local(home)
    plan = compute_pull_plan(remote_files, manifest, local_existing=frozenset(local_before))
    for rel in plan:
        info = remote_files[rel]
        data = transport.get(rel)
        local_mtime = write_local(home, rel, data)
        manifest["files"][rel] = {
            "backend_mtime": info["mtime"],
            "local_mtime": local_mtime,
        }
        log("pull: %s" % rel)

    # Record backend mtimes for pre-existing local files that were not pulled
    # (user edits made before any successful sync). local_mtime is recorded as
    # 0 so the next push cycle PROPAGATES the edit (compute_push_plan pushes
    # when mtime > recorded local_mtime); push_home then records the true
    # mtimes after the upload. backend_mtime keeps later pulls on the backend
    # clock.
    if not manifest["files"]:
        for rel, info in remote_files.items():
            if rel in manifest["files"]:
                continue
            target = home / rel
            if rel in local_before and target.exists() and not target.is_dir():
                manifest["files"][rel] = {
                    "backend_mtime": info["mtime"],
                    "local_mtime": 0,
                }
    if plan or (not manifest["files"] and remote_files):
        save_manifest(home, manifest)


# --------------------------------------------------------------------------
# Push (write-triggered, debounced)
# --------------------------------------------------------------------------

def compute_push_plan(local_entries, manifest):
    """Which local files to upload. Pure decision (unit-testable)."""
    plan = []
    files = manifest.get("files", {})
    for rel in sorted(local_entries):
        mtime = local_entries[rel]
        record = files.get(rel)
        if not record or float(mtime) > float(record.get("local_mtime", 0)):
            plan.append(rel)
    return plan


def push_home(home, transport, manifest, log=print):
    """Push changed files; returns True when anything was pushed."""
    local = scan_local(home)
    plan = compute_push_plan(local, manifest)

    first_sync = not manifest["files"]
    if first_sync:
        log("push: uploading initial snapshot")
        transport.put(SNAPSHOT_FILE, make_snapshot(home))

    for rel in plan:
        # WebDAV/SMB require the parent collection to exist before a PUT
        # (a missing parent is a 409 Conflict).
        ensure_remote_parents(transport, rel)
        data = (home / rel).read_bytes()
        transport.put(rel, data)
        log("push: %s" % rel)

    if first_sync or plan:
        remote = transport.listdir("")
        for rel in plan:
            info = remote.get(rel)
            manifest["files"][rel] = {
                "backend_mtime": info["mtime"] if info else time.time(),
                "local_mtime": local[rel],
            }
        # Files removed locally since the last push keep their record (their
        # backend copy stays as an orphan; deletions are not propagated).
        save_manifest(home, manifest)
    return bool(plan) or first_sync


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def wait_for_tailnet(transport, log=print):
    for attempt in range(1, TAILNET_WAIT_ATTEMPTS + 1):
        try:
            if transport.ping():
                return True
        except Exception:
            pass
        log("sync: tailnet not up yet (%d/%d)" % (attempt, TAILNET_WAIT_ATTEMPTS))
        _sleep(TAILNET_WAIT_S)
    return False


def _sleep(seconds):
    # CheerpX quirks (verified 2026-08-15, plans/networking-bug.md §16):
    # Python time.sleep() timers never fire (hang forever); busybox `sleep`
    # via subprocess is flaky; busy-waits starve the guest clock; emulated
    # socket-timeout waits hang too. NO wait primitive is reliable, so the
    # sync's critical path avoids sleeping entirely (DEBOUNCE_S=0); this is
    # a best-effort wait for the non-critical loops (poll interval, lease
    # retry), which may stall under CheerpX without breaking the sync.
    # On native runtimes the socket wait errors out instantly, so the
    # remaining time is busy-waited on the (reliable) native clock.
    t0 = time.time()
    try:
        import socket
        s = socket.socket()
        s.settimeout(seconds)
        try:
            s.recv(1)
        except socket.timeout:
            pass
        except Exception:
            pass
        finally:
            s.close()
    except Exception:
        pass
    remaining = seconds - (time.time() - t0)
    if remaining > 0:
        end = time.time() + remaining
        while time.time() < end:
            pass


def cmd_pull(home, cfg, log=print):
    transport = build_transport(cfg)
    if not wait_for_tailnet(transport, log=log):
        log("sync: tailnet never came up; skipping boot pull (best-effort)")
        return 0
    # Lease-guarded boot pull: a second session (ephemeral fallback tab,
    # another machine) that holds the backend lease must be refused — only
    # the persistent session may pull. On success the lease is LEFT in place
    # for the push daemon (same session id) to take over.
    node = session_node_id(home)
    try:
        acquire_lease(transport, node)
    except LeaseRefused as exc:
        log("sync: %s — refusing to pull (another session holds the backend)" % exc)
        return 0
    except Exception as exc:
        log("sync: boot pull lease error: %s" % exc)
        return 0
    manifest = load_manifest(home)
    try:
        pull_home(home, transport, manifest, log=log)
        # Refresh the handoff to the push daemon OWNERSHIP-AWARE: re-acquire
        # if a foreign session took the lease over during a long pull (a fresh
        # foreign lease is refused, so we never blind-write over it).
        acquire_lease(transport, node)
    except LeaseRefused as exc:
        log("sync: boot pull lease taken over: %s" % exc)
    except Exception as exc:
        log("sync: boot pull failed: %s" % exc)
    return 0


def acquire_or_wait(transport, node, retryable, stop, log=print):
    """Acquire the backend lease, retrying while `retryable` and not stopped.

    An established owner (`.sync-owned` marker) retries indefinitely — a stale
    lease from a crashed session expires within LEASE_EXPIRY_S, so the owner
    self-heals instead of permanently giving up. A session that has never
    owned the lease (first boot, and ephemeral fallback tabs) retries for one
    lease-expiry window (LEASE_RETRY_ATTEMPTS), so a first boot can recover
    from a stale lease, but an ephemeral tab cannot keep probing/clobbering
    forever. The check applies to BOTH refusals and backend errors: an
    ephemeral session must never become the writer.
    """
    attempts = 0
    while not stop["flag"]:
        try:
            acquire_lease(transport, node)
            return True
        except LeaseRefused as exc:
            attempts += 1
            if attempts == 1 or attempts % 5 == 0:
                log("sync: %s — refusing to sync (another session holds the backend)" % exc)
        except Exception as exc:
            attempts += 1
            if attempts == 1 or attempts % 5 == 0:
                log("sync: backend unreachable (%s); retrying lease" % exc)
        if not retryable and attempts >= LEASE_RETRY_ATTEMPTS:
            return False
        for _ in range(LEASE_RETRY_S):
            if stop["flag"]:
                break
            _sleep(1)
    return False


def _run_push_loop(home, transport, manifest, node, stop, log=print):
    """Write-triggered push loop; returns on a clean stop.

    Raises LeaseRefused when the lease is taken over mid-run (see
    refresh_lease), so the caller re-acquires instead of writing without the
    lease. The final push + lease release run only on a clean shutdown — never
    when the lease was lost to another session.
    """
    last_refresh = 0.0
    while not stop["flag"]:
        try:
            local = scan_local(home)
            plan = compute_push_plan(local, manifest)
            if plan:
                # Debounced push: let the write settle before uploading
                # (editors can write a file over >1s; pushing mid-write would
                # upload a torn copy).
                for _ in range(DEBOUNCE_S):
                    if stop["flag"]:
                        break
                    _sleep(1)
                changed = push_home(home, transport, manifest, log=log)
                if changed:
                    log("sync: pushed changes")
            if time.time() - last_refresh >= LEASE_HEARTBEAT_S:
                refresh_lease(transport, node)  # LeaseRefused propagates
                last_refresh = time.time()
        except LeaseRefused:
            raise
        except Exception as exc:
            log("sync: push error: %s" % exc)
        for _ in range(POLL_S):
            if stop["flag"]:
                break
            _sleep(1)
    try:
        push_home(home, transport, manifest, log=log)
    except Exception as exc:
        log("sync: final push failed: %s" % exc)
    release_lease(transport)
    log("sync: lease released")


def cmd_daemon(home, cfg, log=print):
    transport = build_transport(cfg)
    node = session_node_id(home)
    retryable = session_owned(home)

    stop = {"flag": False}

    def on_term(_signum, _frame):
        stop["flag"] = True

    # CheerpX's emulated Linux may not support signal registration; the daemon
    # must not die on it.
    try:
        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
    except Exception as exc:
        log("sync: signal registration unsupported (%s); continuing" % exc)

    manifest = load_manifest(home)
    while not stop["flag"]:
        if not acquire_or_wait(transport, node, retryable, stop, log=log):
            log("sync: backend lease never became available; giving up on push loop")
            return 0
        try:
            (home / OWNED_FILE).write_text("1", encoding="utf-8")
            _chown_home(home, home / OWNED_FILE)
        except OSError:
            pass
        log("sync: lease acquired; push loop running")
        try:
            _run_push_loop(home, transport, manifest, node, stop, log)
            return 0  # clean shutdown
        except LeaseRefused:
            # Our lease was taken over while we pushed (e.g. a long-hidden tab
            # whose guest timers stalled past LEASE_EXPIRY_S). Stop pushing and
            # re-acquire — never write without the lease.
            log("sync: backend lease taken over; re-acquiring")
    return 0


def _report_crash(cfg, tb):
    """Report a background-agent crash to the backend (guest stderr is not
    forwarded to the page console)."""
    try:
        build_transport(cfg).put(CRASH_FILE, tb.encode("utf-8"))
    except Exception:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="WebVM guest home-sync agent")
    parser.add_argument("command", choices=("pull", "daemon", "both"))
    parser.add_argument("--home", default=str(Path.home()))
    args = parser.parse_args(argv)

    cfg = load_config()
    if not cfg:
        print("sync: no config (browser/none build?) — nothing to do")
        return 0
    home = Path(args.home).expanduser()

    if args.command == "pull":
        return cmd_pull(home, cfg)
    if args.command == "both":
        # One process runs the boot pull and then becomes the push daemon —
        # see sync-home.sh `both` and plans/networking-bug.md §16 (process
        # spawning and teardown are unreliable under CheerpX). BOTH phases
        # are crash-netted: a pull-phase failure (e.g. an SMB share that is
        # unreachable at boot) must not kill the process before the push
        # loop gets a chance to run.
        import traceback
        try:
            cmd_pull(home, cfg)
        except Exception:
            _report_crash(cfg, traceback.format_exc())
        try:
            return cmd_daemon(home, cfg)
        except Exception:
            _report_crash(cfg, traceback.format_exc())
            return 1
    try:
        return cmd_daemon(home, cfg)
    except Exception:
        import traceback
        _report_crash(cfg, traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
