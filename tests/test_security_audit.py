"""Security and correctness fixes for the Python backend (audit/security-bugs)."""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from gev import bridge, control_room, errors, httputil, live, mcp_client, poi, public_http, research, security


def test_content_length_rejects_negative_and_non_digits():
    assert httputil.parse_content_length("0") == 0
    assert httputil.parse_content_length("512", 1024) == 512
    for raw in ("-1", "-512", "+8", "1.5", "0x10", "12abc", "", "  ", None):
        with pytest.raises(httputil.BadBody):
            httputil.parse_content_length(raw)
    with pytest.raises(httputil.BadBody):
        httputil.parse_content_length("9000", 100)


def test_loopback_fetch_requires_opt_in(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(public_http.UnsafeURL):
        public_http.resolve_public("http://127.0.0.1/")
    parts, addresses = public_http.resolve_public("http://127.0.0.1/", allow_loopback=True)
    assert parts.hostname == "127.0.0.1"
    assert addresses[0][4][0] == "127.0.0.1"


@pytest.mark.parametrize("ip", ["169.254.169.254", "10.0.0.1", "100.64.0.1"])
def test_allow_loopback_does_not_open_private_hosts(monkeypatch, ip):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))])
    with pytest.raises(public_http.UnsafeURL):
        public_http.resolve_public(f"http://{ip}/", allow_loopback=True)


def test_header_value_strips_crlf():
    assert "\r" not in public_http.header_value("hermes\r\nX-Injected: 1")
    assert "\n" not in public_http.header_value("hermes\nX-Injected: 1")


def test_poi_and_research_http_refuse_private_without_connecting(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))])
    monkeypatch.setattr(public_http, "_connection", lambda *a, **kw: pytest.fail("private URL was fetched"))
    monkeypatch.setattr(research, "_pace", lambda: None)
    assert poi._http("http://169.254.169.254/latest/meta-data/") == (0, "")
    assert research._http("http://169.254.169.254/latest/meta-data/") == (0, "")


def test_import_file_refuses_token_env_even_in_plugin_data(tmp_path, monkeypatch):
    import hermes_constants
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    monkeypatch.delenv("GODSEYE_IMPORT_ROOT", raising=False)
    root = security.plugin_data()
    root.mkdir(parents=True)
    secret = root / "hud-bridge.env"
    secret.write_text("GEV_HUD_SUMMARY_API_KEY=super-secret-token-value\n")
    with pytest.raises(ValueError, match="credential"):
        security.import_file("hud-bridge.env")
    with pytest.raises(ValueError, match="credential"):
        security.import_file(str(secret))
    (root / ".env").write_text("FIRMS_MAP_KEY=forbidden\n")
    with pytest.raises(ValueError, match="credential"):
        security.import_file(".env")
    ok = root / "pins.json"
    ok.write_text('[{"lat":1,"lon":2}]')
    assert security.import_file("pins.json") == ok.resolve()


def test_ensure_token_chmods_existing_and_ignores_blank(tmp_path):
    path = tmp_path / "hud-bridge.env"
    path.write_text("GEV_HUD_SUMMARY_API_KEY=\n")
    path.chmod(0o644)
    token = bridge.ensure_token(path)
    assert token
    assert "=" not in token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert f"GEV_HUD_SUMMARY_API_KEY={token}" in path.read_text()
    # A later call reuses the same token and keeps the mode.
    assert bridge.ensure_token(path) == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_annotate_scrubs_secret_values_and_key_shaped_text(monkeypatch):
    from agent import secret_scope
    monkeypatch.setattr(secret_scope, "get_secret",
                        lambda name, default=None: "super-secret-live-token" if name == "FIRMS_MAP_KEY" else default)
    out = errors.annotate({
        "ok": False,
        "error": "upstream said API_KEY=super-secret-live-token and also AIzaSyDummyKeyValue",
        "message": "failed with super-secret-live-token",
    })
    blob = " ".join(str(out.get(k)) for k in ("error", "message", "fix"))
    assert "super-secret-live-token" not in blob
    assert "AIzaSyDummyKeyValue" not in blob
    assert "***" in blob or "[redacted]" in blob


def test_checkout_rev_does_not_inherit_host_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "host-only-value")
    root = tmp_path / "gev"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "package.json").write_text('{"version":"1.2.3"}')
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        class Proc:
            returncode = 0
            stdout = "abcdef123456\n"
            stderr = ""
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    version, commit = control_room.checkout_rev(root)
    assert version == "1.2.3"
    assert commit == "abcdef123456"
    env = seen.get("env")
    assert env is not None
    assert "OPENROUTER_API_KEY" not in env
    assert "NODE_OPTIONS" not in env


def test_mcp_client_close_reaps_and_replies(tmp_path):
    script = tmp_path / "stdio_mcp.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    msg = json.loads(line)\n"
        "    method = msg.get('method')\n"
        "    if method == 'notifications/initialized':\n"
        "        continue\n"
        "    if 'id' in msg:\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'method': method}}), flush=True)\n"
    )
    client = mcp_client.StdioMcpClient(argv=[sys.executable, str(script)], timeout=5)
    try:
        assert client.request("ping")["method"] == "ping"
        assert client._proc is not None and client._proc.poll() is None
    finally:
        proc = client._proc
        client.close()
    assert client._proc is None
    assert proc is not None and proc.poll() is not None
    assert client._pending == {}


def _raw_http(host: str, port: int, payload: bytes, timeout: float = 2.0) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)


def test_live_relay_rejects_negative_content_length():
    token = "relay-test-token-value-32charsxxx"
    server = ThreadingHTTPServer(("127.0.0.1", 0), live.make_handler(live.Relay(), token))
    thread = threading.Thread(target=server.serve_forever, name="eoh-audit-live", daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        body = b'{"id":1,"payload":{"ok":true}}'
        raw = (
            b"POST /result HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            + f"Authorization: Bearer {token}\r\n".encode()
            + b"Content-Length: -1\r\n"
            b"\r\n" + body
        )
        reply = _raw_http("127.0.0.1", port, raw)
        assert b" 400 " in reply.split(b"\r\n", 1)[0] or b'"bad body"' in reply
        # A second well-formed unauthenticated request still works: server is alive.
        raw401 = b"POST /result HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 2\r\n\r\n{}"
        reply401 = _raw_http("127.0.0.1", port, raw401)
        assert b" 401 " in reply401.split(b"\r\n", 1)[0]
    finally:
        server.shutdown()
        server.server_close()


def test_hud_bridge_rejects_invalid_content_length():
    def complete(messages, max_tokens):
        raise AssertionError("complete must not run")

    token = "bridge-test-token-value-32charsxx"
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(complete, token))
    thread = threading.Thread(target=server.serve_forever, name="eoh-audit-bridge", daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        raw = (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            + f"Authorization: Bearer {token}\r\n".encode()
            + b"Content-Length: -1\r\n\r\n"
            b'{"messages":[{"role":"user","content":"hi"}]}'
        )
        reply = _raw_http("127.0.0.1", port, raw)
        assert b" 400 " in reply.split(b"\r\n", 1)[0] or b'"bad body"' in reply
    finally:
        server.shutdown()
        server.server_close()
