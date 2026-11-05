"""Street View route walk for godseye_nav op=street_walk.

The sub-action is the argument ``walk`` (start, pause, resume, step, stop,
state), the same shape as the time slider. One hop is one short pane command:
open the nearest panorama, aim the heading along the route, return. The server
dwells, then sends the next hop. The globe stays coarse for the whole walk.
"""
from __future__ import annotations

import threading

from .routegeom import WALK_STEP_M, walk_samples
from .streetlevel import nearest

WALKS = ("start", "pause", "resume", "step", "stop", "state")
_jobs: dict[str, "Walk"] = {}
_lock = threading.Lock()


def halt(session_id: str | None) -> None:
    """Stop the in-memory walk. A reloaded pane must not keep receiving hops."""
    with _lock:
        job = _jobs.get(str(session_id or ""))
    if job is not None:
        job.stop(interrupted=True)


class Walk:
    def __init__(self, samples: list[dict], *, label, step_m, dwell_s, provider, points, start: int = 0) -> None:
        self.samples = samples
        self.label = label
        self.step_m = step_m
        self.dwell_s = dwell_s
        self.provider = provider
        self.points = points
        self.index = max(0, min(int(start), len(samples)))
        self.state = "running"
        self.misses = 0
        self.last: dict = {}
        self.error = None
        self._pause = False
        self._stop = False
        self._interrupted = False
        self.cond = threading.Condition()

    def snapshot(self, state: str | None = None) -> dict:
        body = {
            "kind": "street_walk",
            "state": state or self.state,
            "label": self.label,
            "index": self.index,
            "total": len(self.samples),
            "stepM": self.step_m,
            "dwellS": self.dwell_s,
            "provider": self.last.get("provider") or self.provider,
            "alongM": self.last.get("alongM"),
            "lengthM": self.samples[-1]["alongM"] if self.samples else 0,
            "misses": self.misses,
        }
        if not self.label and self.points:
            body["points"] = [[round(p[0], 5), round(p[1], 5)] for p in self.points[:2000]]
        return body

    def public(self) -> dict:
        shown = self.last or (self.samples[self.index] if self.index < len(self.samples) else (self.samples[-1] if self.samples else {}))
        out = {
            "ok": self.error is None or self.state in ("paused", "stopped", "done", "interrupted", "running"),
            "op": "street_walk",
            "state": self.state,
            "index": self.index,
            "total": len(self.samples),
            "stepM": self.step_m,
            "dwellS": self.dwell_s,
            "label": self.label,
            "lengthM": self.samples[-1]["alongM"] if self.samples else 0,
            "alongM": shown.get("alongM"),
            "lat": shown.get("lat"),
            "lon": shown.get("lon"),
            "heading": shown.get("heading"),
            "provider": self.last.get("provider"),
            "panoId": self.last.get("panoId"),
            "misses": self.misses,
        }
        if self.last.get("fallback"):
            out["fallback"] = self.last["fallback"]
        if self.state == "interrupted":
            out["interrupted"] = True
            out["message"] = "The street walk was interrupted. The saved scene is still there."
            out["fix"] = "Reopen the Eye Of Hermes pane if it closed, then godseye_nav op=street_walk walk=resume to continue this route."
        if self.error and self.state not in ("running", "paused", "done"):
            out["ok"] = False
            out["error"] = self.error.get("error") or "street_walk failed"
            if self.error.get("message"):
                out["message"] = self.error["message"]
        return out

    def pause(self) -> None:
        with self.cond:
            self._pause = True
            self.state = "paused"
            self.cond.notify_all()

    def resume(self) -> None:
        with self.cond:
            self._pause = False
            self._stop = False
            self._interrupted = False
            if self.state in ("paused", "interrupted"):
                self.state = "running"
            self.cond.notify_all()

    def stop(self, interrupted: bool = False) -> None:
        with self.cond:
            self._stop = True
            self._pause = False
            self._interrupted = interrupted
            self.state = "interrupted" if interrupted else "stopped"
            self.cond.notify_all()

    def _wait_if_paused(self) -> str | None:
        """'stop', 'interrupted', or None when the walk should take the next hop."""
        with self.cond:
            while self._pause and not self._stop:
                self.state = "paused"
                self.cond.wait(0.2)
            if self._stop:
                return "interrupted" if self._interrupted else "stop"
            return None

    def run(self, hop, note, sleep, *, one: bool = False) -> None:
        while self.index < len(self.samples):
            why = self._wait_if_paused()
            if why:
                self.state = "interrupted" if why == "interrupted" else "stopped"
                note(self.snapshot(self.state))
                return
            sample = self.samples[self.index]
            self.state = "running"
            note(self.snapshot("running"))
            result = hop(sample, self.index, len(self.samples)) or {}
            with self.cond:
                if result.get("error") in ("pane_lost", "no_pane", "timeout"):
                    self.error = result
                    self.state = "interrupted"
                    self.last = {**sample, **result}
                    note(self.snapshot("interrupted"))
                    return
                self.last = {**sample, **result}
                if not result.get("panoId"):
                    self.misses += 1
                self.index += 1
            if one or self._stop:
                with self.cond:
                    if self._interrupted:
                        self.state = "interrupted"
                    elif self._stop:
                        self.state = "stopped"
                    elif one:
                        self._pause = True
                        self.state = "paused"
                note(self.snapshot(self.state))
                return
            self._dwell(sleep)
        self.state = "done"
        note(None)


    def _dwell(self, sleep) -> None:
        left = float(self.dwell_s or 0)
        while left > 0:
            with self.cond:
                if self._stop or self._pause:
                    return
            step = min(0.25, left)
            sleep(step)
            left -= step


def _job(session_id: str) -> Walk | None:
    with _lock:
        return _jobs.get(str(session_id or ""))


def _put(session_id: str, job: Walk) -> None:
    with _lock:
        _jobs[str(session_id or "")] = job


def _drop(session_id: str, job: Walk) -> None:
    with _lock:
        if _jobs.get(str(session_id or "")) is job:
            _jobs.pop(str(session_id or ""), None)


def _relay(relay):
    if relay is not None:
        return relay
    from . import live
    return live.current()


def _scene_key(relay, session_id):
    targets = relay.targets(session_id) if relay else []
    pane = targets[0] if targets else None
    if relay is None:
        return str(session_id or "default")
    return relay.scene_key(session_id, pane)


def _annotations(relay, session_id):
    if relay is None:
        return []
    return relay.scenes.annotations(_scene_key(relay, session_id))


def _note(relay, session_id, motion) -> None:
    if relay is None:
        return
    relay.scenes.note_motion(_scene_key(relay, session_id), motion)


def _call(relay, session_id, name, args, record=False):
    from . import composer, live
    if relay is not None:
        return relay.submit(name, args, session_id=session_id, record=record)
    body = {"name": name, "args": args}
    if session_id:
        body["session_id"] = str(session_id)
    if record is False:
        body["record"] = False
    return live.call("/cmd", composer._token(), body)


def _clamp_dwell(value) -> float:
    try:
        dwell = float(value if value is not None else 4)
    except (TypeError, ValueError):
        dwell = 4
    return max(0.0, min(30.0, dwell))


def _clamp_step(value) -> float:
    from .routegeom import WALK_STEP_MAX_M, WALK_STEP_MIN_M
    try:
        step = float(value if value is not None else WALK_STEP_M)
    except (TypeError, ValueError):
        step = WALK_STEP_M
    return max(WALK_STEP_MIN_M, min(WALK_STEP_MAX_M, step))


def _source_points(args: dict, annotations) -> tuple[list, str | None]:
    from .flyroute import resolve_points
    return resolve_points(args, annotations)


def _stored_motion(relay, session_id) -> dict | None:
    if relay is None:
        return None
    motion = relay.scenes.scene(_scene_key(relay, session_id)).get("motion")
    return motion if isinstance(motion, dict) and motion.get("kind") == "street_walk" else None


def _prepare(args, annotations, motion) -> tuple[list[dict], list, str | None, float, float, str | None, str | None]:
    """Samples plus the route they came from. A stored walk supplies anything the call omits."""
    from .routegeom import as_points
    merged = dict(motion or {})
    merged.update({k: v for k, v in (args or {}).items() if v not in (None, "")})
    points, label = _source_points(merged, annotations)
    if len(points) < 2 and merged.get("points"):
        points = as_points(merged.get("points"))
    step = _clamp_step(merged.get("stepM"))
    dwell = _clamp_dwell(merged.get("dwellS") if "dwellS" in (args or {}) or not motion else motion.get("dwellS"))
    if args and args.get("dwellS") is not None:
        dwell = _clamp_dwell(args.get("dwellS"))
    provider = merged.get("provider")
    if provider not in ("google", "mapillary", None):
        provider = None
    samples = walk_samples(points, step)
    return samples, points, label, step, dwell, provider, None if samples else "A street walk needs a saved route label, or at least two waypoints."


def run(args: dict | None, session_id: str | None = None, *, relay=None, lookup=None, sync: bool = False,
        sleep=None) -> dict:
    args = dict(args or {})
    action = str(args.get("op") or args.get("walk") or "state").lower()
    if action not in WALKS:
        return {
            "ok": False, "error": "bad_args",
            "message": f"street_walk action {action!r} is not one of {', '.join(WALKS)}.",
            "fix": "Retry godseye_nav op=street_walk with walk set to start, pause, resume, step, stop, or state.",
        }
    relay = _relay(relay)
    session = str(session_id or "")
    job = _job(session)
    if action == "state":
        if job is not None:
            return job.public()
        motion = _stored_motion(relay, session_id)
        if not motion:
            return {"ok": True, "op": "street_walk", "state": "idle", "index": 0, "total": 0}
        return {
            "ok": True, "op": "street_walk", "state": motion.get("state") or "idle",
            "index": motion.get("index") or 0, "total": motion.get("total") or 0,
            "label": motion.get("label"), "stepM": motion.get("stepM"), "dwellS": motion.get("dwellS"),
            "alongM": motion.get("alongM"), "lengthM": motion.get("lengthM"),
            "provider": motion.get("provider"), "misses": motion.get("misses") or 0,
            "interrupted": motion.get("state") == "interrupted",
            **({"message": "The street walk was interrupted.",
                "fix": "godseye_nav op=street_walk walk=resume continues it. walk=start starts over."}
               if motion.get("state") == "interrupted" else {}),
        }
    if action == "pause":
        if job is None:
            return {"ok": False, "error": "no_walk", "message": "No street walk is running.",
                    "fix": "Start one with godseye_nav op=street_walk walk=start and a route label or points."}
        job.pause()
        _note(relay, session_id, job.snapshot("paused"))
        return job.public()
    if action == "stop":
        if job is not None:
            job.stop(interrupted=False)
            if sync:
                pass
            _drop(session, job)
        _quiet(relay, session_id, restore_street=None)
        _note(relay, session_id, None)
        if relay is not None:
            relay.release_activity(session_id, "street_walk")
        return {"ok": True, "op": "street_walk", "state": "stopped"}
    if action == "resume":
        if job is not None and job.state in ("paused", "interrupted", "running"):
            job.resume()
            _note(relay, session_id, job.snapshot("running"))
            if job.state == "running" and not getattr(job, "_thread", None):
                _start_thread(job, session, relay, session_id, lookup, sleep, sync=sync)
            return job.public()
        motion = _stored_motion(relay, session_id) or {}
        if motion.get("state") not in ("interrupted", "paused", "running"):
            return {"ok": False, "error": "no_walk", "message": "No interrupted street walk to resume.",
                    "fix": "godseye_nav op=street_walk walk=start with the route label or waypoints."}
        args = {**motion, "op": "start", "_index": motion.get("index") or 0}
        return run(args, session_id=session_id, relay=relay, lookup=lookup, sync=sync, sleep=sleep)
    # start or step
    if relay is not None and not relay.targets(session_id):
        lost = relay.lost_result(session_id)
        if lost:
            return lost
        from .live import NO_PANE
        return dict(NO_PANE)
    samples, points, label, step, dwell, provider, err = _prepare(args, _annotations(relay, session_id), _stored_motion(relay, session_id) if action != "start" else None)
    if action == "start":
        samples, points, label, step, dwell, provider, err = _prepare(args, _annotations(relay, session_id), None)
    if err:
        return {"ok": False, "error": "bad_points", "message": err,
                "fix": "Pass label of a drawn route, or points / waypoints / stops with at least two coordinates."}
    if job is not None and action == "start":
        job.stop(interrupted=False)
        _drop(session, job)
    start_at = int(args.get("_index") or 0) if action != "step" else int((job.index if job else args.get("_index") or 0))
    if action == "step" and job is not None:
        job.resume()
        _arm(relay, session_id, job)
        job.run(_hopper(relay, session_id, lookup, provider), lambda motion: _note(relay, session_id, motion),
                sleep or (lambda _s: None), one=True)
        if job.state == "done":
            _finish(relay, session_id, job, session)
        return job.public()
    walk = Walk(samples, label=label, step_m=step, dwell_s=dwell, provider=provider, points=points, start=start_at)
    _put(session, walk)
    _arm(relay, session_id, walk)
    if action == "step":
        walk.run(_hopper(relay, session_id, lookup, provider), lambda motion: _note(relay, session_id, motion),
                 sleep or (lambda _s: None), one=True)
        return walk.public()
    _start_thread(walk, session, relay, session_id, lookup, sleep, sync=sync, one=False)
    return walk.public()


def _arm(relay, session_id, walk: Walk) -> None:
    """Street Level on, globe tiles coarse, activity held for the stale window."""
    if relay is not None:
        relay.hold_activity(session_id, "street_walk", {
            "label": walk.label, "index": walk.index, "total": len(walk.samples), "stepM": walk.step_m})
    _note(relay, session_id, walk.snapshot("running"))
    _call(relay, session_id, "hermes_route_light", {"on": True, "perf": "embed"})
    _call(relay, session_id, "set_layer_visibility", {"layerId": "street-level", "enabled": True})


def _quiet(relay, session_id, restore_street) -> None:
    _call(relay, session_id, "hermes_route_light", {"on": False})
    if restore_street is False:
        _call(relay, session_id, "set_layer_visibility", {"layerId": "street-level", "enabled": False})


def _finish(relay, session_id, walk: Walk, session: str) -> None:
    _quiet(relay, session_id, restore_street=None)
    _note(relay, session_id, None)
    if relay is not None:
        relay.release_activity(session_id, "street_walk")
    _drop(session, walk)


def _hopper(relay, session_id, lookup, provider):
    find = lookup or nearest

    def hop(sample, index, total):
        found = {}
        try:
            found = find(sample["lat"], sample["lon"], prefer=provider) or {}
        except Exception:
            found = {"ok": False, "error": "no_pano"}
        payload = {
            "lat": sample["lat"], "lon": sample["lon"], "heading": sample["heading"],
            "alongM": sample.get("alongM"), "index": index, "total": total,
            "provider": found.get("provider") or provider, "panoId": found.get("panoId"),
            "radiusM": 50,
        }
        if found.get("fallback"):
            payload["fallback"] = found["fallback"]
        result = _call(relay, session_id, "hermes_street_hop", payload)
        if not isinstance(result, dict):
            return {"ok": False, "error": "street_walk failed", "miss": True}
        if result.get("ok") is False:
            return {"ok": False, "error": result.get("error") or result.get("code"), "message": result.get("message"), "miss": True}
        body = result.get("result") if isinstance(result.get("result"), dict) else result
        pano = (body or {}).get("panoId") or found.get("panoId")
        return {
            "ok": True,
            "provider": (body or {}).get("provider") or found.get("provider"),
            "panoId": pano,
            "fallback": (body or {}).get("fallback") or found.get("fallback"),
            "miss": not pano,
        }
    return hop


def _start_thread(walk: Walk, session: str, relay, session_id, lookup, sleep, *, sync: bool, one: bool = False) -> None:
    snooze = sleep or __import__("time").sleep

    def body():
        try:
            walk.run(_hopper(relay, session_id, lookup, walk.provider),
                     lambda motion: _note(relay, session_id, motion), snooze, one=one)
        finally:
            if walk.state in ("done", "stopped"):
                _finish(relay, session_id, walk, session)
            elif walk.state == "interrupted":
                _note(relay, session_id, walk.snapshot("interrupted"))
                if relay is not None:
                    relay.release_activity(session_id, "street_walk")
                _call(relay, session_id, "hermes_route_light", {"on": False})

    if sync:
        body()
        return
    thread = threading.Thread(target=body, name=f"godseye-street-walk-{session[:24]}", daemon=True)
    walk._thread = thread
    thread.start()
