#!/usr/bin/env python3
"""WebVM guest home-sync agent.

Backends: container WebDAV (stdlib urllib + PROPFIND) or Samba (pysmb).

Flow (a single agent process started by /etc/local.d/desktop.start as the
`user` account):

  pull   — boot pull: non-destructive, per-file manifest vs last-push record.
           Retries waiting for the tailnet up to ~90s (the guest network comes
           up only after the browser Tailscale client connects), then returns
           regardless so X is never blocked indefinitely.
  daemon — write-triggered push loop: scans ~/ every ~5s, pushes a debounced
           (~2s) delta after changes, holds the backend lease (heartbeat ~15s,
           expiry ~90s), and does a final best-effort push + lease release on
           SIGTERM (tab close / shutdown).

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
LEASE_HEARTBEAT_S = 15
LEASE_EXPIRY_S = 90
POLL_S = 5
DEBOUNCE_S = 2
TAILNET_WAIT_ATTEMPTS = 18  # 18 * 5s = 90s
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
    ".syncrc",
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
    os.replace(tmp, home / MANIFEST_FILE)


# --------------------------------------------------------------------------
# WebDAV transport (stdlib urllib; PROPFIND per-file manifest)
# --------------------------------------------------------------------------

PROPFIND_BODY = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:">'
    "<d:prop><d:getlastmodified/><d:getcontentlength/></d:prop>"
    "</d:propfind>"
).encode("utf-8")


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

    def _open(self, req):
        if self.user:
            import base64

            token = base64.b64encode(
                ("%s:%s" % (self.user, self.password)).encode("utf-8")
            ).decode("ascii")
            req.add_header("Authorization", "Basic " + token)
        try:
            return urllib.request.urlopen(req, timeout=30)
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
        return self.listdir("")


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

def acquire_lease(transport, node):
    try:
        raw = transport.get(LEASE_FILE)
        state = json.loads(raw.decode("utf-8"))
        age = time.time() - float(state.get("ts", 0))
        if age < LEASE_EXPIRY_S:
            raise LeaseRefused(
                "backend lease held by %s (%ds old)" % (state.get("node", "?"), int(age))
            )
    except (FileNotFoundError, ValueError):
        pass
    transport.put(LEASE_FILE, json.dumps({"ts": time.time(), "node": node}).encode("utf-8"))


def refresh_lease(transport, node):
    transport.put(LEASE_FILE, json.dumps({"ts": time.time(), "node": node}).encode("utf-8"))


def release_lease(transport):
    transport.delete(LEASE_FILE)


# --------------------------------------------------------------------------
# File walking
# --------------------------------------------------------------------------

def scan_local(home):
    """Return {relative_path: local_mtime_epoch} for synced files."""
    entries = {}

    def walk(directory):
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if child.name in EXCLUDE_NAMES:
                continue
            rel = child.relative_to(home).as_posix()
            if child.is_symlink() or not child.is_dir():
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
            transport.ping()
            return True
        except Exception:
            log("sync: tailnet not up yet (%d/%d)" % (attempt, TAILNET_WAIT_ATTEMPTS))
            time.sleep(TAILNET_WAIT_S)
    return False


def cmd_pull(home, cfg, log=print):
    transport = build_transport(cfg)
    if not wait_for_tailnet(transport, log=log):
        log("sync: tailnet never came up; skipping boot pull (best-effort)")
        return 0
    manifest = load_manifest(home)
    try:
        pull_home(home, transport, manifest, log=log)
    except Exception as exc:
        log("sync: boot pull failed: %s" % exc)
    return 0


def cmd_daemon(home, cfg, log=print):
    transport = build_transport(cfg)
    node = os.uname().nodename
    # The tailnet may still be coming up; retry lease acquisition briefly.
    acquired = False
    for _attempt in range(6):
        try:
            acquire_lease(transport, node)
            acquired = True
            break
        except LeaseRefused as exc:
            log("sync: %s — refusing to sync (another session holds the backend)" % exc)
            return 0
        except Exception:
            time.sleep(10)
    if not acquired:
        log("sync: backend unreachable; giving up on push loop")
        return 0

    log("sync: lease acquired; push loop running")
    stop = {"flag": False}

    def on_term(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    last_refresh = 0.0
    manifest = load_manifest(home)
    try:
        while not stop["flag"]:
            try:
                changed = push_home(home, transport, manifest, log=log)
                if changed:
                    log("sync: pushed changes")
                if time.time() - last_refresh >= LEASE_HEARTBEAT_S:
                    refresh_lease(transport, node)
                    last_refresh = time.time()
            except Exception as exc:
                log("sync: push error: %s" % exc)
            for _ in range(POLL_S):
                if stop["flag"]:
                    break
                time.sleep(1)
    finally:
        try:
            push_home(home, transport, manifest, log=log)
        except Exception as exc:
            log("sync: final push failed: %s" % exc)
        release_lease(transport)
        log("sync: lease released")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="WebVM guest home-sync agent")
    parser.add_argument("command", choices=("pull", "daemon"))
    parser.add_argument("--home", default=str(Path.home()))
    args = parser.parse_args(argv)

    cfg = load_config()
    if not cfg:
        print("sync: no config (browser/none build?) — nothing to do")
        return 0
    home = Path(args.home).expanduser()

    if args.command == "pull":
        return cmd_pull(home, cfg)
    return cmd_daemon(home, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
