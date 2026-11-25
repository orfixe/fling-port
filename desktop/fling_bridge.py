# SPDX-License-Identifier: MIT
# Copyright (c) 2026 orfixe and the Fling port contributors
# Unofficial port of Fling by Damion Rashford and Tatiana - see CREDITS.md and NOTICE
"""Local bridge between the Fling browser extensions (Chrome, Edge, Firefox,
Safari) and the desktop app.

A tiny HTTP server on 127.0.0.1 only.  It accepts requests from browser
*extensions* and local tools, never from web pages:

* the Host header must be 127.0.0.1:<port> or localhost:<port> (defeats DNS rebinding);
* a request that carries an Origin must come from an extension origin
  (chrome-extension://, moz-extension://, safari-web-extension://);
* POST needs the custom header ``X-Fling: 1`` and a JSON body, so a web page
  can't even send a "simple" cross-site request without a CORS preflight —
  and preflights from web origins are refused;
* optionally (``allowed_ids`` on the handler, from ``bridge_extension_ids`` in
  fling.json) only the listed extension IDs are accepted.  Chrome / Edge IDs are
  fixed per store listing; Firefox and Safari use a random UUID per install.

Requests are handled on the server thread; ``handler(path, body)`` is called
on the Qt thread through ``run_on_ui`` and its dict result is sent back as JSON.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 47613
EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://", "safari-web-extension://",
                     "extension://")          # Edge uses chrome-extension:// too; "extension://" is future-proofing
MAX_BODY = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "Fling"
    protocol_version = "HTTP/1.1"

    # -- helpers
    def log_message(self, fmt, *args):          # keep the app log quiet
        pass

    def _origin_ok(self) -> tuple[bool, str | None]:
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            # No Origin: a local tool (curl, `fling` CLI).  A Safari/Firefox extension
            # background page may also omit it for same-host requests.
            return True, None
        if not origin.startswith(EXTENSION_SCHEMES):
            return False, origin
        allowed = getattr(self.server, "allowed_ids", ())
        if allowed:
            ext_id = origin.split("://", 1)[1].split("/", 1)[0].lower()
            if ext_id not in allowed:
                return False, origin
        return True, origin

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _send(self, code: int, obj: dict | None, origin: str | None = None):
        body = b"" if obj is None else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        if obj is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _guard(self) -> tuple[bool, str | None]:
        if not self._host_ok():
            self._send(403, {"ok": False, "error": "bad host"})
            return False, None
        ok, origin = self._origin_ok()
        if not ok:
            self._send(403, {"ok": False, "error": "origin not allowed"})
            return False, None
        return True, origin

    # -- verbs
    def do_OPTIONS(self):
        if not self._host_ok():
            self._send(403, None)
            return
        ok, origin = self._origin_ok()
        if not ok or not origin:
            self._send(403, None)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Fling")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        ok, origin = self._guard()
        if not ok:
            return
        path = self.path.split("?", 1)[0]
        if path not in ("/v1/ping", "/v1/status"):
            self._send(404, {"ok": False, "error": "not found"}, origin)
            return
        self._dispatch(path, {}, origin)

    def do_POST(self):
        ok, origin = self._guard()
        if not ok:
            return
        if self.headers.get("X-Fling") != "1":
            self._send(403, {"ok": False, "error": "missing X-Fling header"}, origin)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self._send(413, {"ok": False, "error": "body too large"}, origin)
            return
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self._send(400, {"ok": False, "error": "JSON object expected"}, origin)
            return
        path = self.path.split("?", 1)[0]
        if path not in ("/v1/action", "/v1/tab"):
            self._send(404, {"ok": False, "error": "not found"}, origin)
            return
        self._dispatch(path, body, origin)

    def _dispatch(self, path, body, origin):
        try:
            result = self.server.call(path, body)
            self._send(200, result, origin)
        except TimeoutError:
            self._send(503, {"ok": False, "error": "Fling is busy"}, origin)
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e) or type(e).__name__}, origin)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return                      # a browser closed the popup mid-request: not an error
        super().handle_error(request, client_address)


class BrowserBridge:
    """Start with ``start()``; ``run_on_ui(fn)`` must execute fn on the Qt thread."""

    def __init__(self, handler, run_on_ui, port: int = DEFAULT_PORT):
        self.handler = handler
        self.run_on_ui = run_on_ui
        self.port = port
        self.httpd = None
        self.error = None

    def _call(self, path, body, timeout=4.0):
        done = threading.Event()
        box = {}

        def job():
            try:
                box["r"] = self.handler(path, body)
            except Exception as e:      # reported to the extension, not swallowed
                box["e"] = e
            finally:
                done.set()
        self.run_on_ui(job)
        if not done.wait(timeout):
            raise TimeoutError
        if "e" in box:
            raise box["e"]
        return box["r"]

    def start(self) -> bool:
        try:
            self.httpd = _Server(("127.0.0.1", self.port), _Handler)
        except OSError as e:
            self.error = f"port {self.port}: {e}"
            print("bridge:", self.error, flush=True)
            return False
        self.httpd.call = self._call
        self.httpd.allowed_ids = tuple(getattr(self.handler, "allowed_ids", ()) or ())
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="fling-bridge").start()
        print(f"bridge: listening on 127.0.0.1:{self.port}", flush=True)
        return True

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
