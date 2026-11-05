"""Live-globe relay: Hermes tools <-> the God's Eye View pane open in Hermes Desktop / the Webapp.

A loopback-only HTTP relay (127.0.0.1:18792, bearer token from the plugin's 0600 env file) that
any Hermes process can reach. Tools POST /cmd and wait; the pane long-polls through the plugin
backend (/api/plugins/godseye/live/next), runs the command in its sandboxed frame with God's Eye
View's own voice-agent action runner, and posts /result. No pane polling = "no pane open".

Each pane polls with its own pane id (and the chat session ids it is showing), so commands go
to the pane bound to the calling session, else the most recently active one. A pane that is
open but did not pick a command up because it is still running an earlier one is "pane busy".
"""
from __future__ import annotations

import hmac
import itertools
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PANE_STALE_S = 30.0
POLL_WAIT_S = 20.0
RESULT_WAIT_S = 60.0
MAX_BODY = 512 * 1024
PICKUP_S = 8.0
DEFAULT_PANE = "default"
NO_PANE = {"ok": False, "error": "no_pane", "message": "No God's Eye View pane is open in Hermes Desktop or the Webapp. Ask the user to open the God's Eye View pane (and keep it visible), then retry."}
PANE_BUSY = {"ok": False, "error": "pane_busy", "message": "The God's Eye View pane is open but still busy with an earlier command; retry in a few seconds."}


def port() -> int:
    return int(os.environ.get("GODSEYE_LIVE_PORT", "18792"))


class Pane:
    def __init__(self, pane_id: str) -> None:
        self.id = pane_id
        self.sessions: set[str] = set()
        self.last_poll = 0.0
        self.polling = 0
        self.queue: list[dict] = []
        self.inflight: set[int] = set()
        self.created = time.monotonic()

    def fresh(self, now: float) -> bool:
        return bool(self.polling or self.inflight) or now - self.last_poll < PANE_STALE_S

    def info(self, now: float) -> dict:
        return {"id": self.id, "sessions": sorted(self.sessions), "lastPollAgeS": 0.0 if self.polling else round(now - self.last_poll, 1),
                "polling": self.polling > 0, "busy": bool(self.inflight), "queued": len(self.queue),
                "open": self.fresh(now)}


class Relay:
    """Routes each command to ONE pane: the pane bound to the calling session, else the most
    recently active pane. Never "whoever polls first". ``fanout=True`` sends to every open pane."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.panes: dict[str, Pane] = {}
        self.results: dict[int, dict] = {}
        self.ids = itertools.count(1)

    # -- pane side -----------------------------------------------------------------------------
    def _pane(self, pane_id: str | None) -> Pane:
        pid = str(pane_id or DEFAULT_PANE)[:80]
        if pid not in self.panes:
            self.panes[pid] = Pane(pid)
        return self.panes[pid]

    def next(self, wait: float = POLL_WAIT_S, pane_id: str | None = None, sessions=None) -> dict | None:
        with self.cond:
            pane = self._pane(pane_id)
            if sessions:
                pane.sessions = {str(s)[:200] for s in sessions if s}
            pane.last_poll = time.monotonic()
            pane.polling += 1
            self.cond.notify_all()
            try:
                deadline = time.monotonic() + wait
                while not pane.queue:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return None
                    self.cond.wait(left)
                    pane.last_poll = time.monotonic()
                cmd = pane.queue.pop(0)
                pane.inflight.add(cmd["id"])
                return cmd
            finally:
                pane.polling -= 1
                pane.last_poll = time.monotonic()

    def result(self, cid: int, payload: dict) -> None:
        with self.cond:
            cid = int(cid)
            self.results[cid] = payload
            for pane in self.panes.values():
                pane.inflight.discard(cid)
            self.cond.notify_all()

    # -- tool side -----------------------------------------------------------------------------
    def open_panes(self) -> list[Pane]:
        now = time.monotonic()
        # ready (polling) panes first, then the most recently active
        return sorted((p for p in self.panes.values() if p.fresh(now)), key=lambda p: (p.polling > 0, p.last_poll), reverse=True)

    def pane_open(self) -> bool:
        return bool(self.open_panes())

    def list(self) -> list[dict]:
        now = time.monotonic()
        with self.cond:
            return [p.info(now) for p in sorted(self.panes.values(), key=lambda p: p.last_poll, reverse=True)]

    def targets(self, session_id: str | None = None, pane_id: str | None = None, fanout: bool = False) -> list[Pane]:
        live = self.open_panes()
        if fanout:
            return live
        if pane_id:
            return [p for p in live if p.id == pane_id][:1]
        if session_id:
            bound = [p for p in live if str(session_id) in p.sessions]
            if bound:
                return bound[:1]
        return live[:1]

    def submit(self, name: str, args: dict, wait: float = RESULT_WAIT_S, session_id: str | None = None,
               pane_id: str | None = None, fanout: bool = False) -> dict:
        with self.cond:
            targets = self.targets(session_id, pane_id, fanout)
            if not targets:
                return dict(NO_PANE)
            sent = []
            for pane in targets:
                cid = next(self.ids)
                pane.queue.append({"id": cid, "name": name, "args": args})
                sent.append((pane, cid))
            self.cond.notify_all()
            out = {pane.id: self._await(pane, cid, wait) for pane, cid in sent}
        if not fanout:
            res = next(iter(out.values()))
            if isinstance(res, dict) and "pane" not in res:
                res = {**res, "pane": sent[0][0].id}
            return res
        return {"ok": any(r.get("ok") for r in out.values()), "fanout": out}

    def _await(self, pane: Pane, cid: int, wait: float) -> dict:
        deadline = time.monotonic() + wait
        pickup = time.monotonic() + PICKUP_S
        while cid not in self.results:
            now = time.monotonic()
            queued = any(c["id"] == cid for c in pane.queue)
            if queued and now >= pickup:  # nobody picked it up
                pane.queue = [c for c in pane.queue if c["id"] != cid]
                return dict(PANE_BUSY if pane.inflight else NO_PANE)
            if now >= deadline:
                pane.inflight.discard(cid)
                return {"ok": False, "error": "timeout", "message": "The God's Eye View pane did not answer in time."}
            self.cond.wait(max(0.05, min(pickup if queued else deadline, deadline) - now))
        return self.results.pop(cid)


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
            url = urllib.parse.urlsplit(self.path)
            q = urllib.parse.parse_qs(url.query)
            if url.path == "/status":
                return self._send(200, {"paneOpen": relay.pane_open(), "panes": relay.list()})
            if url.path == "/next":
                sessions = [s for s in ",".join(q.get("sessions", [])).split(",") if s]
                return self._send(200, {"command": relay.next(pane_id=(q.get("pane") or [None])[0], sessions=sessions)})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            try:
                body = self._body()
            except Exception:
                return self._send(400, {"error": "bad body"})
            if self.path == "/cmd":
                return self._send(200, relay.submit(
                    str(body.get("name", "")), body.get("args") or {}, session_id=body.get("session_id"),
                    pane_id=body.get("pane_id"), fanout=bool(body.get("fanout"))))
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
