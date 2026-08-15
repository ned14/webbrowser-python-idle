#!/usr/bin/env python3
"""Unit tests for the guest sync agent (diskimage/sync/sync.py).

Pure logic (config parsing, pull/push plans, manifest) plus the WebDAV
transport against the in-process fake server fixture.
"""

import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

SYNC_PY = Path(__file__).resolve().parents[2] / "diskimage" / "sync" / "sync.py"
sys.path.insert(0, str(SYNC_PY.parent))

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
sys.path.insert(0, str(FIXTURES))

import sync  # noqa: E402
from fake_webdav import FakeWebDAVServer  # noqa: E402


# --------------------------------------------------------------------------
# Config parsing
# --------------------------------------------------------------------------

class TestParseConfig:
    def test_key_value_and_comments(self):
        text = (
            "# comment\n"
            "backend = webdav\n"
            "url = http://100.64.0.1:8082/webdav/\n"
            "user=webdav\n"
            "  password = s3cret  \n"
        )
        cfg = sync.parse_config(text)
        assert cfg == {
            "backend": "webdav",
            "url": "http://100.64.0.1:8082/webdav/",
            "user": "webdav",
            "password": "s3cret",
        }

    def test_empty_and_garbage(self):
        assert sync.parse_config("") == {}
        assert sync.parse_config("not-a-key-value\n# only comment\n") == {}


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

class TestManifest:
    def test_roundtrip(self, tmp_path):
        manifest = {"version": 1, "files": {"a.txt": {"backend_mtime": 10.0, "local_mtime": 20.0}}}
        sync.save_manifest(tmp_path, manifest)
        loaded = sync.load_manifest(tmp_path)
        assert loaded == manifest

    def test_missing_manifest(self, tmp_path):
        assert sync.load_manifest(tmp_path) == {"version": 1, "files": {}}

    def test_corrupt_manifest(self, tmp_path):
        (tmp_path / sync.MANIFEST_FILE).write_text("{not json")
        assert sync.load_manifest(tmp_path) == {"version": 1, "files": {}}


# --------------------------------------------------------------------------
# Pull plan (non-destructive)
# --------------------------------------------------------------------------

class TestPullPlan:
    def test_pulls_new_backend_files(self):
        remote = {"a.txt": {"mtime": 100.0}}
        manifest = {"files": {}}
        assert sync.compute_pull_plan(remote, manifest) == ["a.txt"]

    def test_skips_files_not_newer_than_last_push(self):
        remote = {"a.txt": {"mtime": 100.0}}
        manifest = {"files": {"a.txt": {"backend_mtime": 100.0}}}
        assert sync.compute_pull_plan(remote, manifest) == []

    def test_pulls_backend_newer_than_last_push(self):
        remote = {"a.txt": {"mtime": 101.0}}
        manifest = {"files": {"a.txt": {"backend_mtime": 100.0}}}
        assert sync.compute_pull_plan(remote, manifest) == ["a.txt"]

    def test_never_resurrects_deleted_local_files(self):
        # A backend file whose last-push record equals its current mtime is
        # NOT pulled back even though the local copy is gone (deletions are
        # not propagated in either direction).
        remote = {"a.txt": {"mtime": 50.0}}
        manifest = {"files": {"a.txt": {"backend_mtime": 50.0}}}
        assert sync.compute_pull_plan(remote, manifest) == []


# --------------------------------------------------------------------------
# Push plan
# --------------------------------------------------------------------------

class TestPushPlan:
    def test_pushes_unknown_files(self):
        local = {"a.txt": 100.0}
        manifest = {"files": {}}
        assert sync.compute_push_plan(local, manifest) == ["a.txt"]

    def test_skips_unchanged_files(self):
        local = {"a.txt": 100.0}
        manifest = {"files": {"a.txt": {"local_mtime": 100.0}}}
        assert sync.compute_push_plan(local, manifest) == []

    def test_pushes_locally_modified_files(self):
        local = {"a.txt": 101.0}
        manifest = {"files": {"a.txt": {"local_mtime": 100.0}}}
        assert sync.compute_push_plan(local, manifest) == ["a.txt"]

    def test_excludes_volatile_state(self):
        home = Path(tempfile.mkdtemp())
        try:
            (home / "keep.txt").write_text("x")
            (home / ".cache").mkdir()
            (home / ".cache" / "big").write_text("y")
            (home / ".ssh").mkdir()
            (home / ".ssh" / "id_ed25519").write_text("key")
            local = sync.scan_local(home)
            assert "keep.txt" in local
            assert not any(k.startswith(".cache") for k in local)
            assert not any(k.startswith(".ssh") for k in local)
        finally:
            shutil.rmtree(home)


# --------------------------------------------------------------------------
# WebDAV transport against the fake server
# --------------------------------------------------------------------------

class TestWebDAVTransport:
    @pytest.fixture()
    def server(self):
        srv = FakeWebDAVServer(user="webdav", password="secret")
        srv.start()
        yield srv
        srv.stop()

    def test_list_put_get_delete(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        t.put("b.txt", b"hello world")
        entries = t.listdir()
        assert "b.txt" in entries
        assert entries["b.txt"]["size"] == 11
        assert t.get("b.txt") == b"hello world"
        t.delete("b.txt")
        assert "b.txt" not in t.listdir()

    def test_put_then_list_mtime(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        t.put("m.txt", b"x")
        entries = t.listdir()
        assert entries["m.txt"]["mtime"] > 0

    def test_auth_required(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "wrong")
        with pytest.raises(Exception):
            t.put("a.txt", b"x")

    def test_ping_empty(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        # ping returns a boolean (short-timeout reachability probe; the
        # tailnet-wait loop consumes it — see sync.py wait_for_tailnet).
        assert t.ping() is True


# --------------------------------------------------------------------------
# Lease
# --------------------------------------------------------------------------

class TestLease:
    @pytest.fixture()
    def server(self):
        srv = FakeWebDAVServer(user="webdav", password="secret")
        srv.start()
        yield srv
        srv.stop()

    def test_acquire_and_refuse(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        sync.acquire_lease(t, "node-a")
        with pytest.raises(sync.LeaseRefused):
            sync.acquire_lease(t, "node-b")
        sync.release_lease(t)
        sync.acquire_lease(t, "node-b")  # released -> can re-acquire

    def test_stale_lease_expires(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        server.put_file(sync.LEASE_FILE, b'{"ts": 1.0, "node": "old"}', mtime=time.time() - 1000)
        sync.acquire_lease(t, "node-c")  # old lease is stale -> overwritten

    def test_same_node_acquire_refreshes_not_refuses(self, server):
        # The boot pull leaves its lease for the push daemon (same session
        # id): a second acquire by the SAME node must refresh, not refuse.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        sync.acquire_lease(t, "node-a")
        sync.acquire_lease(t, "node-a")  # no LeaseRefused
        state = sync.json.loads(t.get(sync.LEASE_FILE).decode("utf-8"))
        assert state["node"] == "node-a"


# --------------------------------------------------------------------------
# Snapshot round-trip
# --------------------------------------------------------------------------

class TestSnapshot:
    def test_make_and_extract(self, tmp_path):
        (tmp_path / "keep.txt").write_text("hello")
        (tmp_path / ".cache").mkdir()
        (tmp_path / ".cache" / "junk").write_text("junk")
        data = sync.make_snapshot(tmp_path)
        dest = tmp_path / "restored"
        dest.mkdir()
        sync.extract_snapshot(dest, data)
        assert (dest / "keep.txt").read_text() == "hello"
        assert not (dest / ".cache").exists()

    def test_extract_rejects_path_traversal(self, tmp_path):
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("../evil.txt")
            data = b"evil"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        dest = tmp_path / "restored"
        dest.mkdir()
        sync.extract_snapshot(dest, buf.getvalue())
        assert not (tmp_path / "evil.txt").exists()

    def test_extract_rejects_symlink_and_hardlink_members(self, tmp_path):
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            # Symlink member pointing outside the home
            link = tarfile.TarInfo("escaped-link")
            link.type = tarfile.SYMTYPE
            link.linkname = "/tmp/escape-target"
            tf.addfile(link)
            # Hardlink member
            hard = tarfile.TarInfo("hard-link")
            hard.type = tarfile.LNKTYPE
            hard.linkname = "/etc/passwd"
            tf.addfile(hard)
        dest = tmp_path / "restored"
        dest.mkdir()
        sync.extract_snapshot(dest, buf.getvalue())
        assert not (dest / "escaped-link").exists()
        assert not (dest / "hard-link").exists()
        assert not Path("/tmp/escape-target").exists()


# --------------------------------------------------------------------------
# End-to-end pull/push through the fake backend
# --------------------------------------------------------------------------

class TestSyncRoundTrip:
    @pytest.fixture()
    def server(self):
        srv = FakeWebDAVServer(user="webdav", password="secret")
        srv.start()
        yield srv
        srv.stop()

    def test_push_then_pull_roundtrip(self, tmp_path, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        (tmp_path / "work.txt").write_text("version 1")
        manifest = sync.empty_manifest()

        sync.push_home(tmp_path, t, manifest)
        assert "work.txt" in t.listdir()
        # Backend copy now newer than last push -> NOT pulled back
        plan = sync.compute_pull_plan(t.listdir(), manifest)
        assert plan == []

        # Modify the file locally -> pushed again
        (tmp_path / "work.txt").write_text("version 2")
        sync.push_home(tmp_path, t, manifest)
        assert t.get("work.txt") == b"version 2"

        # Pull into a fresh home (initial snapshot restore)
        fresh = tmp_path / "fresh"
        fresh.mkdir()
        fresh_manifest = sync.empty_manifest()
        sync.pull_home(fresh, t, fresh_manifest)
        assert (fresh / "work.txt").read_text() == "version 2"

    def test_nested_files_roundtrip(self, tmp_path, server):
        # Files in subdirectories must be part of the per-file manifest
        # (recursive PROPFIND / listPath), not just root-level files.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "notes.txt").write_text("deep")
        (tmp_path / "code" / "app").mkdir(parents=True)
        (tmp_path / "code" / "app" / "main.py").write_text("print(1)")
        manifest = sync.empty_manifest()

        sync.push_home(tmp_path, t, manifest)
        listing = t.listdir()
        assert "docs/notes.txt" in listing
        assert "code/app/main.py" in listing
        # Real backend mtimes recorded (not a guest-clock fallback)
        assert manifest["files"]["docs/notes.txt"]["backend_mtime"] > 0
        assert manifest["files"]["code/app/main.py"]["backend_mtime"] > 0

        # Pull into a fresh home restores the nested tree
        fresh = tmp_path / "fresh"
        fresh.mkdir()
        sync.pull_home(fresh, t, sync.empty_manifest())
        assert (fresh / "docs" / "notes.txt").read_text() == "deep"
        assert (fresh / "code" / "app" / "main.py").read_text() == "print(1)"

    def test_first_sync_does_not_clobber_local_edits(self, tmp_path, server):
        # If the user edited a file before any successful sync (no manifest),
        # the snapshot restore and per-file pass must NOT overwrite it.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        (tmp_path / "kept.txt").write_text("user edit")
        now = time.time()
        os.utime(tmp_path / "kept.txt", (now, now))
        # Backend already holds an older copy (snapshot + per-file)
        server.put_file("kept.txt", b"old backend copy", mtime=time.time() - 7200)
        manifest = sync.empty_manifest()

        sync.pull_home(tmp_path, t, manifest)
        assert (tmp_path / "kept.txt").read_text() == "user edit"
        # The manifest records backend_mtime (backend clock) and local_mtime 0
        # so the edit is PROPAGATED on the next push cycle.
        assert "kept.txt" in manifest["files"]
        assert manifest["files"]["kept.txt"]["local_mtime"] == 0
        sync.push_home(tmp_path, t, manifest)
        assert t.get("kept.txt") == b"user edit"
        assert manifest["files"]["kept.txt"]["local_mtime"] == pytest.approx(now, abs=5)

    def test_pull_does_not_clobber_newer_local_edit(self, tmp_path, server):
        # A crash after local edits must not be clobbered: the backend file is
        # NOT newer than the last-push record (same backend clock), so a newer
        # local edit survives the pull.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        server.put_file("f.txt", b"pushed version", mtime=time.time() - 3600)
        manifest = sync.empty_manifest()
        manifest["files"]["f.txt"] = {"backend_mtime": time.time() - 3600, "local_mtime": 0.0}
        (tmp_path / "f.txt").write_text("new local edit")
        now = time.time()
        os.utime(tmp_path / "f.txt", (now, now))
        sync.pull_home(tmp_path, t, manifest)
        assert (tmp_path / "f.txt").read_text() == "new local edit"

    def test_rejects_path_traversal_in_remote_listing(self, server):
        # A malicious/buggy WebDAV server advertising "../../…" paths must not
        # write outside the home, and excluded names must not be pulled.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        server.put_file("../../escape.txt", b"evil", mtime=time.time())
        server.put_file(".ssh/authorized_keys", b"ssh-rsa evil", mtime=time.time())
        server.put_file("ok.txt", b"fine", mtime=time.time())

        home = Path(tempfile.mkdtemp())
        try:
            manifest = sync.empty_manifest()
            sync.pull_home(home, t, manifest)
            assert not (home.parent / "escape.txt").exists()
            assert not (home / ".ssh" / "authorized_keys").exists()
            assert (home / "ok.txt").read_text() == "fine"
            assert "ok.txt" in manifest["files"]
        finally:
            shutil.rmtree(home)

    def test_write_local_rejects_unsafe_paths(self, tmp_path):
        for rel in ("../evil", "/abs", "a/../../b", "a//b"):
            with pytest.raises(ValueError):
                sync.write_local(tmp_path, rel, b"x")
        sync.write_local(tmp_path, "sub/file.txt", b"x")
        assert (tmp_path / "sub" / "file.txt").read_text() == "x"

    def test_pull_applies_external_backend_update(self, tmp_path, server):
        # A file the backend changed after our last push IS pulled.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        server.put_file("g.txt", b"external update", mtime=time.time())
        manifest = sync.empty_manifest()
        manifest["files"]["g.txt"] = {"backend_mtime": time.time() - 3600, "local_mtime": 0.0}
        sync.pull_home(tmp_path, t, manifest)
        assert (tmp_path / "g.txt").read_text() == "external update"


# --------------------------------------------------------------------------
# Session node id + lease-guarded boot pull
# --------------------------------------------------------------------------

class TestSessionNodeId:
    def test_stable_within_home(self, tmp_path):
        assert sync.session_node_id(tmp_path) == sync.session_node_id(tmp_path)
        assert (tmp_path / sync.NODE_ID_FILE).exists()

    def test_differs_across_homes(self, tmp_path):
        h1 = tmp_path / "a"
        h2 = tmp_path / "b"
        h1.mkdir()
        h2.mkdir()
        assert sync.session_node_id(h1) != sync.session_node_id(h2)

    def test_excluded_from_sync(self, tmp_path):
        (tmp_path / sync.NODE_ID_FILE).write_text("abc", encoding="utf-8")
        assert sync.NODE_ID_FILE not in sync.scan_local(tmp_path)
        assert sync.NODE_ID_FILE in sync.EXCLUDE_NAMES

    def test_readonly_home_falls_back_to_stable_hostname(self, tmp_path, monkeypatch):
        import os as _os

        def _fail_write(*args, **kwargs):
            raise OSError("read-only home")

        monkeypatch.setattr(Path, "write_text", _fail_write)
        ident = sync.session_node_id(tmp_path)
        # The pull and the daemon are separate processes; the fallback must be
        # the SAME value for both, or the daemon is refused by its own pull's
        # lease (deterministic lock-out).
        assert ident == _os.uname().nodename
        assert sync.session_node_id(tmp_path) == ident

    def test_owned_marker(self, tmp_path):
        assert sync.session_owned(tmp_path) is False
        (tmp_path / sync.OWNED_FILE).write_text("1", encoding="utf-8")
        assert sync.session_owned(tmp_path) is True
        assert sync.OWNED_FILE in sync.EXCLUDE_NAMES


class TestLeaseGuardedPull:
    @pytest.fixture()
    def server(self):
        srv = FakeWebDAVServer(user="webdav", password="secret")
        srv.start()
        yield srv
        srv.stop()

    @staticmethod
    def _cfg(server):
        return {"backend": "webdav", "url": server.url(), "user": "webdav", "password": "secret"}

    def test_pull_refused_when_another_session_holds_lease(self, tmp_path, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        sync.acquire_lease(t, "another-session")
        server.put_file("f.txt", b"backend", mtime=time.time())
        logs = []
        rc = sync.cmd_pull(tmp_path, self._cfg(server), log=logs.append)
        assert rc == 0
        assert not (tmp_path / "f.txt").exists()  # not pulled
        assert any("another session holds the backend" in line for line in logs)

    def test_pull_acquires_lease_and_pulls(self, tmp_path, server):
        server.put_file("f.txt", b"backend", mtime=time.time())
        rc = sync.cmd_pull(tmp_path, self._cfg(server), log=lambda _m: None)
        assert rc == 0
        assert (tmp_path / "f.txt").read_bytes() == b"backend"
        # The lease is LEFT for the push daemon (same session id) AND refreshed
        # after the pull so the handoff is fresh (not about to expire).
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        state = sync.json.loads(t.get(sync.LEASE_FILE).decode("utf-8"))
        assert state["node"] == sync.session_node_id(tmp_path)
        assert time.time() - float(state["ts"]) < 30


class TestAcquireOrWait:
    @pytest.fixture()
    def server(self):
        srv = FakeWebDAVServer(user="webdav", password="secret")
        srv.start()
        yield srv
        srv.stop()

    def test_free_lease_acquired(self, server):
        stop = {"flag": False}
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        assert sync.acquire_or_wait(t, "node-a", retryable=False, stop=stop, log=lambda _m: None) is True

    def test_unowned_session_gives_up_after_retry_window(self, server, monkeypatch):
        monkeypatch.setattr(sync, "LEASE_RETRY_S", 0)
        stop = {"flag": False}
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        sync.acquire_lease(t, "other-session")
        calls = {"n": 0}
        orig = sync.acquire_lease

        def spy(tp, node):
            calls["n"] += 1
            return orig(tp, node)

        monkeypatch.setattr(sync, "acquire_lease", spy)
        logs = []
        # A session that never owned the lease retries for one lease-expiry
        # window (covers a first boot recovering from a stale lease) then gives
        # up — it must not retry/clobber forever.
        assert sync.acquire_or_wait(t, "node-a", retryable=False, stop=stop, log=logs.append) is False
        assert calls["n"] == sync.LEASE_RETRY_ATTEMPTS
        # Throttled: the log fires on the first attempt and every 5th.
        assert len(logs) == 2

    def test_owned_session_waits_beyond_retry_window(self, server, monkeypatch):
        monkeypatch.setattr(sync, "LEASE_RETRY_S", 0)
        stop = {"flag": False}
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        sync.acquire_lease(t, "other-session")
        calls = {"n": 0}
        orig = sync.acquire_lease

        def spy(tp, node):
            calls["n"] += 1
            if calls["n"] >= sync.LEASE_RETRY_ATTEMPTS + 2:
                stop["flag"] = True  # stop after proving it waited past the window
            return orig(tp, node)

        monkeypatch.setattr(sync, "acquire_lease", spy)
        logs = []
        # An established owner retries indefinitely (here until stop).
        assert sync.acquire_or_wait(t, "node-a", retryable=True, stop=stop, log=logs.append) is False
        assert calls["n"] > sync.LEASE_RETRY_ATTEMPTS

    def test_owned_session_acquires_after_stale_lease_expires(self, server, monkeypatch):
        monkeypatch.setattr(sync, "LEASE_RETRY_S", 0)
        stop = {"flag": False}
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        server.put_file(sync.LEASE_FILE, b'{"ts": 1.0, "node": "crashed"}', mtime=time.time() - 1000)
        assert sync.acquire_or_wait(t, "node-a", retryable=True, stop=stop, log=lambda _m: None) is True


class TestLeaseRefreshOwnership:
    @pytest.fixture()
    def server(self):
        srv = FakeWebDAVServer(user="webdav", password="secret")
        srv.start()
        yield srv
        srv.stop()

    def test_refresh_ok_when_own_lease(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        sync.acquire_lease(t, "node-a")
        sync.refresh_lease(t, "node-a")  # no exception
        state = sync.json.loads(t.get(sync.LEASE_FILE).decode("utf-8"))
        assert state["node"] == "node-a"

    def test_refresh_refuses_when_lease_taken_over(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        sync.acquire_lease(t, "node-a")
        # Simulate another session taking the lease over (e.g. after node-a's
        # lease lapsed while its tab was hidden) by overwriting the lease file.
        server.put_file(sync.LEASE_FILE, ('{"ts": %f, "node": "node-b"}' % time.time()).encode("utf-8"))
        # node-a must NOT blind-refresh over node-b's fresh lease.
        with pytest.raises(sync.LeaseRefused):
            sync.refresh_lease(t, "node-a")
        state = sync.json.loads(t.get(sync.LEASE_FILE).decode("utf-8"))
        assert state["node"] == "node-b"  # the interloper's lease survives

    def test_refresh_refuses_when_lease_missing(self, server):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        sync.acquire_lease(t, "node-a")
        sync.release_lease(t)
        with pytest.raises(sync.LeaseRefused):
            sync.refresh_lease(t, "node-a")


# --------------------------------------------------------------------------
# SMB transport (pysmb behind an interface mock)
# --------------------------------------------------------------------------

class TestSMBTransport:
    @staticmethod
    def _inject_fake_smb(monkeypatch, files):
        """Provide a fake `smb.SMBConnection` module so SMBTransport can be
        exercised without pysmb installed.

        files: {relpath: (is_dir, mtime_epoch, size, data)}
        """
        import types

        class FakeFile:
            def __init__(self, name, is_dir, mtime, size):
                self.filename = name
                self.isDirectory = is_dir
                self.last_write_time = datetime.fromtimestamp(mtime, tz=timezone.utc)
                self.file_size = size

        class FakeSMBConnection:
            instances = []
            SEED = {rel: dict(zip(("is_dir", "mtime", "size", "data"), info))
                    for rel, info in files.items()}

            def __init__(self, user, password, *args, **kwargs):
                self.files = {rel: dict(info) for rel, info in self.SEED.items()}
                self.created = []
                self.deleted = []
                FakeSMBConnection.instances.append(self)

            def connect(self, host, port, timeout):
                pass

            def listPath(self, share, path):
                prefix = path.strip("/")
                out = []
                for rel, info in self.files.items():
                    if os.path.dirname(rel) == prefix:
                        out.append(FakeFile(os.path.basename(rel), info["is_dir"], info["mtime"], info["size"]))
                    # Synthesize directory entries so the recursive walk descends
                    # (SMBTransport.listdir only pushes directories it sees).
                    parent = os.path.dirname(rel)
                    if parent and os.path.dirname(parent) == prefix:
                        name = os.path.basename(parent)
                        if not any(e.filename == name for e in out):
                            out.append(FakeFile(name, True, 0.0, 0))
                return out

            def retrieveFile(self, share, path, buf):
                buf.write(self.files[path.strip("/")]["data"])

            def storeFile(self, share, path, stream):
                data = stream.read()
                self.files[path.strip("/")] = {
                    "is_dir": False, "mtime": time.time(), "size": len(data), "data": data,
                }

            def createDirectory(self, share, path):
                rel = path.strip("/")
                self.files.setdefault(rel, {"is_dir": True, "mtime": 0.0, "size": 0, "data": b""})
                self.created.append(rel)

            def deleteFiles(self, share, path):
                rel = path.strip("/")
                if rel in self.files:
                    del self.files[rel]
                    self.deleted.append(rel)

        smb_mod = types.ModuleType("smb")
        conn_mod = types.ModuleType("smb.SMBConnection")
        conn_mod.SMBConnection = FakeSMBConnection
        monkeypatch.setitem(sys.modules, "smb", smb_mod)
        monkeypatch.setitem(sys.modules, "smb.SMBConnection", conn_mod)
        return FakeSMBConnection

    def test_listdir_walks_subdirectories(self, monkeypatch):
        now = time.time()
        self._inject_fake_smb(
            monkeypatch,
            {
                "top.txt": (False, now, 3, b"abc"),
                "docs/notes.txt": (False, now, 5, b"notes"),
                "docs/sub/deep.txt": (False, now, 4, b"deep"),
            },
        )
        t = sync.SMBTransport("tailscale", "share", "user", "pass")
        entries = t.listdir()
        assert "top.txt" in entries
        assert "docs/notes.txt" in entries
        assert "docs/sub/deep.txt" in entries
        assert entries["docs/notes.txt"]["size"] == 5

    def test_put_get_mkdir_delete_roundtrip(self, monkeypatch):
        self._inject_fake_smb(monkeypatch, {})
        t = sync.SMBTransport("tailscale", "share", "user", "pass")
        t.mkdir("docs")
        t.put("docs/a.txt", b"hello")
        assert t.get("docs/a.txt") == b"hello"
        entries = t.listdir()
        assert "docs/a.txt" in entries
        assert entries["docs/a.txt"]["size"] == 5
        t.delete("docs/a.txt")
        assert "docs/a.txt" not in t.listdir()

    def test_mkdir_ignores_already_exists(self, monkeypatch):
        self._inject_fake_smb(monkeypatch, {})
        t = sync.SMBTransport("tailscale", "share", "user", "pass")
        t.mkdir("docs")
        t.mkdir("docs")  # no exception
        t.put("docs/a.txt", b"x")
        assert "docs/a.txt" in t.listdir()
