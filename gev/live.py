"""Live-globe relay: Hermes tools <-> the God's Eye View pane open in Hermes Desktop / the Webapp.

A loopback-only HTTP relay (127.0.0.1:18792, bearer token from the plugin's 0600 env file) that
any Hermes process can reach. Tools POST /cmd and wait; the pane long-polls through the plugin
backend (/api/plugins/godseye/live/next), runs the command in its sandboxed frame with God's Eye
View's own voice-agent action runner, and posts /result. No pane polling = "no pane open".
"""
from __future__ import annotations

import hmac
import itertools
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PANE_STALE_S = 30.0
POLL_WAIT_S = 20.0
RESULT_WAIT_S = 60.0
MAX_BODY = 512 * 1024
PICKUP_S = 8.0
NO_PANE = {"ok": False, "error": "no_pane", "message": "No God's Eye View pane is open in Hermes Desktop or the Webapp. Ask the user to open the God's Eye View pane (and keep it visible), then retry."}


def port() -> int:
    return int(os.environ.get("GODSEYE_LIVE_PORT", "18792"))


class Relay:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.queue: list[dict] = []
        self.results: dict[int, dict] = {}
        self.last_poll = 0.0
        self.ids = itertools.count(1)

    def pane_open(self) -> bool:
        return time.monotonic() - self.last_poll < PANE_STALE_S

    def submit(self, name: str, args: dict, wait: float = RESULT_WAIT_S) -> dict:
        if not self.pane_open():
            return dict(NO_PANE)
        with self.cond:
            cid = next(self.ids)
            self.queue.append({"id": cid, "name": name, "args": args})
            self.cond.notify_all()
            deadline = time.monotonic() + wait
            pickup = time.monotonic() + PICKUP_S
            while cid not in self.results:
                now = time.monotonic()
                queued = any(c["id"] == cid for c in self.queue)
                if queued and now >= pickup:  # nobody is polling: the pane is closed
                    self.queue = [c for c in self.queue if c["id"] != cid]
                    return dict(NO_PANE)
                if now >= deadline:
                    return {"ok": False, "error": "timeout", "message": "The God's Eye View pane did not answer in time."}
                self.cond.wait(max(0.05, (pickup if queued else deadline) - now))
            return self.results.pop(cid)

    def next(self, wait: float = POLL_WAIT_S) -> dict | None:
        with self.cond:
            self.last_poll = time.monotonic()
            deadline = time.monotonic() + wait
            while not self.queue:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.cond.wait(left)
                self.last_poll = time.monotonic()
            return self.queue.pop(0)

    def result(self, cid: int, payload: dict) -> None:
        with self.cond:
            self.results[int(cid)] = payload
            self.cond.notify_all()


def make_handler(relay: Relay, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def _send(self, code: int, payload) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("too large")
            return json.loads(self.rfile.read(n) or b"{}") if n else {}

        def _authed(self) -> bool:
            return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}")

        def do_GET(self):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            if self.path == "/status":
                return self._send(200, {"paneOpen": relay.pane_open()})
            if self.path == "/next":
                return self._send(200, {"command": relay.next()})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            try:
                body = self._body()
            except Exception:
                return self._send(400, {"error": "bad body"})
            if self.path == "/cmd":
                return self._send(200, relay.submit(str(body.get("name", "")), body.get("args") or {}))
            if self.path == "/result":
                relay.result(body.get("id", 0), body.get("payload") or {})
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
    return Handler


_server = None


def start_in_thread(token: str) -> bool:
    global _server
    if _server is not None:
        return True
    try:
        _server = ThreadingHTTPServer(("127.0.0.1", port()), make_handler(Relay(), token))
    except OSError:
        return False
    threading.Thread(target=_server.serve_forever, name="godseye-live-relay", daemon=True).start()
    return True


def call(path: str, token: str, body: dict | None = None, timeout: float = RESULT_WAIT_S + 10) -> dict:
    url = f"http://127.0.0.1:{port()}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": "relay_unavailable",
                "message": f"The live-globe relay is not running ({type(exc).__name__}); restart the Hermes dashboard/gateway with the godseye plugin enabled."}
