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
        assert t.ping() == {}


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
