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
INFLIGHT_STALE_S = 170.0  # longer than the pane's own 150 s command timeout
POLL_WAIT_S = 20.0
RESULT_WAIT_S = 60.0
MAX_BODY = 512 * 1024
PICKUP_S = 8.0
BUSY_WAIT_S = 45.0  # extra time a working pane (scene restore, long flight) gets to pick a command up
DEFAULT_PANE = "default"
NO_PANE = {"ok": False, "error": "no_pane", "message": "No Eye Of Hermes (EOH) pane is open in Hermes Desktop or the Webapp. Ask the user to open the Eye Of Hermes (EOH) pane (and keep it visible), then retry."}
PANE_BUSY = {"ok": False, "error": "pane_busy", "message": "The Eye Of Hermes (EOH) pane is open but still busy with an earlier command; retry in a few seconds."}


def port() -> int:
    return int(os.environ.get("GODSEYE_LIVE_PORT", "18792"))


class Pane:
    def __init__(self, pane_id: str) -> None:
        self.id = pane_id
        self.sessions: set[str] = set()
        self.last_session: str | None = None  # session whose scene this pane last drew (for click events)
        self.last_poll = 0.0
        self.polling = 0
        self.queue: list[dict] = []
        self.inflight: set[int] = set()
        self.created = time.monotonic()
        self.restored: str | None = None
        self.label: str | None = None

    def fresh(self, now: float) -> bool:
        if self.polling or now - self.last_poll < PANE_STALE_S:
            return True
        return bool(self.inflight) and now - self.last_poll < INFLIGHT_STALE_S  # a working pane, not a ghost

    def info(self, now: float) -> dict:
        out = {"id": self.id, "sessions": sorted(self.sessions),
               "lastPollAgeS": 0.0 if self.polling else round(max(0.0, now - self.last_poll), 1),
               "polling": self.polling > 0, "busy": bool(self.inflight), "queued": len(self.queue),
               "open": self.fresh(now)}
        if self.label:
            out["label"] = self.label
        return out


class Relay:
    """Routes each command to ONE pane: the pane bound to the calling session, else the most
    recently active pane. Never "whoever polls first". ``fanout=True`` sends to every open pane."""

    def __init__(self, scenes=None) -> None:
        from .scene import SceneStore
        self.cond = threading.Condition()
        self.panes: dict[str, Pane] = {}
        self.results: dict[int, dict] = {}
        self.callbacks: dict[int, object] = {}  # internal commands: result goes to a callback
        self.ids = itertools.count(1)
        self.scenes = scenes if scenes is not None else SceneStore(None)
        self.tours: dict[str, object] = {}
        # session_id -> pane_id: stable binding so a reopened pane for the same chat reuses its id
        self.session_binding: dict[str, str] = {}

    # -- pane side -----------------------------------------------------------------------------
    def _pane(self, pane_id: str | None) -> Pane:
        pid = str(pane_id or DEFAULT_PANE)[:80]
        if pid not in self.panes:
            self.panes[pid] = Pane(pid)
        return self.panes[pid]

    def _internal(self, pane: Pane, name: str, args: dict, callback=None) -> None:
        cid = next(self.ids)
        self.callbacks[cid] = callback
        pane.queue.append({"id": cid, "name": name, "args": args})

    def next(self, wait: float = POLL_WAIT_S, pane_id: str | None = None, sessions=None,
             fresh: bool = False, label: str | None = None) -> dict | None:
        with self.cond:
            self._prune_locked(now=time.monotonic())
            pane = self._pane(pane_id)
            if label is not None:
                pane.label = str(label).strip()[:80] or None
            if sessions:
                claimed = {str(s)[:200] for s in sessions if s}
                # One pane per session by default: this pane takes the sessions; drop them elsewhere.
                for other in self.panes.values():
                    if other is pane:
                        continue
                    other.sessions -= claimed
                pane.sessions = claimed
                for sid in claimed:
                    self.session_binding[sid] = pane.id
            if fresh:  # a new or reloaded frame: bring its scene back before anything else
                key = self.scenes.pick(sorted(pane.sessions))
                if key:
                    for name, args in self.scenes.restore_commands(key):
                        self._internal(pane, name, args)
                    pane.restored = key
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
            for pane in self.panes.values():
                pane.inflight.discard(cid)
            if cid in self.callbacks:
                cb = self.callbacks.pop(cid)
                if cb:
                    try:
                        cb(payload)
                    except Exception:
                        pass
            else:
                self.results[cid] = payload
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
            self._prune_locked(now=now)  # drop stale closed records every status/list
            return [p.info(now) for p in sorted(self.panes.values(), key=lambda p: p.last_poll, reverse=True)]

    def _prune_locked(self, now: float | None = None) -> list[str]:
        """Drop closed/stale pane records. Never removes a still-fresh (polling/working) pane."""
        now = time.monotonic() if now is None else now
        removed = []
        for pid, pane in list(self.panes.items()):
            if pane.fresh(now):
                continue
            removed.append(pid)
            del self.panes[pid]
            for sid, bound in list(self.session_binding.items()):
                if bound == pid:
                    del self.session_binding[sid]
        return removed

    def prune(self) -> dict:
        with self.cond:
            removed = self._prune_locked()
            return {"ok": True, "removed": removed, "panes": [p.info(time.monotonic()) for p in self.panes.values()]}

    def set_label(self, pane_id: str | None, label: str | None, session_id: str | None = None) -> dict:
        with self.cond:
            pane = None
            if pane_id and pane_id in self.panes:
                pane = self.panes[pane_id]
            elif session_id:
                prefer = self.session_binding.get(str(session_id))
                pane = self.panes.get(prefer) if prefer else None
                if pane is None:
                    hits = self.targets(session_id)
                    pane = hits[0] if hits else None
            if pane is None and self.open_panes():
                pane = self.open_panes()[0]
            if pane is None:
                return dict(NO_PANE)
            pane.label = (str(label).strip()[:80] or None) if label is not None else pane.label
            return {"ok": True, "pane": pane.info(time.monotonic())}

    def panes_op(self, body: dict) -> dict:
        """List / prune / label pane records. ``{prune:true}`` drops stale closed ones."""
        if body.get("prune"):
            out = self.prune()
        else:
            with self.cond:
                self._prune_locked()  # auto-prune on every list
                out = {"ok": True, "panes": [p.info(time.monotonic()) for p in
                                            sorted(self.panes.values(), key=lambda p: p.last_poll, reverse=True)],
                       "bindings": dict(self.session_binding)}
        label = body.get("label")
        if label is not None or body.get("pane_id"):
            labeled = self.set_label(body.get("pane_id"), label, body.get("session_id"))
            out["label"] = labeled
            if not labeled.get("ok"):
                out["ok"] = False
                out["error"] = labeled.get("error")
                out["message"] = labeled.get("message")
        return out

    def targets(self, session_id: str | None = None, pane_id: str | None = None, fanout: bool = False) -> list[Pane]:
        live = self.open_panes()
        if fanout:
            return live
        if pane_id:
            return [p for p in live if p.id == pane_id][:1]
        if session_id:
            sid = str(session_id)
            bound = [p for p in live if sid in p.sessions]
            if bound:
                return bound[:1]
            # Stable binding: prefer the pane id last registered for this session, if still open.
            prefer = self.session_binding.get(sid)
            if prefer:
                hit = [p for p in live if p.id == prefer]
                if hit:
                    return hit[:1]
        return live[:1]

    def submit(self, name: str, args: dict, wait: float = RESULT_WAIT_S, session_id: str | None = None,
               pane_id: str | None = None, fanout: bool = False, record: bool = True) -> dict:
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
        if record:
            for pane, _cid in sent:
                self._record(session_id, pane, name, args, out[pane.id])
        if not fanout:
            res = next(iter(out.values()))
            if isinstance(res, dict) and "pane" not in res:
                res = {**res, "pane": sent[0][0].id}
            return res
        return {"ok": any(r.get("ok") for r in out.values()), "fanout": out}

    def scene_key(self, session_id: str | None, pane: Pane | None = None) -> str:
        from .scene import DEFAULT_KEY
        if session_id:
            return str(session_id)
        if pane and pane.sessions:
            return sorted(pane.sessions)[0]
        return DEFAULT_KEY

    def _record(self, session_id, pane: Pane, name: str, args: dict, res: dict) -> None:
        key = self.scene_key(session_id, pane)
        pane.last_session = key
        self.scenes.record(key, name, args, res)
        if name in ("fly_to_location", "move_camera") and isinstance(res, dict) and res.get("ok"):
            with self.cond:  # follow up with the real camera pose (lat/lon/height/pitch)
                self._internal(pane, "get_current_view_state", {},
                               lambda payload: self.scenes.record(key, "get_current_view_state", {}, payload))
                self.cond.notify_all()

    def drawer_event(self, body: dict) -> dict:
        """The user opened/closed a pin drawer by clicking in the pane: keep it in the scene."""
        pane = self.panes.get(str(body.get("pane") or ""))
        if not pane:
            return {"ok": False, "error": "unknown pane"}
        pin = body.get("pin") if isinstance(body.get("pin"), dict) else None
        self.scenes.note_drawer(pane.last_session or self.scene_key(None, pane), pin)
        return {"ok": True}

    # -- scene + tour operations ---------------------------------------------------------------
    def scene_op(self, body: dict) -> dict:
        op, sid = body.get("op"), body.get("session_id")
        targets = self.targets(sid, body.get("pane_id"))
        key = self.scene_key(sid, targets[0] if targets else None)
        if op == "list":
            return self.scenes.list(key)
        if op == "delete":
            if not self.scenes.delete(key, str(body.get("id") or "")):
                listing = self.scenes.list(key)
                return {"ok": False, "error": "not_found", "message": f"No annotation {body.get('id')!r} in this scene.",
                        "annotations": listing["annotations"]}
            out = {"ok": True, "deleted": body.get("id"), "remaining": len(self.scenes.list(key)["annotations"])}
            if targets:  # redraw what is left on the pane (no geocoding: stored coordinates)
                steps = [self.submit(n, a, session_id=sid, pane_id=targets[0].id, record=False)
                         for n, a in self.scenes.restore_commands(key, redraw_only=True)]
                out["pane"] = targets[0].id
                out["redrawn"] = all(r.get("ok") for r in steps)
            return out
        if op == "restore":
            if not targets:
                return dict(NO_PANE)
            steps = [(n, self.submit(n, a, session_id=sid, pane_id=targets[0].id, record=False, wait=90))
                     for n, a in self.scenes.restore_commands(key, clear=True)]
            return {"ok": all(r.get("ok") for _, r in steps), "pane": targets[0].id, "scene": key,
                    "steps": [{"name": n, "ok": bool(r.get("ok")), **({"error": r.get("error") or (r.get("result") or {}).get("error")} if not r.get("ok") else {})} for n, r in steps],
                    "annotations": len(self.scenes.list(key)["annotations"])}
        return {"ok": False, "error": "bad op"}

    def tour_op(self, body: dict) -> dict:
        from . import tour
        op, sid = body.get("op"), body.get("session_id")
        if op == "start":
            if not self.targets(sid):
                return dict(NO_PANE)
            try:
                spec = tour.plan(body.get("args") or {})
            except ValueError as exc:
                return {"ok": False, "error": "bad_tour", "message": str(exc)}
            for t in self.tours.values():  # one tour at a time per session
                if getattr(t, "session", None) == sid and t.state in ("pending", "running"):
                    t.cancel()
            job = tour.Tour(spec, lambda n, a: self.submit(n, a, session_id=sid, wait=90))
            job.session = sid
            self.tours[job.id] = job
            threading.Thread(target=job, name=job.id, daemon=True).start()
            return {"ok": True, "jobId": job.id, "stops": len(spec["stops"]), "framing": spec["framing"],
                    "dwellMs": spec["dwellMs"], "estimatedS": round(len(spec["stops"]) * (spec["dwellMs"] / 1000 + 8))}
        job = self.tours.get(str(body.get("jobId") or ""))
        if job is None and op == "cancel" and not body.get("jobId"):
            live = [t for t in self.tours.values() if t.state in ("pending", "running")]
            job = live[-1] if live else None
        if job is None:
            return {"ok": False, "error": "not_found", "message": "No such tour job."}
        if op == "cancel":
            job.cancel()
        return job.status()

    def _await(self, pane: Pane, cid: int, wait: float) -> dict:
        deadline = time.monotonic() + wait
        pickup = time.monotonic() + PICKUP_S
        while cid not in self.results:
            now = time.monotonic()
            queued = any(c["id"] == cid for c in pane.queue)
            if queued and now >= pickup:  # nobody picked it up
                if pane.inflight and now < pickup + BUSY_WAIT_S and now < deadline:
                    self.cond.wait(0.25)  # alive and working through earlier commands: keep our place
                    continue
                pane.queue = [c for c in pane.queue if c["id"] != cid]
                return dict(PANE_BUSY if pane.inflight else NO_PANE)
            if now >= deadline:
                pane.inflight.discard(cid)
                return {"ok": False, "error": "timeout", "message": "The Eye Of Hermes (EOH) pane did not answer in time."}
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
                label = (q.get("label") or [None])[0]
                return self._send(200, {"command": relay.next(pane_id=(q.get("pane") or [None])[0], sessions=sessions,
                                                              fresh=(q.get("fresh") or ["0"])[0] == "1",
                                                              label=label)})
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
            if self.path == "/scene":
                return self._send(200, relay.scene_op(body))
            if self.path == "/tour":
                return self._send(200, relay.tour_op(body))
            if self.path == "/panes":
                return self._send(200, relay.panes_op(body))
            if self.path == "/drawer":
                return self._send(200, relay.drawer_event(body))
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
        from .scene import SceneStore, default_path
        _server = ThreadingHTTPServer(("127.0.0.1", port()), make_handler(Relay(SceneStore(default_path())), token))
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
