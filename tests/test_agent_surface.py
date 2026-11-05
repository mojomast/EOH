"""Per-turn map line, concise tool output, and fix-it errors."""
from __future__ import annotations

import json
from pathlib import Path

from gev import detail, errors, hermes_tools, live, turn_state

ROOT = Path(__file__).resolve().parent.parent


def test_map_line_fits_and_names_the_fields():
    layers = [f"layer-{i:02d}-abcdefghij" for i in range(40)]
    line = turn_state.format_line(
        "connected",
        {"lat": 35.68, "lon": 139.69, "rangeM": 2500, "pitch": -45, "heading": 12.4, "layers": layers},
        5, "Sumo " + ("ramen " * 40))
    assert len(line.encode()) <= 300
    assert line.startswith("EOH pane=connected ")
    assert "cam=35.6800,139.6900" in line
    assert "r=2500m" in line and "p=-45" in line and "h=12.4" in line
    assert "pins=5" in line and "drawer=" in line and "layers=" in line
    assert turn_state.format_line("closed", {"lat": 1, "lon": 2}, 9, "X") == "EOH pane=closed"
    assert turn_state.format_line("busy", {"lat": 1, "lon": 2, "rangeM": 100, "pitch": -15, "heading": 0}, 0, None
                                   ).startswith("EOH pane=busy ")


def test_snapshot_drops_secrets_and_keeps_layer_ids():
    snap = turn_state.sanitize_snapshot({
        "lat": 10, "lon": 20, "rangeM": 150, "pitch": -15, "heading": 3,
        "layers": ["flights", "ais-live-vessels", "eyJhbGciOiJIUzI1NiJ9", "not a layer", "AIzaSySecret"],
        "pins": 2, "drawer": "AIzaSyThisIsASecretKeyValue",
    })
    assert snap["layers"] == ["flights", "ais-live-vessels"]
    assert snap["drawer"] == "open"
    assert "AIza" not in json.dumps(snap)
    assert snap["pins"] == 2 and snap["rangeM"] == 150


def test_relay_map_line_from_heartbeat_scene_and_busy():
    r = live.Relay()
    assert r.map_line("chat") == "EOH pane=closed"
    assert r.note_state("P", {
        "lat": 35.68, "lon": 139.69, "rangeM": 2500, "pitch": -45, "heading": 10,
        "layers": ["flights", "ships"], "pins": 2, "drawer": "Sumo",
    })["pending"] is True
    r.next(wait=0, pane_id="P", sessions=["chat"])
    line = r.map_line("chat")
    assert "pane=connected" in line and "cam=35.6800,139.6900" in line
    assert "layers=flights,ships" in line and "pins=2" in line and "drawer=Sumo" in line
    assert len(line.encode()) <= 300
    r.panes["P"].inflight.add(1)
    assert r.map_line("chat").startswith("EOH pane=busy ")


def test_view_result_and_scene_fill_the_line_without_a_poll():
    r = live.Relay()
    r.next(wait=0, pane_id="P", sessions=["chat"])
    r._record("chat", r.panes["P"], "get_current_view_state", {}, {"ok": True, "result": {
        "camera": {"targetLat": 45.5, "targetLon": -73.5, "rangeM": 150, "pitchDeg": -15, "headingDeg": 90},
        "layers": [{"id": "flights", "enabled": True}, {"id": "cctv", "enabled": False}],
    }})
    line = r.map_line("chat")
    assert "cam=45.5000,-73.5000" in line and "r=150m" in line and "p=-15" in line and "h=90" in line
    assert "layers=flights" in line and "cctv" not in line
    # A later heartbeat that cannot see layers must not wipe the ones we learned.
    r.note_state("P", {"lat": 45.5, "lon": -73.5, "rangeM": 150})
    assert "layers=flights" in r.map_line("chat")


def test_scene_camera_pins_and_drawer_when_snapshot_is_empty():
    r = live.Relay()
    r.next(wait=0, pane_id="P", sessions=["chat"])
    r.scenes.record("chat", "get_current_view_state", {}, {"ok": True, "result": {
        "camera": {"latitude": 1.5, "longitude": 2.5, "heightM": 1000, "pitchDeg": -30, "headingDeg": 5}}})
    r.scenes.record("chat", "annotate_map", {"annotations": [
        {"type": "pin", "label": "A", "latitude": 1, "longitude": 2}]},
        {"ok": True, "result": {"items": [{"ok": True, "id": "anno-1", "latitude": 1, "longitude": 2}]}})
    r.scenes.note_drawer("chat", {"latitude": 1.0, "longitude": 2.0, "label": "A"})
    line = r.map_line("chat")
    assert "cam=1.5000,2.5000" in line and "r=1000m" in line and "p=-30" in line and "h=5" in line
    assert "pins=1" in line and "drawer=A" in line


def test_turn_state_hook_is_optional_and_switchable(monkeypatch):
    class Ctx:
        def __init__(self):
            self.hooks = {}

        def register_hook(self, name, cb):
            self.hooks[name] = cb

    monkeypatch.delenv("GODSEYE_TURN_STATE", raising=False)
    ctx = Ctx()
    assert turn_state.register(ctx) is True
    assert "pre_llm_call" in ctx.hooks
    seen = {}

    def fetch(sid):
        seen["sid"] = sid
        return "EOH pane=connected pins=1 drawer=none"

    monkeypatch.setattr(turn_state, "fetch_line", fetch)
    got = ctx.hooks["pre_llm_call"]("chat-1", "hi", [], False, "model", "cli")
    assert got == {"context": "EOH pane=connected pins=1 drawer=none"}
    assert seen["sid"] == "chat-1"
    assert ctx.hooks["pre_llm_call"](session_id="chat-2")["context"].startswith("EOH ")
    assert seen["sid"] == "chat-2"
    monkeypatch.setattr(turn_state, "fetch_line", lambda sid: None)
    assert ctx.hooks["pre_llm_call"](session_id="s") is None
    monkeypatch.setenv("GODSEYE_TURN_STATE", "0")
    assert turn_state.register(Ctx()) is False
    assert turn_state.pre_llm_call(session_id="s") is None
    monkeypatch.setenv("GODSEYE_TURN_STATE", "1")

    class Bare:
        pass

    assert turn_state.register(Bare()) is False
    monkeypatch.setattr(turn_state, "fetch_line", lambda sid: (_ for _ in ()).throw(RuntimeError("down")))
    assert turn_state.pre_llm_call(session_id="s") is None


def test_prompt_mentions_the_line_and_stays_under_the_section_cap():
    assert len(hermes_tools.SYSTEM_PROMPT) <= 1500
    assert "EOH map line" in hermes_tools.SYSTEM_PROMPT
    assert "detail=full" in hermes_tools.SYSTEM_PROMPT
    assert "no pane" in hermes_tools.SYSTEM_PROMPT.lower()


def test_detail_schema_only_on_large_tools():
    class Ctx:
        def __init__(self):
            self.tools = {}

        def register_tool(self, **kw):
            self.tools[kw["name"]] = kw

        def register_system_prompt_section(self, *a, **k):
            pass

    c = Ctx()
    hermes_tools.register_tools(c)
    air = c.tools["godseye_aircraft_in_area"]["schema"]["parameters"]["properties"]["detail"]
    assert air["enum"] == ["concise", "full"] and air["default"] == "concise"
    assert "detail" in c.tools["godseye_live_analyst_query"]["schema"]["parameters"]["properties"]
    assert "detail" in c.tools["godseye_get_cctv_snapshot"]["schema"]["parameters"]["properties"]
    assert "detail" in c.tools["godseye_find_places"]["schema"]["parameters"]["properties"]
    assert "detail" not in c.tools["godseye_doctor"]["schema"]["parameters"]["properties"]
    assert "detail" not in c.tools["godseye_live_fly_to_location"]["schema"]["parameters"]["properties"]
    assert "detail" not in c.tools["godseye_hud_summary"]["schema"]["parameters"]["properties"]


def _aircraft_result(n=40):
    rows = [{
        "id": f"abc{i:03d}", "callsign": f"TST{i}", "icao24": f"{i:06x}",
        "lat": 35.1 + i / 100, "lon": 139.7, "altitudeM": 10000 + i, "speedMps": 230,
        "origin": "RJTT", "destination": "KLAX", "operator": "Test Air", "squawk": "1200",
        "route": ["N" + str(k) for k in range(12)],
    } for i in range(n)]
    text = json.dumps({"total": n, "rows": rows})
    return {"content": [{"type": "text", "text": text}], "structuredContent": {"total": n, "rows": rows}}, text


def _entity_payload(n=12):
    items = [{
        "id": f"ac{i}", "callsign": f"UA{i}", "lat": 32.7, "lon": -117.2,
        "altitudeM": 8000 + i * 10, "speedMps": 200, "originCountry": "United States",
        "operator": "United", "squawk": "1200", "lastContactAtMs": 1_700_000_000_000 + i,
        "blurb": "x" * 240,
    } for i in range(n)]
    return {"ok": True, "result": {"scope": "in_view", "items": items, "complete": True}}


def test_concise_lists_and_images_and_detail_is_not_forwarded(monkeypatch):
    seen = {}

    class Client:
        def call_tool(self, name, args):
            seen["args"] = dict(args)
            if name == "get_cctv_snapshot":
                return {"content": [
                    {"type": "text", "text": "camera cam-7 historical"},
                    {"type": "image", "mimeType": "image/jpeg", "data": "A" * 80_000},
                ]}
            result, _text = _aircraft_result()
            return result

    monkeypatch.setattr(hermes_tools.mcp_client, "client", lambda: Client())
    full = hermes_tools.make_handler("aircraft_in_area")({"area": {"place": "Tokyo"}, "detail": "full"})
    concise = hermes_tools.make_handler("aircraft_in_area")({"area": {"place": "Tokyo"}})
    assert "detail" not in seen["args"]
    assert "altitudeM" in full and "altitudeM" not in concise
    parsed = json.loads(concise)
    assert parsed["rows"]["count"] == 40 and "abc000" in parsed["rows"]["ids"]
    assert parsed["detail"] == "concise" and "detail=full" in parsed["hint"]
    assert len(concise.encode()) < len(full.encode()) * 0.25

    old_img = hermes_tools._format({"content": [
        {"type": "text", "text": "camera cam-7 historical"},
        {"type": "image", "mimeType": "image/jpeg", "data": "A" * 80_000},
    ]})
    concise_img = hermes_tools.make_handler("get_cctv_snapshot")({"camera_id": "cam-7"})
    full_img = hermes_tools.make_handler("get_cctv_snapshot")({"camera_id": "cam-7", "detail": "full"})
    assert "A" * 80 not in old_img and "A" * 80 not in concise_img
    assert "image omitted" in concise_img and "80000" in concise_img.replace(",", "")
    assert "A" * 80 in full_img
    huge = detail.render_mcp("get_cctv_snapshot", {"content": [
        {"type": "text", "text": "big"},
        {"type": "image", "data": "B" * 400_000, "mimeType": "image/png"},
    ]}, "full")
    assert "B" * 100 not in huge and "too_large" in huge

    calls = {}

    def fake_live(name, args, session_id=None, fanout=False):
        calls["args"] = dict(args)
        return _entity_payload()

    monkeypatch.setattr("gev.composer.live_call", fake_live)
    live_full = hermes_tools.make_live_handler("analyst_query")({"layers": ["flights"], "detail": "full"})
    live_concise = hermes_tools.make_live_handler("analyst_query")({"layers": ["flights"]})
    assert "detail" not in calls["args"]
    assert "blurb" in live_full and "blurb" not in live_concise
    body = json.loads(live_concise)
    assert body["result"]["items"]["count"] == 12 and body["detail"] == "concise"
    assert len(live_concise.encode()) < len(live_full.encode()) * 0.35

    rows = []
    aircraft, aircraft_text = _aircraft_result()
    rows.append(("aircraft_in_area", aircraft_text, concise, full))
    rows.append(("analyst_query", live_full, live_concise, live_full))
    rows.append(("get_cctv_snapshot", old_img, concise_img, full_img))
    report = detail.measure(rows)
    assert report  # numbers are asserted below; the return would trip pytest
    # List payloads shrink versus what the handler used to return. The snapshot image
    # was already dropped by the old formatter, so the honest image comparison is full vs concise.
    by = {r["name"]: r for r in report}
    assert by["aircraft_in_area"]["saved_pct_vs_old"] > 70
    assert by["analyst_query"]["saved_pct_vs_full"] > 60
    assert by["get_cctv_snapshot"]["saved_pct_vs_full"] > 95
    assert by["get_cctv_snapshot"]["saved_bytes_vs_old"] < 0  # omission note vs a formatter that dropped the image


def test_fixit_errors_for_the_common_failures(monkeypatch):
    def no_pane(name, args, session_id=None, fanout=False):
        return dict(live.NO_PANE)

    monkeypatch.setattr("gev.composer.live_call", no_pane)
    out = json.loads(hermes_tools.make_live_handler("fly_to_location")({"query": "Tokyo"}))
    assert out["error"] == "no_pane" and out["code"] == "no_pane"
    assert "Eye Of Hermes (EOH) pane" in out["fix"] and "God's Eye View" not in out["fix"]

    busy = errors.annotate(dict(live.PANE_BUSY), tool="fly_to_location")
    assert busy["code"] == "pane_busy" and "retry this same call once" in busy["fix"]
    down = errors.annotate({"ok": False, "error": "relay_unavailable",
                            "message": "The live-globe relay is not running (URLError)."}, tool="x")
    assert down["code"] == "relay_unavailable" and "Restart the Hermes" in down["fix"]
    timeout = errors.annotate({"ok": False, "error": "timeout", "message": "slow"}, tool="x")
    assert timeout["code"] == "timeout" and "godseye_doctor" in timeout["fix"]

    def missing_key(name, args, session_id=None, fanout=False):
        return {"ok": True, "result": {"ok": False, "error": "Cesium ion token missing"}}

    monkeypatch.setattr("gev.composer.live_call", missing_key)
    stack = json.loads(hermes_tools.make_live_handler("set_map_stack")({"stack": "photoreal"}))
    assert stack["code"] == "MAP_STACK_KEY"
    assert "godseye_rebuild_panel" in stack["fix"] and "CESIUM_ION_TOKEN" in stack["fix"]
    assert "remediation" in stack  # existing field kept

    keys = errors.annotate({"ok": False, "code": "KEY_MISSING", "error": "KEY_MISSING",
                            "message": "CESIUM_ION_TOKEN is not set"}, tool="godseye_doctor")
    assert keys["code"] == "KEY_MISSING" and "chmod" not in keys["fix"] and "0600" in keys["fix"]
    assert "never print" in keys["fix"].lower() or "Never print" in keys["fix"]

    over = errors.annotate({"ok": False, "code": "OSM_UNAVAILABLE",
                            "error": "All Overpass endpoints failed: overpass-api.de timed out"},
                           tool="godseye_find_places")
    assert over["code"] == "OVERPASS_DOWN" and "godseye_find_places" in over["fix"]
    assert "invent" in over["fix"]
    configured = errors.annotate({"ok": False, "code": "OVERPASS_NOT_CONFIGURED", "error": "OVERPASS_NOT_CONFIGURED"},
                                 tool="osm_query")
    assert configured["code"] == "OVERPASS_NOT_CONFIGURED" and "OVERPASS_UPSTREAMS=" in configured["fix"]
    area = errors.annotate({"ok": False, "code": "AREA_TOO_LARGE", "error": "AREA_TOO_LARGE"}, tool="osm_query")
    assert "rangeM <= 15000" in area["fix"]

    refused = json.loads(hermes_tools.make_handler("find_vessel")({"query": "facial recognition"}))
    assert "Refused" in refused["error"] and refused["code"] == "refused"
    assert refused["fix"].startswith("Stop.")

    up = errors.annotate({"ok": False, "error": "God's Eye View call failed: connection refused. Is `npm run dev` running at http://localhost:4173?"},
                         tool="get_earthquakes")
    assert up["code"] == "upstream_down" and "npm run dev" in up["fix"]


def test_success_payloads_are_not_rewritten_as_errors():
    out = errors.annotate({"ok": True, "result": {"label": "Tokyo"}}, tool="fly_to_location")
    assert "fix" not in out and "code" not in out
    doctor = {"ok": False, "checks": [{"check": "node", "ok": False, "fix": "Install Node 24."}]}
    assert errors.annotate(doctor, tool="godseye_doctor") == doctor


def test_fork_hooks_are_wired_and_do_not_embed_keys():
    agent = (ROOT / "gev/panel_live_agent.js").read_text()
    plugin = (ROOT / "desktop/plugin.js").read_text()
    api = (ROOT / "dashboard/plugin_api.py").read_text()
    for needle in ("gev:visible", "gev:init", "setHostVisible", "applyHostConfig",
                   "TODO(fork F1)", "TODO(fork F2)", "mapSnapshot", "hermes-gev:state"):
        assert needle in agent
    for needle in ("forkVisibilityMessage", "forkInitMessage", "watchPaneVisibility",
                   "TODO(fork F1)", "TODO(fork F2)", "/live/state"):
        assert needle in plugin
    assert '"/live/state"' in api or "/live/state" in api
    blob = agent + plugin
    assert "AIza" not in plugin
    assert "cesiumIonToken" in agent  # the field name in the TODO, not a value
    assert "config: null" in plugin or "forkInitMessage(null)" in plugin


def test_mapstate_http(monkeypatch):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    relay = live.Relay()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), live.make_handler(relay, "tok"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body):
        req = urllib.request.Request(base + path, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer tok"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    try:
        assert post("/mapstate", {"session_id": "s"})["line"] == "EOH pane=closed"
        relay.next(wait=0, pane_id="P", sessions=["s"])
        noted = post("/state", {"pane": "P", "state": {"lat": 10, "lon": 20, "rangeM": 500, "pitch": -20,
                                                       "heading": 1, "layers": ["flights"], "pins": 0, "drawer": None}})
        assert noted["ok"] is True
        line = post("/mapstate", {"session_id": "s"})["line"]
        assert "pane=connected" in line and "cam=10.0000,20.0000" in line and "drawer=none" in line
    finally:
        srv.shutdown()
