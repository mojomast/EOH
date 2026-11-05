"""Street walk, thinned fly_route, pane heartbeat, and reload recovery."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from gev import flyroute, live, routegeom, streetlevel, streetwalk, turn_state
from gev.scene import SceneStore
from netblock import NetworkBlocked, playing

ROOT = Path(__file__).resolve().parents[1]


def _line(n, step=0.0001, lat0=45.50, lon=-73.57):
    return [[lat0 + i * step, lon] for i in range(n)]


def _serve(relay, pane="P", sessions=("s",)):
    stop = threading.Event()
    seen = []

    def loop():
        while not stop.is_set():
            cmd = relay.next(wait=0.2, pane_id=pane, sessions=list(sessions))
            if not cmd:
                continue
            seen.append(cmd)
            relay.result(cmd["id"], {"ok": True, "result": {
                "ok": True, "panoId": "pano-1", "provider": "google",
                "heading": (cmd.get("args") or {}).get("heading"),
            }})

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return stop, seen, thread


def test_sample_and_douglas_peucker_keep_ends_and_a_spike():
    line = _line(20, 0.0002)
    sampled = routegeom.sample_polyline(line, 50)
    assert sampled[0] == tuple(line[0]) or abs(sampled[0][0] - line[0][0]) < 1e-6
    assert abs(sampled[-1][0] - line[-1][0]) < 1e-5
    assert len(sampled) < len(line)
    flat = routegeom.douglas_peucker(line, 12)
    assert len(flat) == 2
    spike = line[:10] + [[line[10][0], line[10][1] + 0.01]] + line[10:]
    kept = routegeom.douglas_peucker(spike, 12)
    assert len(kept) >= 3
    sparse = [[45.5, -73.6], [45.55, -73.55], [45.6, -73.6]]
    thinned = routegeom.simplify(sparse, 40, 12)
    assert len(thinned) == 3
    straight = [[45.5, -73.6], [45.55, -73.6], [45.6, -73.6]]
    assert len(routegeom.simplify(straight, 40, 12)) <= len(straight)


def test_chunks_overlap_and_stay_under_the_cap():
    pts = [(45.5, -73.5 + i * 0.001) for i in range(650)]
    parts = routegeom.chunks(pts, 300)
    assert all(len(part) <= 300 for part in parts)
    assert len(parts) >= 3
    assert parts[0][-1] == parts[1][0]


def test_streetview_hit_and_mapillary_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-google-key-not-real")
    monkeypatch.delenv("MAPILLARY_CLIENT_TOKEN", raising=False)
    with playing("streetview_metadata"):
        hit = streetlevel.nearest(45.501, -73.567)
    assert hit["ok"] and hit["panoId"] == "pano-1" and hit["provider"] == "google"
    assert "test-google-key-not-real" not in json.dumps(hit)
    monkeypatch.setenv("MAPILLARY_CLIENT_TOKEN", "test-mapillary-token-not-real")
    with playing("streetview_zero", "mapillary_images"):
        fallback = streetlevel.nearest(45.501, -73.567)
    assert fallback["provider"] == "mapillary" and fallback["panoId"] == "img-1"
    assert fallback.get("fallback") == "mapillary"
    blob = json.dumps(fallback)
    assert "test-google-key-not-real" not in blob and "test-mapillary-token-not-real" not in blob


def test_lookup_without_a_cassette_does_not_leak_the_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-google-key-not-real")
    monkeypatch.delenv("MAPILLARY_CLIENT_TOKEN", raising=False)
    out = streetlevel.nearest(45.5, -73.57)
    assert out["error"] == "no_pano"
    assert "test-google-key-not-real" not in json.dumps(out)
    def boom(*_a, **_k):
        raise NetworkBlocked("public network is disabled in tests (https://maps.googleapis.com/streetview/metadata)")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    again = streetlevel.nearest(45.5, -73.57)
    assert again["error"] == "no_pano"
    assert "test-google-key-not-real" not in json.dumps(again)


def test_no_keys_does_not_call_out(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("MAPILLARY_CLIENT_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(streetlevel, "_get", lambda *a, **k: called.append(a) or (0, ""))
    out = streetlevel.nearest(45.5, -73.57)
    assert out["error"] == "no_pano" and called == []
    assert "GOOGLE_MAPS_API_KEY" in out["missingKeys"]


def test_street_walk_start_pause_step_stop_and_state():
    relay = live.Relay(SceneStore(None))
    relay.next(wait=0, pane_id="P", sessions=["walk"])
    stop, seen, thread = _serve(relay, sessions=("walk",))

    def lookup(lat, lon, radius_m=50, prefer=None):
        return {"ok": True, "provider": "google", "panoId": "pano-1", "lat": lat, "lon": lon, "distanceM": 3}

    points = [[45.50, -73.57], [45.502, -73.57]]
    try:
        stepped = streetwalk.run(
            {"walk": "step", "points": points, "dwellS": 0, "stepM": 75},
            session_id="walk", relay=relay, lookup=lookup, sync=True, sleep=lambda _s: None)
        assert stepped["state"] == "paused" and stepped["index"] == 1
        assert stepped["panoId"] == "pano-1"
        assert isinstance(stepped.get("heading"), (int, float))
        paused = streetwalk.run({"walk": "pause"}, session_id="walk", relay=relay, sync=True)
        assert paused["state"] == "paused"
        state = streetwalk.run({"walk": "state"}, session_id="walk", relay=relay)
        assert state["state"] == "paused" and state["total"] >= 2
        resumed = streetwalk.run(
            {"walk": "resume", "dwellS": 0},
            session_id="walk", relay=relay, lookup=lookup, sync=True, sleep=lambda _s: None)
        assert resumed["state"] == "done" and resumed["index"] == resumed["total"]
        names = [cmd["name"] for cmd in seen]
        assert "hermes_street_hop" in names and "hermes_route_light" in names
        hops = [cmd for cmd in seen if cmd["name"] == "hermes_street_hop"]
        assert all(cmd["args"].get("heading") is not None for cmd in hops)
        assert relay.scenes.scene("walk").get("motion") in (None, {})
        idle = streetwalk.run(
            {"walk": "start", "points": points, "dwellS": 30, "stepM": 200},
            session_id="walk", relay=relay, lookup=lookup, sync=False,
            sleep=lambda _s: time.sleep(0.05))
        assert idle["state"] == "running"
        stopped = streetwalk.run({"walk": "stop"}, session_id="walk", relay=relay, sync=True)
        assert stopped["state"] == "stopped"
        time.sleep(0.3)
        assert streetwalk.run({"walk": "state"}, session_id="walk", relay=relay)["state"] == "idle"
    finally:
        streetwalk.halt("walk")
        stop.set()
        thread.join(1)


def test_fly_route_thins_a_long_line_and_restores_street_level():
    relay = live.Relay(SceneStore(None))
    relay.scenes.scene("fly")["layers"] = ["street-level"]
    sent = []

    def call(name, args, record):
        sent.append((name, args, record))
        return {"ok": True, "result": {}}

    points = _line(1608, 0.0001)
    out = flyroute.run({"points": points, "label": "Rue"}, session_id="fly", relay=relay, call=call)
    assert out["ok"] and out["chunked"] is True
    assert out["pointsIn"] == 1608 and out["pointsOut"] < 1608
    names = [name for name, _args, _record in sent]
    assert "fly_route" not in names
    zig = [[45.5 + i * 0.0004, -73.57 + (0.002 if i % 2 else 0.0)] for i in range(650)]
    sent.clear()
    zig_out = flyroute.run({"points": zig, "lighten": False}, session_id="fly", relay=relay, call=call)
    zig_chunks = [args for name, args, _record in sent if name == "hermes_fly_chunk"]
    assert zig_out["chunked"] and zig_chunks
    assert all(len(args["points"]) <= 300 for args in zig_chunks)
    assert len(zig_chunks) >= 2
    sent.clear()
    out = flyroute.run({"points": points, "label": "Rue"}, session_id="fly", relay=relay, call=call)
    names = [name for name, _args, _record in sent]
    assert "fly_route" not in names
    chunks = [args for name, args, _record in sent if name == "hermes_fly_chunk"]
    assert chunks and all(len(args["points"]) <= 300 for args in chunks)
    lights = [(args.get("on"), record) for name, args, record in sent if name == "hermes_route_light"]
    assert lights[0][0] is True and lights[-1][0] is False
    street = [args for name, args, _record in sent if name == "set_layer_visibility"]
    assert street[0]["enabled"] is False and street[-1]["enabled"] is True
    assert all(record is False for name, _args, record in sent if name != "hermes_fly_chunk")

    sent.clear()
    bare = flyroute.run({"points": points, "lighten": False}, session_id="fly", relay=relay, call=call)
    assert bare["ok"] and bare["lighten"] is False
    assert "hermes_route_light" not in [name for name, _a, _r in sent]
    assert "set_layer_visibility" not in [name for name, _a, _r in sent]


def test_heartbeat_extends_the_busy_window_and_pane_lost_names_the_job(monkeypatch):
    relay = live.Relay()
    relay.next(wait=0, pane_id="P", sessions=["s"])
    relay.panes["P"].last_poll = time.monotonic() - 40
    relay.panes["P"].polling = 0
    assert relay.panes["P"].fresh(time.monotonic()) is False
    relay.heartbeat("P", {"busy": True, "name": "fly_route", "detail": {"index": 2, "total": 5, "label": "Rue"}})
    relay.panes["P"].last_poll = time.monotonic() - 40
    assert relay.panes["P"].fresh(time.monotonic()) is True
    relay.panes["P"].last_poll = time.monotonic() - 200
    assert relay.panes["P"].fresh(time.monotonic()) is False
    fresh = live.Relay()
    assert fresh.submit("zoom_to_globe", {})["error"] == "no_pane"
    fresh.next(wait=0, pane_id="P", sessions=["chat"])
    fresh.hold_activity("chat", "street_walk", {"index": 4, "total": 9, "label": "Sherbrooke"})
    monkeypatch.setattr(live, "PANE_STALE_S", 0.01)
    monkeypatch.setattr(live, "INFLIGHT_STALE_S", 0.01)
    monkeypatch.setattr(live, "BUSY_STALE_S", 0.01)
    fresh.panes["P"].last_poll = time.monotonic() - 10
    fresh.panes["P"].polling = 0
    assert "P" in fresh.prune()["removed"]
    lost = fresh.submit("set_layer_visibility", {"layerId": "flights"}, session_id="chat")
    assert lost["error"] == "pane_lost" and lost["code"] == "pane_lost"
    assert "reopen" in lost["fix"].lower()
    assert lost["lastActivity"]["name"] == "street_walk"
    assert lost["lastActivity"]["detail"]["index"] == 4
    assert "Sherbrooke" in lost["message"] or lost["lastActivity"]["detail"].get("label") == "Sherbrooke"
    quiet = fresh.heartbeat("gone", {"busy": False})
    assert quiet["ok"] and fresh.panes["gone"].activity == {}


def test_held_activity_survives_a_quiet_heartbeat():
    relay = live.Relay()
    relay.next(wait=0, pane_id="P", sessions=["s"])
    relay.hold_activity("s", "fly_route", {"index": 1, "total": 3, "label": "Rue"})
    out = relay.heartbeat("P", {"busy": False})
    assert out.get("hold") is True
    assert relay.panes["P"].activity["name"] == "fly_route"
    assert relay.panes["P"].activity["hold"] is True


def test_reload_restores_the_scene_and_reports_the_interrupted_walk(monkeypatch):
    relay = live.Relay(SceneStore(None))
    monkeypatch.setattr(live, "current", lambda: relay)
    relay.scenes.scene("s")["annotations"] = [{
        "id": "a1", "spec": {"type": "pin", "latitude": 45.5, "longitude": -73.5, "label": "Cafe"},
    }]
    relay.scenes.scene("s")["camera"] = {"lat": 45.5, "lon": -73.5, "heightM": 800}
    relay.scenes.note_motion("s", {
        "kind": "street_walk", "state": "running", "label": "Rue", "index": 3, "total": 10,
    })
    relay.next(wait=0, pane_id="old", sessions=["s"])
    relay.panes["old"].inflight.add(99)
    first = relay.next(wait=0, pane_id="old", sessions=["s"], fresh=True)
    names = []
    cmd = first
    while cmd:
        names.append(cmd["name"])
        cmd = relay.next(wait=0, pane_id="old", sessions=["s"])
    assert "annotate_map" in names
    assert "hermes_street_hop" not in names
    assert relay.scenes.scene("s")["motion"]["state"] == "interrupted"
    note = turn_state.pre_llm_call(session_id="s")
    assert note and "MOTION_INTERRUPTED" in note["context"]
    assert "resume" in note["context"]
    again = turn_state.pre_llm_call(session_id="s")
    assert again is None or "MOTION_INTERRUPTED" not in (again.get("context") or "")
    state = streetwalk.run({"walk": "state"}, session_id="s", relay=relay)
    assert state["interrupted"] is True and state["index"] == 3


def test_timeouts_match_and_the_parent_heartbeats():
    assert live.RESULT_WAIT_S == 150
    assert live.PANE_COMMAND_S == 150
    plugin = (ROOT / "desktop" / "plugin.js").read_text()
    assert "PANE_COMMAND_MS" in plugin and "150000" in plugin
    assert "/live/heartbeat" in plugin and "setInterval" in plugin
    assert "clearInterval(heart)" in plugin
    api = (ROOT / "dashboard" / "plugin_api.py").read_text()
    assert "/live/heartbeat" in api
    agent = (ROOT / "gev" / "panel_live_agent.js").read_text()
    assert "hermes_street_hop" in agent and "hermes_fly_chunk" in agent


def test_pane_route_yields_thins_and_aims_the_panorama():
    checker = r"""
const fs = require('fs')
global.window = global
const tiles = { maximumScreenSpaceError: 4 }
const globe = { maximumScreenSpaceError: 2, preloadAncestors: true, preloadSiblings: true }
const scene = { globe, requestRenderMode: false, primitives: { length: 1, get() { return tiles } } }
const opened = []
const applied = []
const pts = []
for (let i = 0; i < 400; i++) pts.push([45.5 + i * 0.00008, -73.57])
global.__godsEyeView = {
  viewer: { scene, camera: { positionWC: { constructor: { fromDegrees() { return {} } } }, setView() {}, cancelFlight() {} } },
  applyHostConfig(cfg) { applied.push(cfg) },
  streetLevel: {
    openNearest(args) { opened.push(args); return { panoId: 'pano-1', provider: 'google' } },
    setHeading(h) { opened.push({ setHeading: h }) }
  },
  annotations: { list() { return [{ type: 'route', label: 'Rue', points: pts }] } },
  voiceCommands: { runner() { throw new Error('runner should not fly a dense route') } }
}
eval(fs.readFileSync(process.argv[1], 'utf8'))
;(async () => {
  const before = globe.maximumScreenSpaceError
  const fly = await global.__hermesRoute.command('fly_route', { label: 'Rue' })
  const on = await global.__hermesRoute.command('hermes_route_light', { on: true })
  const sseOn = globe.maximumScreenSpaceError
  const tileOn = tiles.maximumScreenSpaceError
  const hop = await global.__hermesRoute.command('hermes_street_hop', { lat: 45.5, lon: -73.57, heading: 90, panoId: 'pano-1' })
  const off = await global.__hermesRoute.command('hermes_route_light', { on: false })
  const chunk = await global.__hermesRoute.command('hermes_fly_chunk', { points: pts.slice(0, 20), index: 0, chunks: 1 })
  if (!(fly.pointsOut < 200 && fly.pointsOut < fly.pointsIn && fly.yielded >= 1 && fly.chunks >= 1)) process.exit(2)
  if (!(sseOn >= 16 && tileOn >= 16 && globe.maximumScreenSpaceError === before)) process.exit(3)
  if (!(opened[0] && opened[0].heading === 90 && opened.some((row) => row.setHeading === 90))) process.exit(4)
  if (!(applied[0] && applied[0].perf === 'embed' && on.coarsened && off.on === false)) process.exit(5)
  if (!(chunk.yielded >= 1 && chunk.points === 20)) process.exit(6)
  console.log(JSON.stringify({ ok: true, pointsOut: fly.pointsOut, yielded: fly.yielded, hop: hop.panoId }))
})().catch((err) => { console.error(err); process.exit(1) })
"""
    proc = subprocess.run(["node", "-e", checker, str(ROOT / "gev" / "panel_route.js")],
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "pano-1" in proc.stdout
