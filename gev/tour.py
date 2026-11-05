"""Guided tours run inside the long-lived relay process with real wall-clock dwell.

A stop: caption -> fly (waitForArrival, so the tilt is not swallowed by the flight's final
pitch) -> framing tilt -> optional continuous orbit -> dwell. The tour always ends with
move_camera stop so the globe never keeps spinning."""
from __future__ import annotations

import itertools
import threading
import time

FRAMING = {  # rangeM default, pitch set exactly after arrival (hermes_frame)
    "street": (150, -15),    # range m, pitch deg: ~40 m up, near-horizon, "across the street"
    "rooftop": (450, -30),
    "district": (2500, -45),
}
TILT_SETTLE_S = 2.0
PITCH_TOL = 3.0
ORBIT_SPEEDS = {"slow", "normal", "fast"}
MAX_STOPS = 24
_ids = itertools.count(1)


def plan(args: dict) -> dict:
    stops = [s for s in (args.get("stops") or []) if isinstance(s, dict)]
    if not stops:
        raise ValueError("stops required")
    if len(stops) > MAX_STOPS:
        raise ValueError(f"at most {MAX_STOPS} stops")
    for s in stops:
        if not (isinstance(s.get("lat"), (int, float)) and isinstance(s.get("lon"), (int, float))) and not s.get("name"):
            raise ValueError("each stop needs lat/lon (or a name)")
    framing = args.get("framing") or "street"
    if framing not in FRAMING:
        raise ValueError("framing must be street, rooftop or district")
    orbit = args.get("orbit")
    orbit = None if orbit in (None, False, "", "none", "off") else ("slow" if orbit is True else str(orbit))
    if orbit and orbit not in ORBIT_SPEEDS:
        raise ValueError("orbit must be slow, normal, fast or null")
    dwell = max(0, min(600_000, int(args.get("dwellMs", 20_000))))
    range_m = max(100, min(20_000_000, int(args.get("rangeM") or FRAMING[framing][0])))
    connect = args.get("connect") if args.get("connect") in ("route",) else None
    return {"stops": stops, "framing": framing, "orbit": orbit, "dwellMs": dwell, "rangeM": range_m,
            "connect": connect, "preview": args.get("preview")}


class Tour:
    def __init__(self, spec: dict, run, sleep=None) -> None:
        self.id = f"tour-{next(_ids)}"
        self.spec = spec
        self.run = run                    # run(name, args) -> result dict
        self.cancelled = threading.Event()
        self.sleep = sleep or self.cancelled.wait
        self.state = "pending"
        self.index = -1
        self.errors: list[str] = []
        self.calls: list[str] = []
        self.visited: list[dict] = []
        self.started = time.time()
        self.finished = None

    def _do(self, name: str, args: dict) -> dict:
        self.calls.append(name)
        res = self.run(name, args) or {}
        if not res.get("ok"):
            self.errors.append(f"stop {self.index + 1} {name}: {res.get('message') or res.get('error') or (res.get('result') or {}).get('error') or 'failed'}"[:200])
        return res

    def status(self) -> dict:
        stops = self.spec["stops"]
        cur = stops[self.index] if 0 <= self.index < len(stops) else None
        return {"ok": True, "jobId": self.id, "state": self.state, "index": self.index, "total": len(stops),
                "stop": (cur or {}).get("name"), "remaining": max(0, len(stops) - self.index - 1) if self.index >= 0 else len(stops),
                "errors": self.errors[-10:], "visited": self.visited, "elapsedS": round((self.finished or time.time()) - self.started, 1)}

    def cancel(self) -> None:
        self.cancelled.set()

    def __call__(self) -> None:
        sp = self.spec
        self.state = "running"
        try:
            if sp["connect"] == "route":
                pts = [{"latitude": s["lat"], "longitude": s["lon"]} if isinstance(s.get("lat"), (int, float))
                       else {"target": s["name"]} for s in sp["stops"]][:12]
                marks = [{"type": "pin", "latitude": s["lat"], "longitude": s["lon"], "label": str(s.get("name") or "")[:120]}
                         for s in sp["stops"] if isinstance(s.get("lat"), (int, float))]
                if len(pts) >= 2:
                    marks.append({"type": "route", "points": pts, "label": "Tour"})
                if marks:
                    self._do("annotate_map", {"annotations": marks[:24], "persist": True})
            pitch = FRAMING[sp["framing"]][1]
            for i, stop in enumerate(sp["stops"]):
                if self.cancelled.is_set():
                    break
                self.index = i
                text = stop.get("caption") or stop.get("name")
                if text:
                    self._do("hermes_caption", {"text": str(text)[:160]})
                fly = {"rangeM": int(stop.get("rangeM") or sp["rangeM"]), "waitForArrival": True}
                if isinstance(stop.get("lat"), (int, float)):
                    fly.update(latitude=stop["lat"], longitude=stop["lon"])
                else:
                    fly["query"] = stop["name"]
                if sp["orbit"] and i:
                    self._do("move_camera", {"motion": "stop"})  # a running orbit fights the next flight
                if not self._do("fly_to_location", fly).get("ok"):
                    continue
                pose = {}
                for _attempt in range(3):  # exact framing, verified (a late flight can overwrite it)
                    self._do("hermes_frame", {"pitchDeg": pitch, "rangeM": fly["rangeM"]})
                    self.sleep(TILT_SETTLE_S)
                    pose = (self._do("get_current_view_state", {}).get("result") or {}).get("camera") or {}
                    if not isinstance(pose.get("pitchDeg"), (int, float)) or abs(pose["pitchDeg"] - pitch) <= PITCH_TOL:
                        break
                self.visited.append({"stop": stop.get("name"), "pitchDeg": pose.get("pitchDeg"),
                                     "heightM": round(pose["heightM"]) if isinstance(pose.get("heightM"), (int, float)) else None,
                                     "rangeM": pose.get("rangeM")})
                if sp["orbit"]:
                    self._do("move_camera", {"motion": "orbit", "direction": "right", "mode": "continuous", "speed": sp["orbit"]})
                self.sleep(sp["dwellMs"] / 1000)
            self.state = "cancelled" if self.cancelled.is_set() else "done"
        except Exception as exc:  # never leave a tour thread dying silently
            self.errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
            self.state = "failed"
        finally:
            try:
                self.run("move_camera", {"motion": "stop"})
                self.calls.append("move_camera")
            except Exception:
                pass
            self.finished = time.time()
