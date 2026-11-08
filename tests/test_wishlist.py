"""Hermes wishlist: datasets, pin updates, batch, readback, boxes, probes, pane shot, street level, itinerary, reload, keys."""
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from gev import areas, batching, flyroute, hermes_tools, itinerary, live, marks, providers, records, router, scene, streetstep
from gev.hermes_tools import _drawer_stored, _reload_pane
from gev.scene import SceneStore

ROOT = Path(__file__).resolve().parents[1]
SECRET = "wishlist-secret-value-xyzzy"


def _route(label, ident, lat=45.5):
    return {"type": "route", "label": label, "clientId": ident, "points": [
        {"lat": lat, "lon": -73.6}, {"lat": lat + 0.02, "lon": -73.55},
    ]}


def test_records_row_schema_jsonl_csv_and_inline(tmp_path, monkeypatch):
    monkeypatch.setenv("GODSEYE_IMPORT_ROOT", str(tmp_path))
    rows = [
        {"lat": 45.5, "lon": -73.6, "label": "29 Av", "description_md": "corner",
         "images": ["https://example.com/a.jpg"], "links": ["https://example.com"],
         "tags": {"category": "house"}, "id": "h1"},
        {"latitude": 45.51, "longitude": -73.59, "name": "next", "category": "house", "id": "h2"},
    ]
    pins, warnings = records.pins_from({"records": rows + [{"label": "no coords"}]})
    assert warnings and len(pins) == 2
    assert pins[0]["clientId"] == "h1" and pins[0]["description_md"] == "corner"
    assert pins[0]["images"] == ["https://example.com/a.jpg"]
    assert pins[1]["tags"]["category"] == "house"
    path = tmp_path / "houses.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    loaded, _ = records.pins_from({"source": str(path)})
    assert [pin["clientId"] for pin in loaded] == ["h1", "h2"]
    csv_path = tmp_path / "houses.csv"
    csv_path.write_text("lat,lon,label,id\n45.5,-73.6,Cafe,c1\n")
    csv_pins, _ = records.pins_from({"source": str(csv_path)})
    assert csv_pins[0]["label"] == "Cafe"
    geo = tmp_path / "houses.json"
    geo.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "id": "g1",
        "geometry": {"type": "Point", "coordinates": [-73.6, 45.5]},
        "properties": {"label": "geo"},
    }]}))
    geo_pins, _ = records.pins_from({"source": str(geo)})
    assert geo_pins[0]["clientId"] == "g1" and geo_pins[0]["latitude"] == 45.5
    stops, _ = records.stops_from({"records": rows[:1]})
    assert stops[0]["name"] == "29 Av" and stops[0]["lat"] == 45.5
    with pytest.raises(ValueError):
        records.pins_from({"source": "https://example.com/houses.jsonl"})
    env = tmp_path / ".env"
    env.write_text("lat,lon\n1,2\n")
    with pytest.raises(ValueError):
        records.pins_from({"source": str(env)})


def test_annotate_source_waives_the_annotations_requirement(tmp_path):
    path = tmp_path / "one.json"
    path.write_text(json.dumps([{"lat": 1, "lon": 2, "label": "A", "id": "a"}]))
    spec = router.BY_LEGACY["godseye_live_annotate_map"]
    prepared, forwarded, _, err = router._prepare_generic(spec, {"source": str(path), "replace": True})
    assert err is None and prepared.op == "annotate"
    assert "annotations" not in forwarded and forwarded["source"] == str(path) and forwarded["replace"] is True
    tour = router.BY_LEGACY["godseye_live_run_tour"]
    prepared, forwarded, _, err = router._prepare_generic(tour, {"records": [{"lat": 1, "lon": 2, "label": "A"}]})
    assert err is None and "stops" not in forwarded and forwarded["records"]


def test_replace_matches_client_id_or_nearby_point_not_the_label():
    existing = [{"id": "a1", "spec": {
        "type": "pin", "latitude": 45.5, "longitude": -73.6, "label": "29 Av", "clientId": "h1",
        "description_md": "old",
    }}]
    same_label = {"type": "pin", "latitude": 45.9, "longitude": -73.1, "label": "29 Av", "clientId": "h9"}
    assert marks.classify(existing, same_label, replace=False)[0] == "add"
    moved = {"type": "pin", "latitude": 45.5001, "longitude": -73.6001, "label": "renamed", "clientId": "h1",
             "description_md": "new"}
    action, stored = marks.classify(existing, moved, replace=True)
    assert action == "update" and stored["id"] == "a1"
    nearby = {"type": "pin", "latitude": 45.5001, "longitude": -73.6001, "label": "$679,900", "description_md": "price"}
    action, stored = marks.classify(existing, nearby, replace=True)
    assert action == "update" and stored["id"] == "a1"
    duplicate = {"type": "pin", "latitude": 45.5, "longitude": -73.6, "label": "29 Av"}
    assert marks.classify(existing, duplicate, replace=False)[0] == "skip"
    planned = marks.plan(existing, [duplicate, {"type": "pin", "latitude": 46, "longitude": -73, "label": "other"}], False)
    assert planned["skipped"] and len(planned["add"]) == 1
    assert marks.image_status(404) == "404"
    assert marks.image_status(0) == "csp-blocked"
    assert marks.image_status(200) == "loaded"


def test_update_patches_and_delete_takes_many_ids(tmp_path):
    store = SceneStore(tmp_path / "s.json")
    store.append_pins("chat", [
        {"type": "pin", "latitude": 1, "longitude": 2, "label": "A", "clientId": "c1", "description_md": "old"},
        {"type": "pin", "latitude": 3, "longitude": 4, "label": "B", "clientId": "c2"},
        {"type": "pin", "latitude": 5, "longitude": 6, "label": "C", "clientId": "c3"},
    ])
    listed = store.list("chat")["annotations"]
    assert "description_md" not in listed[0]
    full = store.list("chat", full=True)["annotations"]
    assert full[0]["description_md"] == "old" and full[0]["clientId"] == "c1"
    assert store.update_annotation("chat", "c1", {"description_md": "patched", "label": "A2"})
    again = store.list("chat", full=True)["annotations"]
    assert again[0]["description_md"] == "patched" and again[0]["label"] == "A2"
    gone = store.delete_many("chat", ["c2", "c3"])
    assert gone == ["c2", "c3"]
    left = store.list("chat")["annotations"]
    assert [row["label"] for row in left] == ["A2"]
    drawer = _drawer_stored({"annotations": again}, {"id": "c1"})
    assert drawer["description_md"] == "patched"


def test_batch_returns_each_result_and_can_stop(monkeypatch):
    def handler(tool):
        def inner(args, **kw):
            if args.get("op") == "bad":
                return json.dumps({"ok": False, "error": "nope"})
            return json.dumps({"ok": True, "tool": tool, "op": args.get("op")})
        return inner

    monkeypatch.setattr(router, "composable_handler", handler)
    out = batching.run({"calls": [
        {"tool": "godseye_admin", "args": {"op": "status"}},
        {"tool": "godseye_view", "args": {"op": "state"}},
    ]})
    assert out["ok"] and out["count"] == 2 and out["results"][1]["tool"] == "godseye_view"
    stopped = batching.run({"stop_on_error": True, "calls": [
        {"tool": "godseye_admin", "args": {"op": "bad"}},
        {"tool": "godseye_view", "args": {"op": "state"}},
    ]})
    assert stopped["ok"] is False and stopped["count"] == 1 and stopped["stopped"] is True
    nested = batching.run({"calls": [{"tool": "godseye_admin", "args": {"op": "batch", "calls": []}}]})
    assert nested["results"][0]["error"] == "nested_batch"
    unknown = batching.run({"calls": [{"tool": "godseye_secret", "args": {}}]})
    assert unknown["results"][0]["error"] == "unknown_tool"


def test_named_bbox_place_radius_and_implausible_boxes():
    named = areas.interpret_bbox({"south": 45.4, "west": -73.7, "north": 45.6, "east": -73.5})
    assert named["bbox"] == [45.4, -73.7, 45.6, -73.5] and named["order"] == "named"
    swapped = [60.0, -140.0, 70.0, -130.0]
    rewritten, warning = areas.rewrite_feed_args({"bbox": swapped}, "wsen")
    assert rewritten["bbox"] == [-140.0, 60.0, -130.0, 70.0]
    assert warning
    correct, warning = areas.rewrite_feed_args({"bbox": [-73.7, 45.4, -73.5, 45.6]}, "wsen")
    assert correct["bbox"] == [-73.7, 45.4, -73.5, 45.6] and warning
    ambiguous, warning = areas.rewrite_feed_args({"bbox": [45.4, -73.7, 45.6, -73.5]}, "wsen")
    assert ambiguous["bbox"] == [45.4, -73.7, 45.6, -73.5] and warning
    both = areas.interpret_bbox([10, 20, 30, 40], prefer="swne")
    assert both["warning"] and both["bbox"] == [10, 20, 30, 40]
    with pytest.raises(ValueError):
        areas.interpret_bbox([10, 20, 5, 15])
    with pytest.raises(ValueError):
        areas.interpret_bbox([0, 0, 0, 1])
    area = areas.area_from_place("Baie-Comeau", 6, lambda place: {"lat": 49.2, "lon": -68.1, "name": place})
    assert area["place"] == "Baie-Comeau" and area["kind"] == "circle"
    assert area["bbox"][0] < 49.2 < area["bbox"][2]
    from gev import watch
    parsed = watch.normalize_area({"place": "Baie-Comeau", "radius_km": 6},
                                   geocode=lambda place: {"lat": 49.2, "lon": -68.1, "name": place})
    assert parsed["kind"] == "circle"


def test_provider_probe_classifies_and_never_returns_the_key(monkeypatch):
    secret = "probe-" + SECRET

    def fetch(url, headers, timeout):
        blob = url + json.dumps(headers or {})
        if "geocode" in url or "mapillary" in url:
            assert secret in blob
        else:
            assert secret not in blob
        if "geocode" in url:
            return 200, b'{"status":"REQUEST_DENIED"}'
        if "mapillary" in url:
            return 429, b'{"error":"quota"}'
        if url.endswith("/api/vessels?limit=1"):
            return 200, b'{"vessels":[]}'
        if url.endswith("/api/firms/status"):
            return 503, b"down"
        if url.endswith("/api/tomtom/status"):
            return 200, b'{"remaining":1}'
        if url.endswith("/api/tomtom/flow/0/0/0.png"):
            return 200, b"png"
        return 500, b"nope"

    names = ("GOOGLE_MAPS_API_KEY", "MAPILLARY_CLIENT_TOKEN", "AISSTREAM_API_KEY", "FIRMS_MAP_KEY", "TOMTOM_API_KEY")
    for name in names:
        monkeypatch.setenv(name, secret)
    rows = providers.probe_providers(fetch=fetch, file_env={
        "GOOGLE_MAPS_API_KEY": secret,
        "MAPILLARY_CLIENT_TOKEN": secret,
        "AISSTREAM_API_KEY": secret,
        "FIRMS_MAP_KEY": secret,
        "TOMTOM_API_KEY": secret,
    }, api_base="http://127.0.0.1:9")
    by = {row["provider"]: row["status"] for row in rows}
    assert by == {"google": "invalid", "mapillary": "quota", "ais": "ok", "firms": "unreachable",
                  "tomtom": "ok", "tomtom-tile": "ok"}
    assert all(row.get("cache") == "reload the pane" for row in rows if row["provider"].startswith("tomtom"))
    assert secret not in json.dumps(rows)
    for name in names:
        monkeypatch.delenv(name)
    missing = providers.probe_providers(fetch=fetch, file_env={}, api_base="http://127.0.0.1:9")
    assert {row["status"] for row in missing} == {"missing"}
    assert providers.classify_probe(None, "") == "unreachable"


def test_street_level_steps_and_itinerary_draws_one_route():
    streetstep.reset("s")
    hops = []

    def lookup(lat, lon, prefer=None):
        return {"ok": True, "provider": "google", "panoId": f"p-{lat:.3f}", "lat": lat, "lon": lon}

    opened = streetstep.run({"street": "at", "lat": 45.5, "lon": -73.6}, session_id="s", lookup=lookup, hop=hops.append)
    assert opened["ok"] and opened["index"] == 0 and hops
    nxt = streetstep.run({"street": "next"}, session_id="s", lookup=lookup, hop=hops.append)
    assert nxt["error"] == "end"
    route = streetstep.run({
        "street": "route",
        "points": [{"lat": 45.5, "lon": -73.6}, {"lat": 45.51, "lon": -73.59}],
    }, session_id="s", lookup=lookup, hop=hops.append)
    assert route["total"] == 2 and route["index"] == 0
    stepped = streetstep.run({"street": "next"}, session_id="s", lookup=lookup, hop=hops.append)
    assert stepped["index"] == 1 and stepped["provider"] == "google"
    drawn = []

    def route_fn(points, mode):
        return {"ok": True, "line": points, "meters": 1200, "minutes": 4}

    out = itinerary.run(
        {"pins": [{"lat": 45.5, "lon": -73.6}, {"lat": 45.52, "lon": -73.57}], "mode": "drive", "label": "houses",
         "dolly": True, "streetStops": True},
        session_id="itin", route_fn=route_fn, draw=drawn.append,
        dolly=lambda line, label: {"ok": True, "dolly": label},
        street=lambda line, label: {"ok": True, "street": label},
    )
    assert out["ok"] and out["mode"] == "drive" and drawn[0][0]["type"] == "route"
    assert out["dolly"]["ok"] and out["street"]["ok"]
    assert itinerary.run({"pins": [{"lat": 1, "lon": 2}], "mode": "bike"})["error"] == "bad_args"


def test_fly_route_matches_exact_id_then_exact_label_before_fuzzy(tmp_path):
    store = SceneStore(tmp_path / "s.json")
    store.append_pins("fly", [_route("walk tour", "walk-1", 45.5), _route("drive tour", "drive-1", 46.5)])
    relay = live.Relay(store)
    anns = store.annotations("fly")
    points, label, err = itinerary.choose_route({"label": "tour"}, anns)
    assert err["error"] == "ambiguous" and points == []
    points, label, err = itinerary.choose_route({"label": "drive tour"}, anns)
    assert err is None and label == "drive tour" and points[0][0] == 46.5
    points, label, err = itinerary.choose_route({"id": "drive-1"}, anns)
    assert err is None and points[0][0] == 46.5
    points, label, err = itinerary.choose_route({"points": [{"lat": 1, "lon": 2}, {"lat": 1.1, "lon": 2.1}]}, anns)
    assert err is None and points[0] == (1, 2)
    spec = router.BY_LEGACY["godseye_live_fly_route"]
    _prepared, forwarded, _, err = router._prepare_generic(spec, {"id": "drive-1", "points": [{"lat": 1, "lon": 2}, {"lat": 1.1, "lon": 2.1}]})
    assert err is None and forwarded["id"] == "drive-1" and len(forwarded["points"]) == 2
    doctor = router.BY_LEGACY["godseye_doctor"]
    _prepared, forwarded, _, err = router._prepare_generic(doctor, {"detail": "full"})
    assert err is None and forwarded.get("probe") is True and "detail" not in forwarded
    called = []
    out = flyroute.run({"label": "tour"}, session_id="fly", relay=relay, call=lambda *a, **k: called.append(a))
    assert out["error"] == "ambiguous" and called == []
    out = flyroute.run({"id": "missing"}, session_id="fly", relay=relay, call=lambda *a, **k: called.append(a))
    assert out["error"] == "not_found" and called == []


def test_reload_reports_unsaved_marks_and_snapshots_coordinates(tmp_path, monkeypatch):
    class Composer:
        def __init__(self):
            self.reloaded = False

        def live_call(self, name, args, sid, fanout=False):
            if name == "hermes_scene_info":
                return {"ok": True, "marks": [
                    {"id": "kept", "label": "A", "latitude": 1, "longitude": 2},
                    {"id": "new", "label": "B", "latitude": 3, "longitude": 4},
                ]}
            if name == "hermes_reload":
                self.reloaded = True
                return {"ok": True}
            return {"ok": False}

        def scene_op(self, op, sid, full=False):
            return {"annotations": [{"id": "kept", "label": "A"}]}

    store = SceneStore(tmp_path / "s.json")
    relay = live.Relay(store)
    composer = Composer()
    monkeypatch.setattr(live, "current", lambda: relay)
    out = _reload_pane(composer, "chat", False, False)
    assert composer.reloaded and out["unsavedMarks"] == 1 and out["snapshotted"] == 1
    saved = store.list("chat")["annotations"]
    assert saved[0]["label"] == "B"

    class CountOnly(Composer):
        def live_call(self, name, args, sid, fanout=False):
            if name == "hermes_scene_info":
                return {"ok": True, "annotations": 10}
            self.reloaded = True
            return {"ok": True}

        def scene_op(self, op, sid, full=False):
            return {"annotations": []}

    held = CountOnly()
    blocked = _reload_pane(held, "chat", False, False)
    assert blocked["error"] == "unsaved_marks" and blocked["unsavedMarks"] == 10 and held.reloaded is False
    allowed = _reload_pane(held, "chat", False, True)
    assert allowed["ok"] and held.reloaded is True


def test_keys_install_is_removed_without_reading_files(tmp_path, monkeypatch):
    src = tmp_path / "keys.env"
    src.write_text(f"TOMTOM_API_KEY={SECRET}\n")
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    assert not hasattr(providers, "install_keys")
    assert "godseye_keys" not in router.BY_LEGACY
    result = json.loads(hermes_tools.make_handler("godseye_keys")({"action": "install", "file": str(src)}))
    assert result["ok"] is False and SECRET not in json.dumps(result)
    assert "TOMTOM_API_KEY" not in os.environ


def test_label_offsets_and_pane_shot(tmp_path):
    script = r"""
const fs = require('fs')
const root = process.argv[2]
global.window = global
global.addEventListener = (type, fn) => { if (type === 'message') global.handler = fn }
global.MutationObserver = function () { this.observe = () => {} }
global.document = {
  body: { appendChild() {} },
  head: { appendChild() {} },
  createElement() {
    return { getContext() { return { drawImage() {}, fillRect() {}, fillText() {} } }, width: 0, height: 0, toDataURL() { return 'data:image/png;base64,' + 'A'.repeat(40) } }
  },
  querySelector() { return { innerText: '$679,900\n29 Av' } },
  addEventListener(type, fn) { global.handler = fn },
  getElementById() { return null },
}
global.__godsEyeView = { voiceCommands: { runner: async () => ({ ok: true }) } }
global.sessionStorage = { length: 1, key() { return 'tomtom-status' }, removeItem() { this.cleared = true } }
global.localStorage = { length: 0, key() { return null }, removeItem() {} }
const messages = []
global.window.parent = { postMessage(msg) { messages.push(msg) } }
eval(fs.readFileSync(root + '/gev/panel_pins.js', 'utf8'))
const layout = window.__hermesPins.labelOffsets([
  { id: 'a', lat: 45.501, lon: -73.567 },
  { id: 'b', lat: 45.50105, lon: -73.56705 },
])
if (layout.a.y === layout.b.y) throw new Error('labels stacked on the same pixel')
if (layout.a.show !== true || layout.b.show !== true) throw new Error('nearby labels should stay visible')
const crowded = []
for (let i = 0; i < 6; i++) crowded.push({ id: 'p' + i, lat: 45.5, lon: -73.5 + i * 0.00001 })
const packed = window.__hermesPins.labelOffsets(crowded)
if (packed.p4.show !== false) throw new Error('the fifth overlapping label should hide')
eval(fs.readFileSync(root + '/gev/panel_live_agent.js', 'utf8'))
const canvas = { width: 20, height: 10, toDataURL() { return 'data:image/png;base64,' + 'A'.repeat(40) } }
const listeners = []
global.__godsEyeView.viewer = {
  scene: {
    canvas,
    postRender: { addEventListener(fn) { listeners.push(fn); return () => { const i = listeners.indexOf(fn); if (i >= 0) listeners.splice(i, 1) } } },
    requestRender() { for (const fn of [...listeners]) fn() },
  },
}
global.__godsEyeView.annotations = { list: () => [{ id: 'p1', type: 'pin', label: '29 Av', lat: 45.5, lon: -73.5 }] }
;(async () => {
  const shot = await window.__hermesPaneShot()
  if (!shot.ok || shot.mime !== 'image/png' || !shot.includesDrawer) throw new Error('pane shot missing drawer')
  if (shot.capturedAt !== 'postRender') throw new Error('expected postRender capture')
  await global.handler({ source: window.parent, data: { type: 'hermes-gev:run', id: '1', name: 'hermes_scene_info' } })
  const info = messages.find((msg) => msg.id === '1')
  const marks = info && info.payload && info.payload.marks
  if (!marks || marks[0].label !== '29 Av' || info.payload.annotations !== 1) throw new Error('marks missing ' + JSON.stringify(info))
  await global.handler({ source: window.parent, data: { type: 'hermes-gev:run', id: '2', name: 'hermes_invalidate_providers' } })
  const cleared = messages.find((msg) => msg.id === '2')
  if (!cleared || cleared.payload.note !== 'reload the pane') throw new Error('cache note missing')
})().catch((err) => { console.error(err); process.exit(1) })
"""
    path = tmp_path / "pane.js"
    path.write_text(script)
    proc = subprocess.run(["node", str(path), str(ROOT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_catalog_stays_eleven_tools():
    assert len({item.composable for item in router.CATALOG}) == 11
    for op in ("update", "street_level", "itinerary", "pane", "batch"):
        assert any(item.op == op for item in router.CATALOG)
    assert "TOMTOM_API_KEY" in providers.ALLOWED_KEYS
