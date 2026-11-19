"""Loopback UI server: forced MIME types, traversal-safe, token + Host/Origin guarded."""

from __future__ import annotations

import http.client
import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tokenvault.manager.paths import ui_dir
from tokenvault.manager.webserver import UiServer

SECRET = "TOP-SECRET-CONTENT"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    ui = tmp_path / "ui"
    (ui / "js" / "routes").mkdir(parents=True)
    (ui / "css").mkdir()
    (ui / "img").mkdir()
    (ui / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
    (ui / "js" / "app.js").write_text("export default 1;", encoding="utf-8")
    (ui / "js" / "mod.mjs").write_text("export default 2;", encoding="utf-8")
    (ui / "css" / "a.css").write_text("body{}", encoding="utf-8")
    (ui / "data.json").write_text("{}", encoding="utf-8")
    (ui / "img" / "i.svg").write_text("<svg/>", encoding="utf-8")
    (ui / "img" / "p.png").write_bytes(b"\x89PNG\r\n")
    (ui / "f.woff2").write_bytes(b"wOF2")
    (ui / "__init__.py").write_text("x = 1", encoding="utf-8")
    (ui / ".hidden.js").write_text("1", encoding="utf-8")
    return ui


def calls_api(method: str, args: list[Any]) -> Any:
    if method == "echo":
        return {"args": args}
    if method == "fail":
        raise RuntimeError("[template_missing] adapter template missing")
    raise LookupError(method)


@pytest.fixture
def server(root: Path) -> Iterator[UiServer]:
    s = UiServer(root, api_call=calls_api, require_token=True)
    s.start()
    yield s
    s.stop()


def req(
    srv: UiServer,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    token: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    h = {"Host": f"127.0.0.1:{srv.port}"}
    if token:
        h["Cookie"] = f"tvk={srv.token}"
    h.update(headers or {})
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in h.items():
        conn.putheader(k, v)
    if body is not None:
        conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(body)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, data


@pytest.mark.parametrize(
    ("path", "ctype"),
    [
        ("/", "text/html; charset=utf-8"),
        ("/index.html", "text/html; charset=utf-8"),
        ("/js/app.js", "text/javascript; charset=utf-8"),
        ("/js/mod.mjs", "text/javascript; charset=utf-8"),
        ("/css/a.css", "text/css; charset=utf-8"),
        ("/data.json", "application/json; charset=utf-8"),
        ("/img/i.svg", "image/svg+xml"),
        ("/img/p.png", "image/png"),
        ("/f.woff2", "font/woff2"),
    ],
)
def test_forced_mime_types(server: UiServer, path: str, ctype: str) -> None:
    status, headers, _ = req(server, "GET", path)
    assert status == 200
    assert headers["content-type"] == ctype
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"


def test_real_ui_dir_serves_js_as_javascript() -> None:
    s = UiServer(ui_dir(), require_token=False)
    s.start()
    try:
        status, headers, body = req(s, "GET", "/js/app.js", token=False)
        assert status == 200 and headers["content-type"].startswith("text/javascript")
        assert b"import" in body
    finally:
        s.stop()


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/..%2fsecret.txt",
        "/%2e%2e/secret.txt",
        "/%2E%2E%2Fsecret.txt",
        "/js/..%2f..%2fsecret.txt",
        "/js/%2e%2e/%2e%2e/secret.txt",
        "/..%5csecret.txt",
        "/js%5c..%5c..%5csecret.txt",
        "/js\\..\\..\\secret.txt",
        "/..\\secret.txt",
        "//secret.txt",
        "//../secret.txt",
        "/C:/Windows/win.ini",
        "/C:%5cWindows%5cwin.ini",
        "/%00index.html",
        "/index.html%00.js",
        "/%252e%252e/secret.txt",
        "/..%c0%afsecret.txt",
        "/js/",
        "/js",
        "/__init__.py",
        "/.hidden.js",
        "/nope.js",
    ],
)
def test_traversal_and_non_assets_refused(server: UiServer, path: str) -> None:
    status, _, body = req(server, "GET", path)
    assert status in (400, 403, 404)
    assert SECRET.encode() not in body


def test_absolute_form_request_line_refused(server: UiServer) -> None:
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as s:
        s.sendall(
            f"GET http://127.0.0.1:{server.port}/../secret.txt HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{server.port}\r\nCookie: tvk={server.token}\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        data = s.recv(65536)
    assert data.split(b" ")[1] in (b"400", b"403", b"404") and SECRET.encode() not in data


def test_head_has_headers_no_body(server: UiServer) -> None:
    status, headers, body = req(server, "HEAD", "/js/app.js")
    assert status == 200 and body == b""
    assert headers["content-type"].startswith("text/javascript")
    assert int(headers["content-length"]) == len("export default 1;")
    assert req(server, "HEAD", "/js/missing.js")[0] == 404


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_methods_refused(server: UiServer, method: str) -> None:
    assert req(server, method, "/index.html")[0] == 405


def test_post_to_static_path_refused(server: UiServer) -> None:
    assert req(server, "POST", "/index.html", body=b"[]")[0] in (404, 405)


@pytest.mark.parametrize(
    "host", ["evil.example", f"evil.example:{0}", "127.0.0.2:{p}", "[::1]:{p}", "localhost:1"]
)
def test_host_header_checked(server: UiServer, host: str) -> None:
    status, _, _ = req(server, "GET", "/index.html", headers={"Host": host.format(p=server.port)})
    assert status == 403


def test_localhost_host_allowed(server: UiServer) -> None:
    assert req(server, "GET", "/index.html", headers={"Host": f"localhost:{server.port}"})[0] == 200


def test_token_required_and_query_sets_cookie(server: UiServer) -> None:
    assert req(server, "GET", "/index.html", token=False)[0] == 403
    assert req(server, "GET", "/js/app.js", token=False, headers={"Cookie": "tvk=nope"})[0] == 403
    status, headers, _ = req(server, "GET", f"/?tvk={server.token}", token=False)
    assert status == 303 and headers["location"] == "/"
    cookie = headers["set-cookie"]
    assert f"tvk={server.token}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert req(server, "GET", "/?tvk=wrong", token=False)[0] == 403
    assert server.entry_url.endswith(f"/?tvk={server.token}")


def test_cross_site_fetch_metadata_refused(server: UiServer) -> None:
    for site in ("cross-site", "same-site"):
        assert req(server, "GET", "/js/app.js", headers={"Sec-Fetch-Site": site})[0] == 403
    for site in ("same-origin", "none"):
        assert req(server, "GET", "/js/app.js", headers={"Sec-Fetch-Site": site})[0] == 200


def test_api_dispatch(server: UiServer) -> None:
    status, headers, body = req(
        server, "POST", "/api/echo", body=b'[1, "\xc4\x90"]',
        headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server.port}"},
    )
    assert status == 200 and headers["content-type"].startswith("application/json")
    assert json.loads(body) == {"args": [1, "Đ"]}


def test_api_errors(server: UiServer) -> None:
    hj = {"Content-Type": "application/json"}
    st, _, body = req(server, "POST", "/api/fail", body=b"[]", headers=hj)
    assert st == 500 and json.loads(body)["error"].startswith("[template_missing]")
    assert req(server, "POST", "/api/unknown", body=b"[]", headers=hj)[0] == 404
    assert req(server, "POST", "/api/echo", body=b"{}", headers=hj)[0] == 400
    assert req(server, "POST", "/api/echo", body=b"[", headers=hj)[0] == 400
    assert req(server, "POST", "/api/Bad-Name", body=b"[]", headers=hj)[0] == 404
    assert req(server, "POST", "/api/echo", body=b"[]", headers=hj, token=False)[0] == 403
    bad_origin = {**hj, "Origin": "http://evil.example"}
    assert req(server, "POST", "/api/echo", body=b"[]", headers=bad_origin)[0] == 403


def test_api_body_too_large(server: UiServer) -> None:
    big = b"[" + b"1," * 600_000 + b"1]"
    assert req(server, "POST", "/api/echo", body=big)[0] == 413


def test_no_api_when_not_configured(root: Path) -> None:
    s = UiServer(root, require_token=False)
    s.start()
    try:
        assert req(s, "POST", "/api/echo", body=b"[]", token=False)[0] in (404, 405)
    finally:
        s.stop()


def test_binds_loopback_only(server: UiServer) -> None:
    assert server.host == "127.0.0.1"
    assert server.url == f"http://127.0.0.1:{server.port}"
