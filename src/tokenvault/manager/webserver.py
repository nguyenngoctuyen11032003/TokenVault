"""Loopback HTTP server for the UI folder (+ optional ``POST /api/<method>``).

Serves ``src/tokenvault/ui`` to the pywebview window (ES modules need http and a
``text/javascript`` MIME type; the Windows registry may map ``.js`` to
``text/plain``, so every type is FORCED from a fixed table) and, for the dev
server and as the UI's http fallback, dispatches ``POST /api/<method>`` (JSON
argument array) to the same ``Api`` object the window uses.

Access control (other local programs / web pages must not probe it):

* Binds ``127.0.0.1`` on an ephemeral port only.
* ``Host`` must be ``127.0.0.1:<port>`` or ``localhost:<port>`` (DNS rebinding).
* ``Sec-Fetch-Site`` of ``cross-site`` / ``same-site`` is refused, and an ``Origin``
  on POST must be our own origin.
* Per-launch random token (``require_token``, used by the app window): the
  window first loads ``/?tvk=<token>``; the server answers 303 to ``/`` with an
  ``HttpOnly; SameSite=Strict`` cookie; every other request needs that cookie
  (or an ``X-TV-Token`` header). The dev server runs without the token (fake
  data, but still loopback + Host + fetch-metadata checks).

POST checks (Host, fetch metadata, Origin, token, method name, Content-Length <=
1 MiB) all run on the headers BEFORE any body byte is read; a rejected body is only
drained (bounded, never parsed) so Windows does not reset the reply. Every response
carries a strict ``Content-Security-Policy`` (see ``CSP``).

Static rules: GET/HEAD only, no directory listing, only known asset extensions,
no dot-files, ``Cache-Control: no-store``. The raw path is refused if it contains
backslashes, ``%5c``, NUL, ``//``, a drive colon or any ``.``/``..`` segment
(before and after one percent-decoding), and the resolved file must stay inside
the UI root.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import re
import secrets
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

log = logging.getLogger(__name__)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8",
}
MAX_BODY = 1 << 20
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'")
_METHOD = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
ApiCall = Callable[[str, list[Any]], Any]


class _Server(ThreadingHTTPServer):
    request_queue_size = 128  # Chromium opens many module requests at once
    daemon_threads = True
    owner: UiServer


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "TokenVault"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ----------------------------------------------------------- plumbing
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.debug("ui %s - " + format, self.client_address[0], *args)

    def _send(self, status: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8",
              extra: dict[str, str] | None = None, head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body and not head:
            self.wfile.write(body)

    def _fail(self, status: int, head: bool = False) -> None:
        if self.command == "POST":
            self.close_connection = True  # the request body was not read
        self._send(status, HTTPStatus(status).phrase.encode(), head=head)

    def _json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    # ------------------------------------------------------------- guards
    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _site_ok(self) -> bool:
        return self.headers.get("Sec-Fetch-Site", "none").lower() in ("none", "same-origin")

    def _token_ok(self) -> bool:
        owner = self.server.owner
        if not owner.require_token:
            return True
        given = self.headers.get("X-TV-Token")
        if given is None:
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except Exception:  # noqa: BLE001 - malformed cookie header = no token
                return False
            given = jar["tvk"].value if "tvk" in jar else None
        return given is not None and hmac.compare_digest(given.encode(), owner.token.encode())

    def _guard(self) -> bool:
        if not self.path.startswith("/") or not self._host_ok() or not self._site_ok():
            self._fail(403)
            return False
        return True

    # ------------------------------------------------------------- static
    def _resolve(self, raw: str) -> Path | None:
        low = raw.lower()
        if "\\" in raw or "%5c" in low or "%00" in low or raw.startswith("//"):
            return None
        try:
            path = unquote(raw, errors="strict")
        except UnicodeDecodeError:
            return None
        if path == "/":
            path = "/index.html"
        if any(c in path for c in ("\\", "\x00", ":")) or path.startswith("//"):
            return None
        parts = path[1:].split("/")
        if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
            return None
        root = self.server.owner.root
        target = root.joinpath(*parts).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        if target.suffix.lower() not in MIME:
            return None
        return target

    def _static(self, head: bool) -> None:
        if not self._guard():
            return
        raw, _, query = self.path.partition("?")
        owner = self.server.owner
        if owner.require_token and "tvk" in (q := parse_qs(query)):
            if hmac.compare_digest(q["tvk"][0].encode(), owner.token.encode()):
                cookie = f"tvk={owner.token}; Path=/; HttpOnly; SameSite=Strict"
                self._send(303, b"", extra={"Location": raw or "/", "Set-Cookie": cookie},
                           head=head)
            else:
                self._fail(403, head)
            return
        if not self._token_ok():
            self._fail(403, head)
            return
        target = self._resolve(raw)
        if target is None:
            self._fail(404, head)
            return
        try:
            body = target.read_bytes()
        except OSError:
            self._fail(404, head)
            return
        self._send(200, body, MIME[target.suffix.lower()], head=head)

    def do_GET(self) -> None:
        self._static(head=False)

    def do_HEAD(self) -> None:
        self._static(head=True)

    # ---------------------------------------------------------------- api
    def _content_length(self) -> int | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        return n if n >= 0 else None

    def _discard(self, length: int | None) -> None:
        """After a REJECTION reply: swallow (never parse) up to 1 MiB of the unread body
        so Windows does not reset the connection before the client reads the reply."""
        left = min(length or 0, MAX_BODY)
        with contextlib.suppress(OSError):
            self.connection.settimeout(1.0)
            while left > 0 and (chunk := self.rfile.read(min(left, 65536))):
                left -= len(chunk)

    def do_POST(self) -> None:
        self.close_connection = True  # one call per connection
        length = self._content_length()
        if not self._post_allowed(length):
            self._discard(length)
            return
        assert length is not None
        body = b""
        while len(body) < length and (chunk := self.rfile.read(min(length - len(body), 65536))):
            body += chunk
        try:
            args = json.loads(body or b"[]")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "body must be a JSON array"})
            return
        if not isinstance(args, list):
            self._json(400, {"error": "body must be a JSON array"})
            return
        method = self.path.partition("?")[0][len("/api/"):]
        owner = self.server.owner
        assert owner.api_call is not None
        try:
            result = owner.api_call(method, args)
        except LookupError:
            self._json(404, {"error": f"unknown method {method}"})
            return
        except Exception as e:  # noqa: BLE001 - Api errors are already clean messages
            self._json(500, {"error": str(e)})
            return
        self._json(200, result)

    def _post_allowed(self, length: int | None) -> bool:
        """Every check that needs only the request line and headers (body unread)."""
        if not self._guard():
            return False
        owner = self.server.owner
        raw = self.path.partition("?")[0]
        if owner.api_call is None or not raw.startswith("/api/"):
            self._fail(405)
            return False
        origin = self.headers.get("Origin")
        port = self.server.server_address[1]
        if origin is not None and origin not in (f"http://127.0.0.1:{port}",
                                                 f"http://localhost:{port}"):
            self._fail(403)
            return False
        if not self._token_ok():
            self._fail(403)
            return False
        if not _METHOD.fullmatch(raw[len("/api/"):]):
            self._json(404, {"error": "unknown method"})
            return False
        if length is None or length > MAX_BODY:
            self._json(413 if length is not None else 400, {"error": "bad request body"})
            return False
        return True

    def _not_allowed(self) -> None:
        self._fail(405)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed


class UiServer:
    def __init__(
        self,
        root: Path | str,
        *,
        api_call: ApiCall | None = None,
        require_token: bool = True,
        port: int = 0,
    ) -> None:
        self.root = Path(root).resolve()
        self.api_call = api_call
        self.require_token = require_token
        self.token = secrets.token_urlsafe(24)
        self.host = "127.0.0.1"
        self._port = port
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1]) if self._httpd else 0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def entry_url(self) -> str:
        """First URL to load (sets the token cookie when tokens are required)."""
        return f"{self.url}/?tvk={self.token}" if self.require_token else f"{self.url}/"

    def start(self) -> str:
        httpd = _Server((self.host, self._port), _Handler)
        httpd.owner = self
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, args=(0.05,), name="tv-ui-http", daemon=True
        )
        self._thread.start()
        log.info("UI server on %s (token %s)", self.url, "on" if self.require_token else "off")
        return self.entry_url

    def serve_forever(self) -> None:
        """Block until stop() (used by the dev server)."""
        if self._thread is not None:
            self._thread.join()

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
