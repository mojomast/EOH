"""Loopback OpenAI-compatible bridge so God's Eye View's own HUD uses Hermes inference.

Serves POST /v1/chat/completions on 127.0.0.1 only, bearer-token gated, and answers with
Hermes' host-owned plugin LLM (agent.plugin_llm.PluginLlm, the same lane as ctx.llm).
God's Eye View points GEV_HUD_SUMMARY_BASE_URL at it. Text only, no tools, no streaming.

Run:  <hermes python> -m gev.bridge   (see scripts/hud-bridge.sh; the plugin records the path)
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 64 * 1024
MAX_TOKENS = 400


def data_dir() -> Path:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    path = home / "plugin-data" / "godseye"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_token(path: Path | None = None) -> str:
    """Token shared with God's Eye View through an 0600 env file, never logged."""
    path = path or data_dir() / "hud-bridge.env"
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.startswith("GEV_HUD_SUMMARY_API_KEY="):
                return line.split("=", 1)[1]
    token = secrets.token_urlsafe(32)
    port = os.environ.get("GODSEYE_BRIDGE_PORT", "18791")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"GEV_HUD_SUMMARY_BASE_URL=http://127.0.0.1:{port}/v1\n"
                 f"GEV_HUD_SUMMARY_API_KEY={token}\nGEV_HUD_SUMMARY_MODEL=hermes\n")
    return token


def flatten(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def make_handler(complete, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # no request logging (bodies/headers stay private)
            pass

        def _send(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, f"Bearer {token}"):
                return self._send(401, {"error": "unauthorized"})
            if self.path.rstrip("/") != "/v1/chat/completions":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(length))
                messages = [{"role": m.get("role", "user"), "content": flatten(m.get("content"))}
                            for m in body.get("messages", []) if m.get("role") in ("system", "user", "assistant")]
                if not messages:
                    return self._send(400, {"error": "messages required"})
                text = complete(messages, min(int(body.get("max_tokens") or 200), MAX_TOKENS))
            except BaseException as exc:  # noqa: BLE001 - host may raise SystemExit-like errors
                import traceback; traceback.print_exc()
                print(f"inference failed: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr, flush=True)
                return self._send(502, {"error": f"inference failed: {type(exc).__name__}"})
            self._send(200, {"id": f"gev-{int(time.time()*1000)}", "object": "chat.completion",
                             "model": "hermes", "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}]})
    return Handler


def hermes_complete():
    from agent.plugin_llm import PluginLlm
    llm = PluginLlm(plugin_id="godseye")

    def complete(messages, max_tokens):
        return llm.complete(messages=messages, max_tokens=max_tokens, temperature=0.2,
                            purpose="godseye.hud-summary").text or ""
    return complete


_server = None


def start_in_thread() -> bool:
    """Start once inside a long-lived Hermes process (the dashboard/desktop backend imports
    plugin_api.py). A port already in use means another process serves it; that is fine."""
    global _server
    if _server is not None or os.environ.get("GODSEYE_HUD_BRIDGE", "1") == "0":
        return _server is not None
    import threading
    port = int(os.environ.get("GODSEYE_BRIDGE_PORT", "18791"))
    try:
        _server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hermes_complete(), ensure_token()))
    except OSError:
        return False
    threading.Thread(target=_server.serve_forever, name="godseye-hud-bridge", daemon=True).start()
    return True


def start_live_relay() -> bool:
    from . import live
    return live.start_in_thread(ensure_token())


def main() -> None:
    agent_dir = os.environ.get("HERMES_AGENT_DIR")
    if agent_dir and agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    port = int(os.environ.get("GODSEYE_BRIDGE_PORT", "18791"))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hermes_complete(), ensure_token()))
    print(f"godseye HUD bridge on 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
