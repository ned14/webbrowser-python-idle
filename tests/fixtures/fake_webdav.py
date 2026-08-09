#!/usr/bin/env python3
"""Minimal in-memory WebDAV server for unit tests.

Implements PROPFIND (depth 1), GET, PUT, DELETE with basic auth — enough for
the sync agent's transport tests. Run in a thread:

    server = FakeWebDAVServer(port=0, user="webdav", password="secret")
    server.start()
    url = server.url()          # http://127.0.0.1:PORT/webdav/
    server.put_file("a.txt", b"hello")
    ...
    server.stop()
"""

import base64
import datetime
import email.utils
import http.server
import threading
import urllib.parse


def _http_date(ts):
    return email.utils.formatdate(ts, usegmt=True)


class FakeWebDAVServer:
    def __init__(self, port=0, user="webdav", password="secret"):
        self.user = user
        self.password = password
        self._files = {}  # relpath -> (mtime_epoch, bytes)
        self._collections = set()  # relpath -> collection marker (MKCOL)
        self._httpd = None
        self._thread = None
        self.port = port

    def url(self):
        return "http://127.0.0.1:%d/webdav/" % self._httpd.server_address[1]

    def put_file(self, rel, data, mtime=None):
        self._files[rel] = (mtime if mtime is not None else datetime.datetime.now(datetime.timezone.utc).timestamp(), data)

    def list_files(self):
        return dict(self._files)

    def start(self):
        self._httpd = http.server.HTTPServer(("127.0.0.1", self.port), self._handler(self))
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._thread.join(timeout=5)

    @staticmethod
    def _handler(server):
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _check_auth(self):
                header = self.headers.get("Authorization", "")
                if not header.startswith("Basic "):
                    return False
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                user, _, password = decoded.partition(":")
                return user == server.user and password == server.password

            def _rel(self):
                # Strip the /webdav/ base path and leading slash
                path = urllib.parse.unquote(self.path.split("?", 1)[0])
                base = "/webdav/"
                if path.startswith(base):
                    path = path[len(base):]
                return path.lstrip("/")

            def _collection(self, rel):
                # PROPFIND listing: return every stored file under the prefix
                # (the agent issues Depth: infinity for the per-file manifest,
                # so nested files must appear too).
                prefix = rel.rstrip("/") + "/" if rel else ""
                members = [(name, mtime, data) for name, (mtime, data) in server._files.items()
                           if name.startswith(prefix)]
                return members

            def do_PROPFIND(self):
                if not self._check_auth():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="webdav"')
                    self.end_headers()
                    return
                rel = self._rel()
                ns = 'xmlns:d="DAV:"'
                responses = []
                members = self._collection(rel)
                for name, mtime, _data in members:
                    href = "/webdav/" + name
                    responses.append(
                        "<d:response><d:href>%s</d:href><d:propstat><d:prop>"
                        "<d:getlastmodified>%s</d:getlastmodified>"
                        "<d:getcontentlength>%d</d:getcontentlength>"
                        "</d:prop><d:status>HTTP/1.1 200 OK</d:status>"
                        "</d:propstat></d:response>" % (href, _http_date(mtime), len(_data))
                    )
                body = (
                    '<?xml version="1.0"?><d:multistatus %s>%s</d:multistatus>' % (ns, "".join(responses))
                ).encode("utf-8")
                self.send_response(207)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._check_auth():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="webdav"')
                    self.end_headers()
                    return
                entry = server._files.get(self._rel())
                if entry is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                _mtime, data = entry
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_PUT(self):
                if not self._check_auth():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="webdav"')
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                data = self.rfile.read(length)
                rel = self._rel()
                # WebDAV semantics: a PUT to a path whose parent collection
                # does not exist is a 409 Conflict (the agent must MKCOL first).
                parent = rel.rpartition("/")[0]
                if parent:
                    exists = (parent in server._collections) or any(
                        n == parent or n.startswith(parent + "/") for n in server._files
                    )
                    if not exists:
                        self.send_response(409)
                        self.end_headers()
                        return
                now = datetime.datetime.now(datetime.timezone.utc).timestamp()
                server._files[rel] = (now, data)
                self.send_response(201)
                self.end_headers()

            def do_MKCOL(self):
                if not self._check_auth():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="webdav"')
                    self.end_headers()
                    return
                rel = self._rel()
                # MKCOL creates a collection marker; existing -> 405
                if any(n == rel for n in server._files) or rel in server._collections:
                    self.send_response(405)
                    self.end_headers()
                    return
                server._collections.add(rel)
                self.send_response(201)
                self.end_headers()

            def do_DELETE(self):
                if not self._check_auth():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="webdav"')
                    self.end_headers()
                    return
                rel = self._rel()
                if rel in server._files:
                    del server._files[rel]
                    self.send_response(204)
                else:
                    self.send_response(404)
                self.end_headers()

        return Handler
