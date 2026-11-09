"""Live-globe relay: Hermes tools <-> the God's Eye View pane open in Hermes Desktop / the Webapp.

A loopback-only HTTP relay (127.0.0.1:18792, bearer token from the plugin's 0600 env file) that
any Hermes process can reach. Tools POST /cmd and wait; the pane long-polls through the plugin
backend (/api/plugins/godseye/live/next), runs the command in its sandboxed frame with God's Eye
View's own voice-agent action runner, and posts /result. No pane polling = "no pane open".

Each pane polls with its own pane id (and the chat session ids it is showing), so commands go
to the pane bound to the calling session, else the most recently focused one the user can see.
A pane that is open but did not pick a command up because it is still running an earlier one
is "pane busy".

Panes may report ``visible`` and ``focused`` (pane heartbeat body, or the ``/next`` query).
Older panes do not, so the flags default to visible / not focused and routing keeps the
previous last-poll order. A hidden pane stays routable when it is the only one, but it never
outranks a visible pane.

The relay waits ``COMMAND_WAIT_S`` for one command. The pane frame's own ceiling
(``PANE_COMMAND_MS`` in desktop/plugin.js) is wider; a relay timeout returns a retryable
``timeout`` with a ``fix`` instead of hanging for two and a half minutes.
"""
from __future__ import annotations

import hmac
import itertools
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .httputil import BadBody, parse_content_length

PANE_STALE_S = 30.0
# desktop/plugin.js keeps its own per-command ceiling (PANE_COMMAND_MS, 150s) for the frame.
# The relay does not wait that long: a hidden or stale pane would block the caller for two and
# a half minutes. COMMAND_WAIT_S is the relay budget (20-30s); PANE_COMMAND_S stays as the
# pane-side ceiling so older callers that read it keep working.
PANE_COMMAND_S = 150.0
COMMAND_WAIT_S = 25.0
RESULT_WAIT_S = PANE_COMMAND_S  # legacy HTTP ceiling; submit() uses COMMAND_WAIT_S by default
INFLIGHT_STALE_S = 30.0  # slightly past COMMAND_WAIT_S so a picked-up command is not pruned first
# A fly_route or street_walk may hitch the page. Heartbeats normally refresh last_poll;
# this is the gap allowed when those heartbeats pause but the pane still says it is busy.
BUSY_STALE_S = 180.0
BUSY_COMMANDS = {"fly_route", "street_walk", "hermes_fly_chunk", "hermes_street_hop", "hermes_route_light"}
LOST_MEMORY_S = 1800.0
POLL_WAIT_S = 20.0
MAX_BODY = 512 * 1024
PICKUP_S = 8.0
BUSY_WAIT_S = 45.0  # extra time a working pane (scene restore, long flight) gets to pick a command up
DEFAULT_PANE = "default"
_DETAIL_KEYS = ("index", "total", "label", "chunk", "chunks", "provider", "stepM")
# ``code`` / ``fix`` sit beside the original ``error`` string so older checks keep working.
NO_PANE = {"ok": False, "error": "no_pane", "code": "no_pane",
           "message": "No Eye Of Hermes (EOH) pane is open in Hermes Desktop or the Webapp. Ask the user to open the Eye Of Hermes (EOH) pane (and keep it visible), then retry.",
           "fix": "Ask the user to open the Eye Of Hermes (EOH) pane and keep it visible, then retry this same call. Data tools (godseye_* without live_) work with no pane."}
PANE_BUSY = {"ok": False, "error": "pane_busy", "code": "pane_busy",
             "message": "The Eye Of Hermes (EOH) pane is open but still busy with an earlier command; retry in a few seconds.",
             "fix": "Wait a few seconds and retry this same call once. Do not send a different live command first, and do not open a second pane."}


def _plain_name(value) -> str:
    text = str(value or "")[:40]
    if text and all(ch.isalnum() or ch == "_" for ch in text):
        return text
    return ""


def _as_bool(value):
    """True/False for a presence flag, or None when the pane did not report it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return None


def _public_detail(detail) -> dict:
    if not isinstance(detail, dict):
        return {}
    out = {}
    for key in _DETAIL_KEYS:
        value = detail.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            out[key] = int(value) if isinstance(value, int) or key in ("index", "total", "chunk", "chunks") else round(float(value), 1)
        elif isinstance(value, str) and key in ("label", "provider"):
            out[key] = value[:80]
    return out


def motion_notice(motion: dict) -> dict:
    """Told to the next turn when a reload cut off a walk or a dolly."""
    kind = str(motion.get("kind") or "route").replace("_", " ")
    label = motion.get("label") or "the route"
    index, total = motion.get("index"), motion.get("total")
    where = ""
    if isinstance(index, int) and isinstance(total, int) and total:
        where = f" It had reached point {index + 1} of {total}."
    resume = ("Offer to resume with godseye_nav op=street_walk walk=resume."
              if motion.get("kind") == "street_walk"
              else "Offer to start the dolly again with godseye_nav op=fly_route.")
    return {
        "ok": False, "code": "MOTION_INTERRUPTED",
        "message": f"A {kind} along {label} was interrupted when the Eye Of Hermes pane reloaded.{where} The saved scene is restored.",
        "fix": "Ask the user to keep the pane open. " + resume,
        "motion": {k: motion.get(k) for k in ("kind", "state", "label", "index", "total", "stepM", "dwellS")
                   if motion.get(k) is not None},
    }


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
        self.snapshot: dict = {}  # last compact map state (heartbeat or a view-state result)
        self.last_heartbeat = 0.0
        self.context_losses = 0
        self.activity: dict = {}  # fly_route / street_walk, held while that job runs
        # Presence reported by the pane chrome. Unknown/old panes stay routable (visible,
        # not focused) so routing falls back to last-poll order.
        self.visible = True
        self.focused = False
        self.last_focus = 0.0
        self.last_visible = 0.0

    def route_rank(self) -> tuple:
        """Visible first, then the most recently focused, then the readiest poller."""
        return (bool(self.visible), bool(self.focused), self.last_focus,
                self.polling > 0, self.last_poll)

    def busy_route(self) -> bool:
        name = (self.activity or {}).get("name")
        return name in BUSY_COMMANDS or bool((self.activity or {}).get("hold"))

    def fresh(self, now: float) -> bool:
        limit = BUSY_STALE_S if self.busy_route() else PANE_STALE_S
        if self.polling or now - self.last_poll < limit:
            return True
        return bool(self.inflight) and now - self.last_poll < max(INFLIGHT_STALE_S, limit)

    def info(self, now: float) -> dict:
        out = {"id": self.id, "sessions": sorted(self.sessions),
               "lastPollAgeS": 0.0 if self.polling else round(max(0.0, now - self.last_poll), 1),
               "polling": self.polling > 0, "busy": bool(self.inflight) or self.busy_route(),
               "queued": len(self.queue), "visible": bool(self.visible), "focused": bool(self.focused),
               "open": self.fresh(now)}
        if self.label:
            out["label"] = self.label
        if self.last_focus:
            out["lastFocusAgeS"] = round(max(0.0, now - self.last_focus), 1)
        if self.context_losses:
            out["contextLosses"] = self.context_losses
        if self.last_heartbeat:
            out["lastHeartbeatAgeS"] = round(max(0.0, now - self.last_heartbeat), 1)
        if (self.activity or {}).get("name"):
            out["activity"] = self.activity["name"]
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
        # Commands the relay stopped waiting for. The pane may still post a late result:
        # drop it instead of growing self.results after every timeout.
        self.abandoned: dict[int, float] = {}
        self.ids = itertools.count(1)
        self.scenes = scenes if scenes is not None else SceneStore(None)
        self.tours: dict[str, object] = {}
        # session_id -> pane_id: stable binding so a reopened pane for the same chat reuses its id
        self.session_binding: dict[str, str] = {}
        # Compact map snapshots that arrived before the pane's first poll.
        self.pending_snaps: dict[str, dict] = {}
        # WebGL loss / recovery notices drained into the next model turn. session -> list.
        self.gl_notices: dict[str, list[dict]] = {}
        # A walk or dolly that a reload cut off. One notice per session, drained once.
        self.motion_notices: dict[str, list[dict]] = {}
        # session or pane id -> last pane that went stale, so the next call can say pane_lost.
        self.lost: dict[str, dict] = {}

    # -- pane side -----------------------------------------------------------------------------
    def _pane(self, pane_id: str | None) -> Pane:
        pid = str(pane_id or DEFAULT_PANE)[:80]
        if pid not in self.panes:
            self.panes[pid] = Pane(pid)
        return self.panes[pid]

    def _apply_presence(self, pane: Pane, visible, focused, now: float) -> None:
        """Record pane chrome presence. Caller holds ``self.cond``.

        At most one pane is focused: a focus report clears the flag on the others but keeps
        their ``last_focus`` so "most recently focused" still has history.
        """
        visible = _as_bool(visible)
        focused = _as_bool(focused)
        if visible is not None:
            pane.visible = visible
            pane.last_visible = now
        if focused is not None:
            if focused:
                for other in self.panes.values():
                    if other is not pane:
                        other.focused = False
                pane.focused = True
                pane.last_focus = now
                pane.last_visible = now
            else:
                pane.focused = False

    def note_presence(self, pane_id: str | None, visible=None, focused=None) -> dict:
        """Visibility/focus report from a pane chrome (no command, no queue)."""
        with self.cond:
            pane = self._pane(pane_id)
            self._apply_presence(pane, visible, focused, time.monotonic())
            return {"ok": True, "pane": pane.info(time.monotonic())}

    def _internal(self, pane: Pane, name: str, args: dict, callback=None) -> None:
        cid = next(self.ids)
        self.callbacks[cid] = callback
        pane.queue.append({"id": cid, "name": name, "args": args})

    def next(self, wait: float = POLL_WAIT_S, pane_id: str | None = None, sessions=None,
             fresh: bool = False, label: str | None = None,
             visible=None, focused=None) -> dict | None:
        with self.cond:
            now = time.monotonic()
            self._prune_locked(now=now)
            pane = self._pane(pane_id)
            self._apply_presence(pane, visible, focused, now)
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
            pending = self.pending_snaps.pop(pane.id, None)
            if pending:
                pane.snapshot = {**(getattr(pane, "snapshot", None) or {}), **pending}
                pane.last_heartbeat = time.monotonic()
            if fresh:  # a new or reloaded frame: the old one cannot answer
                self._fail_inflight_locked(pane)
                for sid in sorted(pane.sessions):
                    self._halt_routes(sid)
                key = self.scenes.pick(sorted(pane.sessions))
                if key:
                    for name, args in self.scenes.restore_commands(key):
                        self._internal(pane, name, args)
                    pane.restored = key
                    motion = self.scenes.interrupt_motion(key)
                    if motion:
                        self._push_motion_locked(pane, key, motion)
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
            if self.abandoned.pop(cid, None) is not None:
                self.cond.notify_all()
                return
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
        # visible first, then the most recently focused, then ready (polling) / newest poll
        return sorted((p for p in self.panes.values() if p.fresh(now)),
                      key=lambda p: p.route_rank(), reverse=True)

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
            self._remember_lost(pane, now)
            removed.append(pid)
            del self.panes[pid]
            for sid, bound in list(self.session_binding.items()):
                if bound == pid:
                    del self.session_binding[sid]
        # Forget abandoned ids whose late result never arrived.
        cutoff = now - max(COMMAND_WAIT_S * 4.0, 120.0)
        for cid, at in list(self.abandoned.items()):
            if at < cutoff:
                del self.abandoned[cid]
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

    def submit(self, name: str, args: dict, wait: float | None = None, session_id: str | None = None,
               pane_id: str | None = None, fanout: bool = False, record: bool = True) -> dict:
        wait = COMMAND_WAIT_S if wait is None else wait
        if name == "annotate_map" and isinstance(args, dict):
            try:
                from . import enrich
                intent = args.get("intent") or args.get("context")
                args = {**args, "annotations": enrich.enrich_annotations(
                    args.get("annotations") or [], intent=intent, mode="light")}
            except Exception:
                args = dict(args or {})
        with self.cond:
            targets = self.targets(session_id, pane_id, fanout)
            if not targets:
                rec = self._fresh_lost(session_id) or self._fresh_lost(pane_id)
                if rec:
                    return self._pane_lost_from_rec(rec)
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
        self._absorb_snapshot(pane, name, args, res)
        if name == "annotate_map" and isinstance(res, dict) and res.get("ok"):
            try:
                from . import enrich
                result = res.get("result") if isinstance(res.get("result"), dict) else res
                intent = (args or {}).get("intent") or (args or {}).get("context")
                enrich.after_annotate(
                    self.scenes, key, args or {}, result or {},
                    intent=intent,
                    live=lambda n, a: self.submit(n, a, session_id=session_id, pane_id=pane.id, record=True))
            except Exception:
                pass
        if name in ("fly_to_location", "move_camera") and isinstance(res, dict) and res.get("ok"):
            with self.cond:  # follow up with the real camera pose (lat/lon/height/pitch)
                self._internal(pane, "get_current_view_state", {},
                               lambda payload, p=pane, k=key: self._noted_view(p, k, payload))
                self.cond.notify_all()

    def _noted_view(self, pane: Pane, key: str, payload: dict) -> None:
        """Scene record + snapshot update. Runs under Relay.cond (from result()); must not re-acquire it."""
        self.scenes.record(key, "get_current_view_state", {}, payload)
        self._absorb_snapshot(pane, "get_current_view_state", {}, payload)

    def _absorb_snapshot(self, pane: Pane, name: str, args: dict, res: dict) -> None:
        """Cache camera and layers from a tool result for the per-turn map line.

        Does not take Relay.cond: the view-state callback runs while that lock is held
        (Condition is not reentrant).
        """
        from .turn_state import absorb_result
        extra = absorb_result(name, args, res)
        if name == "set_layer_visibility" and isinstance(res, dict) and res.get("ok"):
            lid = str((args or {}).get("layerId") or "")[:32]
            current = (pane.snapshot or {}).get("layers")
            if lid and isinstance(current, list):
                layers = [x for x in current if x != lid]
                if (args or {}).get("enabled") is not False:
                    layers.append(lid)
                extra = {**extra, "layers": layers[:12]}
        if extra:
            pane.snapshot = {**(pane.snapshot or {}), **extra}

    def note_state(self, pane_id: str | None, snap: dict | None) -> dict:
        """Store a compact snapshot pushed by the pane (no command, no queue)."""
        from .turn_state import sanitize_snapshot
        pid = str(pane_id or "")[:80]
        clean = sanitize_snapshot(snap if isinstance(snap, dict) else {})
        with self.cond:
            pane = self.panes.get(pid)
            if pane is None:
                self.pending_snaps[pid] = {**self.pending_snaps.get(pid, {}), **clean}
                return {"ok": True, "pending": True}
            pane.snapshot = {**(pane.snapshot or {}), **clean}
            pane.last_heartbeat = time.monotonic()
            return {"ok": True}

    def map_line(self, session_id: str | None = None) -> str:
        """One line for pre_llm_call. Reads the cache; does not enqueue a pane command."""
        from .turn_state import line_from_parts
        with self.cond:
            targets = self.targets(session_id)
            pane = targets[0] if targets else None
            if pane is None:
                return line_from_parts("closed", {}, {})
            status = "busy" if (pane.inflight or pane.queue) else "connected"
            snap = dict(pane.snapshot or {})
            key = self.scene_key(session_id, pane)
        listing = self.scenes.list(key)
        return line_from_parts(status, snap, listing)

    def drawer_event(self, body: dict) -> dict:
        """The user opened/closed a pin drawer by clicking in the pane: keep it in the scene."""
        pane = self.panes.get(str(body.get("pane") or ""))
        if not pane:
            return {"ok": False, "error": "unknown pane"}
        pin = body.get("pin") if isinstance(body.get("pin"), dict) else None
        self.scenes.note_drawer(pane.last_session or self.scene_key(None, pane), pin)
        return {"ok": True}

    def _notice_keys(self, pane: Pane | None, body: dict, scene_key: str | None) -> list[str]:
        keys: set[str] = set()
        if pane:
            keys.update(pane.sessions)
            if pane.last_session:
                keys.add(pane.last_session)
        for sid in body.get("sessions") or []:
            if sid:
                keys.add(str(sid)[:200])
        if scene_key:
            keys.add(scene_key)
        if not keys:
            keys.add(DEFAULT_PANE)
        return sorted(keys)

    def _scene_key_for_gl(self, pane: Pane | None, body: dict) -> str:
        if pane and pane.last_session:
            return pane.last_session
        sessions = sorted(pane.sessions) if pane else []
        picked = self.scenes.pick(sessions)
        if picked:
            return picked
        if sessions:
            return sessions[0]
        for sid in body.get("sessions") or []:
            if sid:
                return str(sid)[:200]
        return DEFAULT_PANE

    def gl_event(self, body: dict) -> dict:
        """Record a context-loss notice, or return restore_commands for a fresh viewer.

        The commands are the scene store's, not a second copy of the scene. They are
        not queued on the voice runner: the pane applies them on the new viewer.
        """
        from .gl_recover import context_lost_notice, context_restored_notice
        event = str(body.get("event") or "")
        pid = str(body.get("pane") or "")[:80]
        with self.cond:
            pane = self.panes.get(pid)
        if event == "restore":
            if pane is None and not (body.get("sessions") or body.get("session_id")):
                return {"ok": False, "error": "unknown pane"}
            key = self._scene_key_for_gl(pane, body)
            cmds = self.scenes.restore_commands(key, clear=True)
            return {"ok": True, "scene": key, "commands": [[name, args] for name, args in cmds]}
        kind = str(body.get("kind") or "")
        if event == "recovered":
            notice = context_restored_notice(body.get("detail") if isinstance(body.get("detail"), dict) else {})
        elif event == "lost":
            if kind not in ("recovering", "exhausted", "manual"):
                kind = "exhausted" if body.get("exhausted") else "recovering"
            notice = context_lost_notice(kind)
        else:
            return {"ok": False, "error": "bad op"}
        key = self._scene_key_for_gl(pane, body)
        keys = self._notice_keys(pane, body, key)
        with self.cond:
            if event == "lost" and pane is not None:
                pane.context_losses = int(getattr(pane, "context_losses", 0)) + 1
            for sid in keys:
                bucket = self.gl_notices.setdefault(sid, [])
                bucket.append(notice)
                del bucket[:-8]
        return notice

    def drain_gl(self, session_id: str | None) -> list[dict]:
        """Notices for this chat, once. An empty session does not drain another chat."""
        if not session_id:
            return []
        with self.cond:
            return self.gl_notices.pop(str(session_id), [])

    def drain_motion(self, session_id: str | None) -> list[dict]:
        """A walk or dolly that a reload cut off, once per chat."""
        if not session_id:
            return []
        with self.cond:
            return self.motion_notices.pop(str(session_id), [])

    def heartbeat(self, pane_id, body: dict | None) -> dict:
        """Refresh last_poll from a timer that does not run a command.

        A heartbeat with busy false must not clear a held fly_route or street_walk.
        The hold is the job, not the command that happens to be in flight.
        """
        body = body if isinstance(body, dict) else {}
        name = _plain_name(body.get("name"))
        busy = bool(body.get("busy")) and bool(name)
        with self.cond:
            pane = self._pane(pane_id)
            now = time.monotonic()
            self._apply_presence(pane, body.get("visible"), body.get("focused"), now)
            pane.last_poll = now
            pane.last_heartbeat = pane.last_poll
            if (pane.activity or {}).get("hold"):
                self.cond.notify_all()
                return {"ok": True, "hold": True}
            if busy:
                pane.activity = {"name": name, "detail": _public_detail(body.get("detail")), "hold": False}
            else:
                pane.activity = {}
            self.cond.notify_all()
            return {"ok": True}

    def hold_activity(self, session_id, name: str, detail=None) -> None:
        """Keep the stale window open for the whole job, including the gaps between chunks."""
        clean = _public_detail(detail)
        with self.cond:
            panes = self.targets(session_id)
            if not panes and session_id:
                bound = self.session_binding.get(str(session_id))
                if bound and bound in self.panes:
                    panes = [self.panes[bound]]
            for pane in panes:
                pane.activity = {"name": name, "detail": clean, "hold": True}
                if clean.get("label"):
                    pane.activity["label"] = clean["label"]
                pane.last_poll = time.monotonic()
            self.cond.notify_all()

    def release_activity(self, session_id, name: str) -> None:
        with self.cond:
            for pane in self.panes.values():
                act = pane.activity or {}
                if not act.get("hold") or act.get("name") != name:
                    continue
                if session_id:
                    sid = str(session_id)
                    bound = self.session_binding.get(sid)
                    if sid not in pane.sessions and pane.last_session != sid and bound != pane.id:
                        continue
                pane.activity = {}

    def lost_result(self, session_id=None, pane_id=None):
        """pane_lost when this chat's pane went stale. None when no pane was ever open."""
        with self.cond:
            rec = self._fresh_lost(session_id) or self._fresh_lost(pane_id)
            if not rec:
                return None
            return self._pane_lost_from_rec(rec)

    def _remember_lost(self, pane: Pane, now: float) -> None:
        rec = {"at": now, "last_poll": pane.last_poll, "pane": pane.id,
               "activity": dict(pane.activity or {}), "sessions": list(pane.sessions)}
        self.lost[pane.id] = rec
        for sid in list(pane.sessions) + ([pane.last_session] if pane.last_session else []):
            if sid:
                self.lost[str(sid)] = rec

    def _fresh_lost(self, key, now: float | None = None):
        if not key:
            return None
        rec = self.lost.get(str(key))
        if not rec:
            return None
        now = time.monotonic() if now is None else now
        if now - float(rec.get("at") or 0) > LOST_MEMORY_S:
            return None
        return rec

    def _pane_lost_from_pane(self, pane: Pane, now: float) -> dict:
        return self._pane_lost_from_rec({
            "at": now, "last_poll": pane.last_poll, "pane": pane.id,
            "activity": dict(pane.activity or {}),
        }, now)

    def _pane_lost_from_rec(self, rec: dict, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        activity = rec.get("activity") or {}
        name = activity.get("name") if isinstance(activity.get("name"), str) else None
        detail = _public_detail(activity.get("detail"))
        if activity.get("label") and "label" not in detail:
            detail = {**detail, "label": str(activity.get("label"))[:80]}
        age = round(max(0.0, now - float(rec.get("last_poll") or now)), 1)
        doing = ""
        index, total = detail.get("index"), detail.get("total")
        if name and isinstance(index, int) and isinstance(total, int) and total:
            doing = f" It was on {name} at point {index + 1} of {total}."
        elif name:
            doing = f" It was on {name}."
        return {
            "ok": False, "error": "pane_lost", "code": "pane_lost",
            "message": "The Eye Of Hermes (EOH) pane stopped responding." + doing
                       + f" Last activity was {age}s ago.",
            "fix": "Ask the user to reopen the Eye Of Hermes (EOH) pane and keep it visible. "
                   "The saved scene restores on its own. If a route walk or dolly was running, "
                   "it was interrupted; offer to resume it.",
            "lastActivity": {"name": name, "detail": detail, "pane": rec.get("pane"), "ageS": age},
        }

    def _fail_inflight_locked(self, pane: Pane) -> None:
        """The old frame is gone. Do not call result(); this already holds the relay lock."""
        now = time.monotonic()
        payload = self._pane_lost_from_pane(pane, now)
        ids = {cmd["id"] for cmd in pane.queue} | set(pane.inflight)
        pane.queue = []
        pane.inflight.clear()
        for cid in ids:
            if cid in self.callbacks:
                cb = self.callbacks.pop(cid)
                if cb:
                    try:
                        cb(dict(payload))
                    except Exception:
                        pass
            else:
                self.results[cid] = dict(payload)
        if ids:
            self.cond.notify_all()

    def _halt_routes(self, session_id: str) -> None:
        try:
            from . import flyroute, streetwalk
            streetwalk.halt(session_id)
            flyroute.halt(session_id)
        except Exception:
            pass

    def _push_motion_locked(self, pane: Pane, key: str, motion: dict) -> None:
        notice = motion_notice(motion)
        keys = set(pane.sessions)
        if key:
            keys.add(str(key))
        if pane.last_session:
            keys.add(pane.last_session)
        for sid in keys:
            if sid:
                self.motion_notices[str(sid)] = [notice]

    def timeline_event(self, body: dict) -> dict:
        """The user moved the time slider. Merge the playhead; keep saved releases and events."""
        pane = self.panes.get(str(body.get("pane") or ""))
        if not pane:
            return {"ok": False, "error": "unknown pane"}
        tl = body.get("timeline") if isinstance(body.get("timeline"), dict) else {}
        self.scenes.note_timeline(pane.last_session or self.scene_key(None, pane), tl, replace=False)
        return {"ok": True}

    def overlays_op(self, body: dict) -> dict:
        """Sidebar and godseye_annotate op=overlays. Does not start the app."""
        from .overlays import handle
        pane = self.panes.get(str((body or {}).get("pane") or ""))
        sessions = (body or {}).get("sessions")
        if isinstance(sessions, str):
            sessions = [item for item in sessions.split(",") if item]
        if not isinstance(sessions, list):
            sessions = []
        sid = (body or {}).get("session_id")
        if pane and (pane.last_session or pane.sessions):
            key = pane.last_session or self.scene_key(sid, pane)
        elif sid:
            key = self.scene_key(sid, pane)
        elif sessions:
            key = self.scenes.pick(sessions) or str(sessions[0])
        else:
            key = self.scene_key(sid if isinstance(sid, str) else None)
        return handle(self.scenes, key, body or {}, relay=self,
                      session_id=sid if isinstance(sid, str) else None)

    def filters_event(self, body: dict) -> dict:
        """The user toggled a pin filter chip in the pane: keep it in the scene."""
        pane = self.panes.get(str(body.get("pane") or ""))
        if not pane:
            return {"ok": False, "error": "unknown pane"}
        f = body.get("filters") if isinstance(body.get("filters"), dict) else {}
        self.scenes.note_filters(pane.last_session or self.scene_key(None, pane), f)
        return {"ok": True}

    # -- scene + tour operations ---------------------------------------------------------------
    def scene_op(self, body: dict) -> dict:
        op, sid = body.get("op"), body.get("session_id")
        targets = self.targets(sid, body.get("pane_id"))
        key = self.scene_key(sid, targets[0] if targets else None)
        if op == "timeline":
            if body.get("clear") or isinstance(body.get("timeline"), dict):
                self.scenes.note_timeline(key, None if body.get("clear") else body.get("timeline"),
                                          replace=bool(body.get("replace", True)))
            tl = None
            with self.scenes.lock:
                tl = self.scenes.scene(key).get("timeline")
            from .timeline import public_timeline, visible_at
            return {"ok": True, "timeline": public_timeline(tl), "visible": visible_at(tl) if tl else None,
                    "stored": tl}
        if op == "list":
            return self.scenes.list(key, full=bool(body.get("full")))
        if op == "update":
            fields = body.get("fields") if isinstance(body.get("fields"), dict) else {}
            ann_id = str(body.get("id") or "")
            if not self.scenes.update_annotation(key, ann_id, fields):
                return {"ok": False, "error": "not_found", "message": f"No annotation {ann_id!r} in this scene.",
                        "fix": "Use an id from godseye_annotate op=list detail=full."}
            out = {"ok": True, "updated": ann_id, "redraw": False}
            if targets:
                step = self.submit("hermes_pin_update", {"id": ann_id, **fields}, session_id=sid,
                                   pane_id=targets[0].id, record=False)
                out["pane"] = targets[0].id
                out["patched"] = bool(isinstance(step, dict) and step.get("ok"))
            return out
        if op == "delete":
            ids = []
            if body.get("id"):
                ids.append(str(body.get("id")))
            if isinstance(body.get("ids"), list):
                ids.extend(str(item) for item in body["ids"] if item)
            ids = list(dict.fromkeys(ids))
            if not ids:
                return {"ok": False, "error": "bad_args", "message": "delete needs id or ids.",
                        "fix": "Pass id, or ids as a list of annotation ids."}
            gone = self.scenes.delete_many(key, ids)
            if not gone:
                listing = self.scenes.list(key)
                return {"ok": False, "error": "not_found", "message": f"No annotation {ids[0]!r} in this scene.",
                        "annotations": listing["annotations"]}
            out = {"ok": True, "deleted": gone[0] if len(gone) == 1 else gone,
                   "removed": len(gone), "remaining": len(self.scenes.list(key)["annotations"])}
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
                if pane.inflight and now < pickup + BUSY_WAIT_S and now < deadline and pane.fresh(now):
                    self.cond.wait(0.25)  # alive and working through earlier commands: keep our place
                    continue
                pane.queue = [c for c in pane.queue if c["id"] != cid]
                if not pane.fresh(now):
                    self._remember_lost(pane, now)
                    return self._pane_lost_from_pane(pane, now)
                return dict(PANE_BUSY if pane.inflight else NO_PANE)
            if now >= deadline:
                pane.inflight.discard(cid)
                if any(c["id"] == cid for c in pane.queue):  # never started: do not run it late
                    pane.queue = [c for c in pane.queue if c["id"] != cid]
                else:
                    self.abandoned[cid] = now
                if not pane.fresh(now):
                    self._remember_lost(pane, now)
                    return self._pane_lost_from_pane(pane, now)
                budget = max(1, int(round(wait)))
                return {"ok": False, "error": "timeout", "code": "timeout",
                        "message": f"The Eye Of Hermes (EOH) pane did not answer in time ({budget}s budget).",
                        "fix": "Retry this call once. If it times out again, call godseye_view op=panes "
                               "to check which pane is open, focused, and visible, then godseye_admin "
                               "op=doctor before changing arguments.",
                        "budgetS": budget}
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

        def _body(self, size: int | None = None) -> dict:
            n = parse_content_length(self.headers.get("Content-Length") or 0, MAX_BODY) if size is None else size
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
                return self._send(200, {"command": relay.next(
                    pane_id=(q.get("pane") or [None])[0], sessions=sessions,
                    fresh=(q.get("fresh") or ["0"])[0] == "1", label=label,
                    visible=_as_bool((q.get("visible") or [None])[0]),
                    focused=_as_bool((q.get("focused") or [None])[0]))})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            try:
                size = parse_content_length(self.headers.get("Content-Length") or 0)
            except BadBody:
                return self._send(400, {"error": "bad body"})
            path = urllib.parse.urlsplit(self.path).path
            if path == "/result":
                logging.getLogger(__name__).info("EOH result bytes=%d", size)
            if size > MAX_BODY and path == "/result":
                # Drain a bounded upload before replying, avoiding a broken pipe at the sender.
                self.connection.settimeout(5)
                try:
                    self.rfile.read(min(size, 8 * 1024 * 1024))
                except (OSError, TimeoutError):
                    pass
                error = {"ok": False, "error": "result too large", "code": "result_too_large",
                         "resultBytes": size, "limitBytes": MAX_BODY}
                try:
                    cid = int(self.headers.get("X-Hermes-Command-Id", "0"))
                    if cid > 0:
                        relay.result(cid, error)
                except (TypeError, ValueError):
                    pass
                self.close_connection = True
                return self._send(413, error)
            if size > MAX_BODY:
                return self._send(413, {"error": "body too large", "limitBytes": MAX_BODY})
            try:
                body = self._body(size)
            except Exception:
                return self._send(400, {"error": "bad body"})
            if path == "/cmd":
                return self._send(200, relay.submit(
                    str(body.get("name", "")), body.get("args") or {}, session_id=body.get("session_id"),
                    pane_id=body.get("pane_id"), fanout=bool(body.get("fanout")),
                    record=body.get("record") is not False))
            if path == "/heartbeat":
                return self._send(200, relay.heartbeat(body.get("pane"), body))
            if path == "/scene":
                return self._send(200, relay.scene_op(body))
            if path == "/tour":
                return self._send(200, relay.tour_op(body))
            if path == "/panes":
                return self._send(200, relay.panes_op(body))
            if path == "/filters":
                return self._send(200, relay.filters_event(body))
            if path == "/overlays":
                return self._send(200, relay.overlays_op(body))
            if path == "/timeline":
                return self._send(200, relay.timeline_event(body))
            if path == "/drawer":
                return self._send(200, relay.drawer_event(body))
            if path == "/state":
                return self._send(200, relay.note_state(body.get("pane"), body.get("state") if isinstance(body.get("state"), dict) else body))
            if path == "/gl":
                return self._send(200, relay.gl_event(body))
            if path == "/mapstate":
                return self._send(200, {"ok": True, "line": relay.map_line(body.get("session_id"))})
            if path == "/result":
                relay.result(body.get("id", 0), body.get("payload") or {})
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
    return Handler


_server = None
_relay = None


def current():
    """The relay started in this process, or None. Tests and the turn hook use this."""
    return _relay


def start_in_thread(token: str) -> bool:
    global _server, _relay
    if _server is not None:
        return True
    try:
        from .scene import SceneStore, default_path
        _relay = Relay(SceneStore(default_path()))
        _server = ThreadingHTTPServer(("127.0.0.1", port()), make_handler(_relay, token))
    except OSError:
        return False
    threading.Thread(target=_server.serve_forever, name="godseye-live-relay", daemon=True).start()
    return True


def call(path: str, token: str, body: dict | None = None, timeout: float = RESULT_WAIT_S + 10) -> dict:
    url = f"http://127.0.0.1:{port()}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    if path == "/result" and body:
        headers["X-Hermes-Command-Id"] = str(body.get("id", 0))
    req = urllib.request.Request(url, data, headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 413:
            return {"ok": False, "error": "result too large", "code": "result_too_large"}
        return {"ok": False, "error": "relay_http_error", "code": "relay_http_error", "status": exc.code}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "error": "relay_unavailable", "code": "relay_unavailable",
                "message": f"The live-globe relay is not running ({type(exc).__name__}); restart the Hermes dashboard/gateway with the godseye plugin enabled.",
                "fix": "Restart the Hermes dashboard or gateway with the godseye plugin enabled, then retry."}
