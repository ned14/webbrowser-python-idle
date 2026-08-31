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

    def test_legacy_record_without_size_keeps_same_mtime_skip(self):
        # Manifests written before backend_size existed must not start
        # re-pulling every unchanged file after an upgrade.
        remote = {"a.txt": {"mtime": 50.0, "size": 10}}
        manifest = {"files": {"a.txt": {"backend_mtime": 50.0}}}
        assert sync.compute_pull_plan(remote, manifest) == []

    def test_same_mtime_same_size_is_skipped(self):
        remote = {"a.txt": {"mtime": 100.0, "size": 10}}
        manifest = {"files": {"a.txt": {"backend_mtime": 100.0, "backend_size": 10}}}
        assert sync.compute_pull_plan(remote, manifest) == []

    def test_same_mtime_different_size_is_pulled(self):
        # HTTP dates have 1s resolution: a same-second overwrite with
        # different content changes the size, not the mtime — and must be
        # pulled on the next boot.
        remote = {"a.txt": {"mtime": 100.0, "size": 20}}
        manifest = {"files": {"a.txt": {"backend_mtime": 100.0, "backend_size": 10}}}
        assert sync.compute_pull_plan(remote, manifest) == ["a.txt"]

    def test_newer_mtime_is_pulled_even_with_same_size(self):
        remote = {"a.txt": {"mtime": 101.0, "size": 10}}
        manifest = {"files": {"a.txt": {"backend_mtime": 100.0, "backend_size": 10}}}
        assert sync.compute_pull_plan(remote, manifest) == ["a.txt"]

    def test_unrecorded_remote_file_existing_locally_is_not_pulled(self):
        # First-sync protection: a backend file with NO last-push record must
        # never overwrite an existing local file (it may be a user edit made
        # while sync was unavailable).
        remote = {"a.txt": {"mtime": 100.0}}
        assert sync.compute_pull_plan(remote, {"files": {}},
                                      local_existing=frozenset(["a.txt"])) == []

    def test_unrecorded_remote_file_not_local_is_pulled(self):
        remote = {"a.txt": {"mtime": 100.0}}
        assert sync.compute_pull_plan(remote, {"files": {}},
                                      local_existing=frozenset()) == ["a.txt"]


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

    def test_ping_false_on_bad_credentials(self, server):
        # The wait_for_tailnet retry loop lives on the FALSE answers: a dead
        # server or wrong creds must report "not up yet", never raise into
        # the loop (the loop treats exceptions as "not up" too, but the
        # truthful False path is the contract).
        t = sync.WebDAVTransport(server.url(), "webdav", "wrong")
        assert t.ping() is False

    def test_get_missing_file_raises(self, server):
        # The basis of acquire_lease's free-lease detection: a missing
        # backend file surfaces as FileNotFoundError (from the transport's
        # 404 mapping), never as a generic HTTPError.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        with pytest.raises(FileNotFoundError):
            t.get("no-such-file.txt")

    def test_delete_missing_file_is_silent(self, server):
        # release_lease deletes a lease that may already be gone (another
        # session's shutdown): must not raise.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        t.delete("no-such-file.txt")  # no exception

    def test_listdir_missing_collection_returns_empty(self, server):
        # pull_home on a fresh backend (no /webdav/ tree yet): an empty
        # listing, not an error.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        assert t.listdir("missing-dir") == {}

    def test_mkdir_creates_and_ignores_existing(self, server):
        # ensure_remote_parents MKCOLs every parent before a nested PUT; the
        # "already exists" (405) and race (404) outcomes are the NORMAL path
        # and must be silent — the whole nested-push flow depends on it.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        t.mkdir("docs")  # fresh create
        t.mkdir("docs")  # 405 -> ignored
        t.mkdir("no-parent/deep")  # parent missing -> 409-ish -> ignored
        t.put("docs/nested.txt", b"nested")
        assert "docs/nested.txt" in t.listdir()


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

    def test_corrupt_lease_is_treated_as_free(self, server):
        # A lease file that is not JSON (crashed mid-write, partial upload)
        # must not refuse the session forever — overwrite it.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        server.put_file(sync.LEASE_FILE, b"not json at all")
        sync.acquire_lease(t, "node-a")  # no LeaseRefused
        state = sync.json.loads(t.get(sync.LEASE_FILE).decode("utf-8"))
        assert state["node"] == "node-a"

    def test_lease_missing_ts_key_is_treated_as_stale(self, server):
        # A lease record without a ts key (ValueError on float(None)) is a
        # broken record: acquire must overwrite, not refuse.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        server.put_file(sync.LEASE_FILE, b'{"node": "other"}')
        sync.acquire_lease(t, "node-a")
        state = sync.json.loads(t.get(sync.LEASE_FILE).decode("utf-8"))
        assert state["node"] == "node-a"

    def test_refresh_overwrites_corrupt_lease(self, server):
        # refresh_lease's ValueError branch: a corrupt lease cannot be read
        # as "ours", so it is overwritten (re-acquired) rather than refused.
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        server.put_file(sync.LEASE_FILE, b"garbage")
        sync.refresh_lease(t, "node-a")  # no LeaseRefused
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

    def test_make_snapshot_excludes_nested_excluded_names(self, tmp_path):
        # The .ssh keypair (and .cache etc.) must stay in the guest even when
        # a synced project directory contains same-named entries: the
        # snapshot exclusion contract is per-path-part, exactly like
        # scan_local/extract_snapshot (regression 2026-08-30: tarfile.add
        # recursive mode filtered only top-level names).
        (tmp_path / "project").mkdir()
        (tmp_path / "project" / ".ssh").mkdir()
        (tmp_path / "project" / ".ssh" / "id_ed25519").write_text("secret")
        (tmp_path / "project" / ".cache").mkdir()
        (tmp_path / "project" / ".cache" / "junk").write_text("junk")
        (tmp_path / "project" / "code.py").write_text("print(1)")
        (tmp_path / "top.txt").write_text("top")

        data = sync.make_snapshot(tmp_path)
        import io
        import tarfile
        names = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").getnames()
        assert "top.txt" in names
        assert "project/code.py" in names
        assert not any(".ssh" in n or ".cache" in n for n in names), names

    def test_make_snapshot_skips_symlinks(self, tmp_path):
        # Symlinks must never ride the snapshot: the agent may run as root
        # (following a link could upload files outside the home tree) and the
        # extractor refuses link members anyway — the same contract as
        # scan_local. A snapshot carrying links would leak what it pointed at
        # and restore nothing.
        (tmp_path / "real.txt").write_text("real")
        os.symlink("/etc/passwd", tmp_path / "escaped-link")
        os.symlink(tmp_path / "real.txt", tmp_path / "local-link")
        (tmp_path / "dir").mkdir()
        os.symlink("/tmp", tmp_path / "dir" / "dir-link")

        data = sync.make_snapshot(tmp_path)
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            members = tf.getmembers()
        assert "real.txt" in [m.name for m in members]
        assert not any(m.name in ("escaped-link", "local-link", "dir/dir-link") for m in members), members
        assert not any(m.issym() or m.islnk() for m in members), members

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

    def test_pull_restores_initial_snapshot(self, tmp_path, server):
        # First sync with a pre-existing snapshot on the backend (no per-file
        # manifest yet): the snapshot is extracted non-clobbering. The
        # per-file manifest stays EMPTY (the snapshot is the only backend
        # copy; per-file records land on the next push, which then uploads
        # the per-file copies).
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("snapshot a")
        (src / "sub").mkdir()
        (src / "sub" / "b.txt").write_text("snapshot b")
        server.put_file(sync.SNAPSHOT_FILE, sync.make_snapshot(src))

        home = tmp_path / "home"
        home.mkdir()
        (home / "a.txt").write_text("local edit")  # must survive (non-clobbering)
        manifest = sync.empty_manifest()
        sync.pull_home(home, t, manifest)
        assert (home / "a.txt").read_text() == "local edit"
        assert (home / "sub" / "b.txt").read_text() == "snapshot b"
        assert manifest["files"] == {}
        # The next push records the manifest AND uploads the per-file copies
        # (the backend then has both the snapshot and the per-file tree).
        sync.push_home(home, t, manifest)
        assert "sub/b.txt" in manifest["files"]
        assert "sub/b.txt" in t.listdir()


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

    def test_pull_skips_when_tailnet_never_came_up(self, tmp_path, server, monkeypatch):
        # A misconfigured tailnet must never block the boot: the pull logs
        # the skip and returns 0 (X starts regardless).
        monkeypatch.setattr(sync, "wait_for_tailnet", lambda t, log=print: False)
        server.put_file("f.txt", b"backend", mtime=time.time())
        logs = []
        rc = sync.cmd_pull(tmp_path, self._cfg(server), log=logs.append)
        assert rc == 0
        assert any("skipping boot pull" in line for line in logs)
        assert not (tmp_path / "f.txt").exists()

    def test_pull_lease_error_is_crash_safe(self, tmp_path, server, monkeypatch):
        # A NON-LeaseRefused failure while acquiring (backend unreachable)
        # must log and return 0 — the boot pull is best-effort by design
        # (the daemon phase retries later; X must never wait on it).
        monkeypatch.setattr(sync, "wait_for_tailnet", lambda t, log=print: True)
        monkeypatch.setattr(sync, "acquire_lease", lambda t, n: (_ for _ in ()).throw(ConnectionError("boom")))
        logs = []
        rc = sync.cmd_pull(tmp_path, self._cfg(server), log=logs.append)
        assert rc == 0
        assert any("boot pull lease error" in line for line in logs)

    def test_pull_failure_is_crash_safe(self, tmp_path, server, monkeypatch):
        # A mid-pull failure (transport dies during listdir/get) must log and
        # return 0 — same best-effort contract.
        monkeypatch.setattr(sync, "wait_for_tailnet", lambda t, log=print: True)
        monkeypatch.setattr(sync, "pull_home", lambda h, t, m, log=print: (_ for _ in ()).throw(ConnectionError("mid-pull")))
        logs = []
        rc = sync.cmd_pull(tmp_path, self._cfg(server), log=logs.append)
        assert rc == 0
        assert any("boot pull failed" in line for line in logs)


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

    def test_backend_error_retries_then_gives_up(self, server, monkeypatch):
        # A transport that RAISES on acquire (backend unreachable) must be
        # retried like a refusal — and an unowned session must still give up
        # after the retry window (it must never become the writer).
        monkeypatch.setattr(sync, "LEASE_RETRY_S", 0)

        class RaisingTransport:
            def put(self, path, data):
                raise ConnectionError("backend unreachable")

            def get(self, path):
                raise ConnectionError("backend unreachable")

        logs = []
        stop = {"flag": False}
        assert sync.acquire_or_wait(RaisingTransport(), "node-a", retryable=False,
                                    stop=stop, log=logs.append) is False
        assert any("backend unreachable" in line for line in logs)


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
            # When set, every listPath raises (a mid-session Samba restart /
            # dead connection) — the transport must discard the cached
            # connection so the next operation reconnects.
            FAIL_LISTPATH = False

            def __init__(self, user, password, *args, **kwargs):
                self.files = {rel: dict(info) for rel, info in self.SEED.items()}
                self.created = []
                self.deleted = []
                FakeSMBConnection.instances.append(self)

            def connect(self, host, port, timeout):
                pass

            def listPath(self, share, path):
                if FakeSMBConnection.FAIL_LISTPATH:
                    raise OSError("connection reset by Samba")
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

    def test_ping_true_on_empty_share(self, monkeypatch):
        # An EMPTY but reachable share counts as up (a falsy return made
        # wait_for_tailnet report "not up" forever on a healthy share).
        self._inject_fake_smb(monkeypatch, {})
        t = sync.SMBTransport("tailscale", "share", "user", "pass")
        assert t.ping() is True

    def test_discards_stale_connection_after_failure(self, monkeypatch):
        # A mid-session Samba restart must not leave a stale connection that
        # fails forever: a failed operation discards it, the next one
        # reconnects (a fresh fake instance).
        now = time.time()
        fake = self._inject_fake_smb(
            monkeypatch, {"f.txt": (False, now, 3, b"abc")}
        )
        fake.FAIL_LISTPATH = False
        t = sync.SMBTransport("tailscale", "share", "user", "pass")
        assert t.get("f.txt") == b"abc"
        assert len(fake.instances) == 1

        fake.FAIL_LISTPATH = True
        # listdir swallows the per-directory failure (the walk skips the
        # unreadable dir) but MUST discard the stale connection so the next
        # operation reconnects — the stale-connection invariant.
        entries = t.listdir()
        assert entries == {}
        assert t.conn is None, "the stale connection must be discarded"

        fake.FAIL_LISTPATH = False
        assert "f.txt" in t.listdir()
        assert len(fake.instances) == 2, "the next operation must reconnect"


# --------------------------------------------------------------------------
# Config loading / transport construction error paths
# --------------------------------------------------------------------------

class TestLoadConfig:
    def test_precedence_opt_over_home_over_root(self, monkeypatch):
        import builtins
        import io

        contents = {
            "/opt/syncrc": "backend = webdav\nurl = http://opt/\n",
            "/home/user/.syncrc": "backend = webdav\nurl = http://home/\n",
            "/root/.syncrc": "backend = webdav\nurl = http://root/\n",
        }

        def fake_open(path, *args, **kwargs):
            if str(path) in contents:
                return io.StringIO(contents[str(path)])
            raise OSError(2, "no such file")

        monkeypatch.setattr(builtins, "open", fake_open)
        assert sync.load_config()["url"] == "http://opt/"

        del contents["/opt/syncrc"]
        assert sync.load_config()["url"] == "http://home/"

        del contents["/home/user/.syncrc"]
        assert sync.load_config()["url"] == "http://root/"

    def test_no_config_anywhere_returns_empty(self, monkeypatch):
        import builtins

        def fake_open(path, *args, **kwargs):
            raise OSError(2, "no such file")

        monkeypatch.setattr(builtins, "open", fake_open)
        assert sync.load_config() == {}

    def test_ignores_file_without_backend_key(self, monkeypatch):
        import builtins
        import io

        monkeypatch.setattr(
            builtins, "open",
            lambda path, *a, **k: io.StringIO("url = http://x/\n")
        )
        assert sync.load_config() == {}


class TestBuildTransport:
    def test_unknown_backend_raises(self):
        with pytest.raises(sync.ConfigError):
            sync.build_transport({"backend": "nfs"})

    def test_webdav_missing_url_raises(self):
        with pytest.raises(sync.ConfigError):
            sync.build_transport({"backend": "webdav"})

    def test_samba_missing_host_or_share_raises(self):
        with pytest.raises(sync.ConfigError):
            sync.build_transport({"backend": "samba", "share": "s"})
        with pytest.raises(sync.ConfigError):
            sync.build_transport({"backend": "samba", "host": "h"})

    def test_webdav_builds(self):
        t = sync.build_transport({"backend": "webdav", "url": "http://x/"})
        assert isinstance(t, sync.WebDAVTransport)


class TestRedirectDropsAuth:
    """Security-relevant: urllib copies request headers verbatim across
    redirects, so a redirecting (or compromised) WebDAV server could leak
    the sync password — _SameOriginAuthRedirectHandler must drop the
    Authorization header off-origin."""

    def test_cross_origin_redirect_drops_authorization(self):
        import urllib.request

        handler = sync._SameOriginAuthRedirectHandler()
        req = urllib.request.Request("http://a.example:8082/webdav/f.txt")
        req.add_header("Authorization", "Basic d2ViZGF2OnNlY3JldA==")

        new = handler.redirect_request(req, None, 302, "Found", {}, "http://evil.example/f.txt")
        assert new is not None
        assert not new.has_header("Authorization")

        new2 = handler.redirect_request(req, None, 302, "Found", {}, "http://a.example:8082/webdav/g.txt")
        assert new2 is not None
        assert new2.has_header("Authorization")

        # A scheme change is cross-origin too (http -> https)
        new3 = handler.redirect_request(req, None, 302, "Found", {}, "https://a.example/f.txt")
        assert new3 is not None
        assert not new3.has_header("Authorization")


# --------------------------------------------------------------------------
# Tailnet wait + push loop + daemon
# --------------------------------------------------------------------------

class TestWaitForTailnet:
    def test_retries_until_ping_succeeds(self, monkeypatch):
        calls = {"n": 0}

        class PingTransport:
            def ping(self):
                calls["n"] += 1
                return calls["n"] >= 3

        monkeypatch.setattr(sync, "TAILNET_WAIT_ATTEMPTS", 5)
        monkeypatch.setattr(sync, "_sleep", lambda s: None)
        logs = []
        assert sync.wait_for_tailnet(PingTransport(), log=logs.append) is True
        assert calls["n"] == 3
        assert len(logs) == 2

    def test_gives_up_after_attempts(self, monkeypatch):
        class DownTransport:
            def ping(self):
                return False

        monkeypatch.setattr(sync, "TAILNET_WAIT_ATTEMPTS", 3)
        monkeypatch.setattr(sync, "_sleep", lambda s: None)
        assert sync.wait_for_tailnet(DownTransport()) is False

    def test_raising_ping_retries_like_down(self, monkeypatch):
        # A transport whose ping RAISES (dead connection mid-probe) must be
        # treated exactly like "not up yet" — the retry loop re-pings.
        class RaisingTransport:
            def ping(self):
                raise ConnectionError("connection refused")

        monkeypatch.setattr(sync, "TAILNET_WAIT_ATTEMPTS", 3)
        monkeypatch.setattr(sync, "_sleep", lambda s: None)
        logs = []
        assert sync.wait_for_tailnet(RaisingTransport(), log=logs.append) is False
        assert len(logs) == 3


# --------------------------------------------------------------------------
# WebDAV listing sanitization (_href_to_rel — the security boundary for
# untrusted remote listings) + HTTP date parsing
# --------------------------------------------------------------------------

class TestHrefToRel:
    def make(self, base="http://100.64.0.1:8082/webdav/"):
        return sync.WebDAVTransport(base)

    def test_absolute_url_href_stripped_to_relative(self):
        t = self.make()
        assert t._href_to_rel("http://100.64.0.1:8082/webdav/sub/f.txt") == "sub/f.txt"

    def test_relative_href_with_base_path(self):
        t = self.make()
        assert t._href_to_rel("/webdav/sub/f.txt") == "sub/f.txt"

    def test_collection_root_is_none(self):
        t = self.make()
        assert t._href_to_rel("http://100.64.0.1:8082/webdav/") is None
        assert t._href_to_rel("/webdav") is None
        assert t._href_to_rel("http://100.64.0.1:8082/webdav") is None

    def test_percent_encoding_decoded(self):
        t = self.make()
        assert t._href_to_rel("/webdav/a%20b.txt") == "a b.txt"

    def test_path_traversal_rejected(self):
        t = self.make()
        assert t._href_to_rel("/webdav/../escape.txt") is None
        assert t._href_to_rel("/webdav/sub/../../escape.txt") is None

    def test_empty_segments_rejected(self):
        t = self.make()
        assert t._href_to_rel("/webdav/sub//a.txt") is None

    def test_base_without_path(self):
        t = self.make("http://100.64.0.1:8082/")
        assert t._href_to_rel("http://100.64.0.1:8082/f.txt") == "f.txt"

    def test_foreign_host_href_uses_its_path(self):
        # A malicious/broken server advertising an absolute URL on a
        # different host: only the PATH is trusted (the base-path strip
        # still applies), so nothing outside the synced tree is addressable.
        t = self.make()
        assert t._href_to_rel("http://evil.example/webdav/x.txt") == "x.txt"


class TestParseHttpDate:
    def test_rfc1123_gmt(self):
        assert sync._parse_http_date("Fri, 29 Aug 2026 10:00:00 GMT") == \
            datetime(2026, 8, 29, 10, 0, 0, tzinfo=timezone.utc).timestamp()

    def test_naive_date_treated_as_utc(self):
        # A server sending a timezone-less date must not be interpreted as
        # local time (clock-skew-safe sync decisions).
        assert sync._parse_http_date("29 Aug 2026 10:00:00") == \
            datetime(2026, 8, 29, 10, 0, 0, tzinfo=timezone.utc).timestamp()

    def test_invalid_dates_return_zero(self):
        assert sync._parse_http_date("not a date") == 0.0
        assert sync._parse_http_date("") == 0.0


# --------------------------------------------------------------------------
# ensure_remote_parents (MKCOL chain before PUTs)
# --------------------------------------------------------------------------

class TestEnsureRemoteParents:
    def test_mkcols_full_ancestor_chain(self):
        class RecordingMkdir:
            def __init__(self):
                self.mkcols = []

            def mkdir(self, path):
                self.mkcols.append(path)

        t = RecordingMkdir()
        sync.ensure_remote_parents(t, "a/b/c.txt")
        assert t.mkcols == ["a", "a/b"]

    def test_root_level_file_needs_no_mkcol(self):
        class RecordingMkdir:
            def __init__(self):
                self.mkcols = []

            def mkdir(self, path):
                self.mkcols.append(path)

        t = RecordingMkdir()
        sync.ensure_remote_parents(t, "root.txt")
        assert t.mkcols == []


class TestPushLoop:
    @pytest.fixture()
    def server(self):
        srv = FakeWebDAVServer(user="webdav", password="secret")
        srv.start()
        yield srv
        srv.stop()

    def test_pushes_changes_and_releases_lease_on_stop(self, tmp_path, server, monkeypatch):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        (tmp_path / "note.txt").write_text("hello")
        manifest = sync.empty_manifest()
        node = "node-a"
        sync.acquire_lease(t, node)

        stop = {"flag": False}
        sleeps = {"n": 0}

        def fake_sleep(_s):
            sleeps["n"] += 1
            stop["flag"] = True  # stop after the first poll sleep

        monkeypatch.setattr(sync, "_sleep", fake_sleep)
        sync._run_push_loop(tmp_path, t, manifest, node, stop, log=lambda _m: None)

        assert t.get("note.txt") == b"hello"
        assert manifest["files"]["note.txt"]["local_mtime"] > 0
        # Clean shutdown: final push ran AND the lease was released.
        with pytest.raises(FileNotFoundError):
            t.get(sync.LEASE_FILE)

    def test_lease_takeover_propagates(self, tmp_path, server, monkeypatch):
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        manifest = sync.empty_manifest()
        sync.acquire_lease(t, "node-a")
        stop = {"flag": False}

        def fake_sleep(_s):
            # Another session took the lease over mid-run.
            server.put_file(sync.LEASE_FILE, '{"ts": 1e18, "node": "node-b"}'.encode("utf-8"))

        monkeypatch.setattr(sync, "_sleep", fake_sleep)
        with pytest.raises(sync.LeaseRefused):
            sync._run_push_loop(tmp_path, t, manifest, "node-a", stop, log=lambda _m: None)

    def test_push_error_and_final_push_failure_are_logged(self, tmp_path, server, monkeypatch):
        # A backend that dies mid-session must not kill the daemon: the loop
        # logs "push error" and keeps polling; the final push on shutdown
        # failing is logged too, and the lease release still runs.
        class DeadBackend:
            def put(self, path, data):
                raise ConnectionError("backend died")

            def get(self, path):
                raise ConnectionError("backend died")

            def delete(self, path):
                return None  # lease release "succeeds" (best-effort)

            def listdir(self, path=""):
                raise ConnectionError("backend died")

            def mkdir(self, path):
                raise ConnectionError("backend died")

        (tmp_path / "note.txt").write_text("hello")
        manifest = sync.empty_manifest()
        stop = {"flag": False}
        sleeps = {"n": 0}

        def fake_sleep(_s):
            sleeps["n"] += 1
            stop["flag"] = True  # stop after the first poll sleep

        monkeypatch.setattr(sync, "_sleep", fake_sleep)
        logs = []
        sync._run_push_loop(tmp_path, DeadBackend(), manifest, "node-a", stop, log=logs.append)
        assert any("push error" in line for line in logs), logs
        assert any("final push failed" in line for line in logs), logs

    def test_push_loop_backs_off_to_idle_cadence_and_rearms_on_change(self, tmp_path, server, monkeypatch):
        """POLL_S -> POLL_IDLE_S after POLL_IDLE_CYCLES change-free scans, and
        back to POLL_S the moment a change appears (the loop's scans are
        emulated stat walks; a quiet home must not pay the fast cadence
        forever). The cadence is observed via the wall-clock GAP between
        consecutive scans (the loop sleeps 1s per poll tick, POLL_S ticks per
        fast cycle)."""
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        manifest = sync.empty_manifest()
        node = "node-a"
        sync.acquire_lease(t, node)

        stop = {"flag": False}
        clock = {"t": 1000.0}
        scan_times = []
        real_scan = sync.scan_local

        def counting_scan(home):
            scan_times.append(clock["t"])
            if len(scan_times) >= 8:
                stop["flag"] = True
            return real_scan(home)

        def fake_sleep(_s):
            clock["t"] += 1
            if clock["t"] - 1000 >= 50:
                # A change appears mid-slow-cadence.
                (tmp_path / "new.txt").write_text("hi")

        monkeypatch.setattr(sync, "scan_local", counting_scan)
        monkeypatch.setattr(sync, "_sleep", fake_sleep)
        monkeypatch.setattr(sync.time, "time", lambda: clock["t"])
        sync._run_push_loop(tmp_path, t, manifest, node, stop, log=lambda _m: None)

        gaps = [round(b - a) for a, b in zip(scan_times, scan_times[1:])]
        # push_home scans internally too (same clock -> a 0 gap); the
        # cadence signal is the non-zero gaps between LOOP scans.
        cadence = [g for g in gaps if g > 0]
        # 3 fast cycles at POLL_S, then the slow cadence kicks in...
        assert cadence[:3] == [sync.POLL_S] * 3
        assert cadence[3:5] == [sync.POLL_IDLE_S] * 2
        # ...and the change re-arms the fast cadence on the next scan.
        assert cadence[5] == sync.POLL_S
        # The change actually landed on the backend.
        assert t.get("new.txt") == b"hi"


class TestCmdDaemon:
    @pytest.fixture()
    def server(self):
        srv = FakeWebDAVServer(user="webdav", password="secret")
        srv.start()
        yield srv
        srv.stop()

    @staticmethod
    def _cfg(server):
        return {"backend": "webdav", "url": server.url(), "user": "webdav", "password": "secret"}

    def test_runs_until_sigterm_and_releases_lease(self, tmp_path, server, monkeypatch):
        # Run the daemon in-process and deliver a real SIGTERM: the daemon's
        # handler sets its internal stop flag, the loop exits cleanly with a
        # final push + lease release. POLL_S=0 keeps the loop spinning so the
        # signal lands promptly.
        import signal
        import threading

        monkeypatch.setattr(sync, "POLL_S", 0)

        def stop_later():
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=stop_later, daemon=True).start()
        logs = []
        rc = sync.cmd_daemon(tmp_path, self._cfg(server), log=logs.append)
        assert rc == 0
        assert any("lease acquired" in line for line in logs)
        assert any("lease released" in line for line in logs)
        t = sync.WebDAVTransport(server.url(), "webdav", "secret")
        with pytest.raises(FileNotFoundError):
            t.get(sync.LEASE_FILE)

    def test_gives_up_when_lease_never_available(self, tmp_path, server, monkeypatch):
        monkeypatch.setattr(
            sync, "acquire_or_wait",
            lambda t, n, retryable, stop, log=lambda _m: None: False,
        )
        logs = []
        rc = sync.cmd_daemon(tmp_path, self._cfg(server), log=logs.append)
        assert rc == 0
        assert any("never became available" in line for line in logs)

    def test_signal_registration_failure_is_not_fatal(self, tmp_path, server, monkeypatch):
        # CheerpX's emulated Linux may not support signal registration; the
        # daemon must log and continue (a crash here would kill the push
        # loop on every tailnet boot).
        import signal as signal_mod

        def _no_signal(*_a, **_k):
            raise RuntimeError("no signals here")

        monkeypatch.setattr(signal_mod, "signal", _no_signal)
        monkeypatch.setattr(
            sync, "acquire_or_wait",
            lambda t, n, retryable, stop, log=lambda _m: None: False,
        )
        logs = []
        rc = sync.cmd_daemon(tmp_path, self._cfg(server), log=logs.append)
        assert rc == 0
        assert any("signal registration unsupported" in line for line in logs)

    def test_requires_after_takeover_reacquires(self, tmp_path, server, monkeypatch):
        # A mid-run lease takeover raises LeaseRefused out of the push loop;
        # cmd_daemon's outer loop must re-acquire (never return — the daemon
        # is a long-lived process) and resume pushing.
        import signal
        import threading

        monkeypatch.setattr(sync, "POLL_S", 0)
        # The lease heartbeat gate is wall-clock; advance the (patched) clock
        # on every sleep so each loop iteration re-checks the lease.
        clock = {"t": 1000.0}
        state = {"runs": 0}

        def fake_sleep(_s):
            clock["t"] += 16
            state["runs"] += 1
            if state["runs"] == 1:
                # Mid-first-run takeover by a STALE foreign lease (like a
                # hidden tab whose guest timers stalled past LEASE_EXPIRY_S —
                # the ts must be old or the re-acquire would refuse forever).
                server.put_file(sync.LEASE_FILE, ('{"ts": %f, "node": "other"}' % (clock["t"] - 200)).encode("utf-8"))
            else:
                # Second run: clean stop.
                threading.Timer(0.2, os.kill, args=(os.getpid(), signal.SIGTERM)).start()

        monkeypatch.setattr(sync, "_sleep", fake_sleep)
        monkeypatch.setattr(sync.time, "time", lambda: clock["t"])
        logs = []
        rc = sync.cmd_daemon(tmp_path, self._cfg(server), log=logs.append)
        assert rc == 0
        assert any("lease taken over; re-acquiring" in line for line in logs)
        assert state["runs"] >= 2, "the daemon must survive the takeover and re-run the push loop"


class TestMain:
    def test_no_config_prints_and_returns_zero(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(sync, "load_config", lambda: {})
        assert sync.main(["pull", "--home", str(tmp_path)]) == 0
        assert "no config" in capsys.readouterr().out

    def test_unknown_command_exits_2(self):
        with pytest.raises(SystemExit) as exc:
            sync.main(["frobnicate"])
        assert exc.value.code == 2


# --------------------------------------------------------------------------
# Snapshot / scan edge cases
# --------------------------------------------------------------------------

class TestSnapshotEdgeCases:
    def test_extract_snapshot_skip_existing(self, tmp_path):
        # First-sync restore is non-clobbering: pre-existing local files must
        # never be overwritten by the snapshot copy.
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("new a")
        (src / "b.txt").write_text("new b")
        data = sync.make_snapshot(src)

        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "a.txt").write_text("local edit")
        sync.extract_snapshot(dest, data, skip_existing=True)
        assert (dest / "a.txt").read_text() == "local edit"
        assert (dest / "b.txt").read_text() == "new b"

    def test_scan_local_skips_symlinks(self, tmp_path):
        # The agent may run as root; following a symlink could upload files
        # outside the home tree.
        (tmp_path / "real.txt").write_text("x")
        outside = tmp_path / "outside-secret.txt"
        outside.write_text("secret")
        (tmp_path / "link").symlink_to(outside)
        local = sync.scan_local(tmp_path)
        assert "real.txt" in local
        assert "link" not in local
