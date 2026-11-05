"""Context ("what am I looking at"), measure, isochrone and compare: Python side + pane JS (gev/panel_context.js)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gev import context, hermes_tools as h, measure as M, panel_inject, poi
from gev.scene import SceneStore

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node") or str(Path.home() / ".local/node24/bin/node")
needs_node = pytest.mark.skipif(not Path(NODE).exists(), reason="node not installed")

MTL, OLD_PORT = (45.5017, -73.5673), (45.5088, -73.5540)


# ---- geometry ---------------------------------------------------------------------------------
def test_haversine_known_distances():
    assert M.haversine_m((0, 0), (0, 1)) == pytest.approx(111195, rel=1e-3)
    assert M.haversine_m({"lat": 51.5074, "lon": -0.1278}, {"latitude": 48.8566, "longitude": 2.3522}) == pytest.approx(343_500, rel=5e-3)
    assert M.haversine_m(MTL, MTL) == 0


def test_bad_points_rejected():
    with pytest.raises(ValueError):
        M._pt((95, 0))
    out = M.measure({"op": "distance", "points": [{"lat": "x", "lon": 1}, {"lat": 1, "lon": 2}]})
    assert out["ok"] is False and out["error"] == "bad_points"
    assert M.measure({"op": "distance", "points": [MTL]})["error"] == "bad_points"
    assert M.measure({"op": "area", "points": [MTL, OLD_PORT]})["error"] == "bad_points"


def test_measure_distance_legs_and_label():
    out = M.measure({"points": [MTL, OLD_PORT, MTL]})
    assert out["ok"] and out["op"] == "distance" and len(out["legs"]) == 2
    assert out["meters"] == pytest.approx(2 * out["legs"][0]["m"], rel=1e-6)
    assert out["label"].endswith("km") and 0 <= out["legs"][0]["bearingDeg"] < 360


def test_polygon_area_one_degree_box_and_closed_ring():
    box = [(0, 0), (0, 1), (1, 1), (1, 0)]
    a = M.polygon_area_m2(box)
    assert a == pytest.approx(12_364e6, rel=5e-3)  # ~ 1 deg x 1 deg at the equator
    assert M.polygon_area_m2(box + [box[0]]) == pytest.approx(a)
    out = M.measure({"op": "area", "points": box})
    assert out["label"].endswith("km²") and out["perimeterM"] == pytest.approx(4 * 111195, rel=5e-3)


def test_small_area_units_and_formatters():
    sq = [(45.5, -73.5), (45.5, -73.5 + 100 / 78000), (45.5 + 100 / 111195, -73.5 + 100 / 78000), (45.5 + 100 / 111195, -73.5)]
    assert M.polygon_area_m2(sq) == pytest.approx(10000, rel=0.03)
    assert M.fmt_area(500) == "500 m²" and M.fmt_area(25000) == "2.50 ha" and M.fmt_distance(950) == "950 m"


def test_antimeridian_area():
    a = M.polygon_area_m2([(0, 179.5), (0, -179.5), (1, -179.5), (1, 179.5)])
    assert a == pytest.approx(12_364e6, rel=5e-3)


def test_destination_roundtrip():
    p = M.destination(MTL, 90, 1000)
    assert M.haversine_m(MTL, p) == pytest.approx(1000, abs=1) and M.bearing_deg(MTL, p) == pytest.approx(90, abs=0.1)


def test_mode_aliases():
    assert M.mode_of("walking") == "walk" and M.mode_of("car") == "drive" and M.mode_of(None) == "walk"
    with pytest.raises(ValueError):
        M.mode_of("teleport")


# ---- routing services (stubbed HTTP) -----------------------------------------------------------
def test_route_ok_and_down(monkeypatch):
    body = {"code": "Ok", "routes": [{"distance": 1834.2, "duration": 1320, "geometry": {"coordinates": [[-73.5673, 45.5017], [-73.554, 45.5088]]}}]}
    seen = []
    monkeypatch.setattr(M, "_http", lambda url, data=None, timeout=20: (seen.append(url), (200, json.dumps(body)))[1])
    out = M.measure({"op": "route", "points": [MTL, OLD_PORT], "mode": "walk"})
    assert out["ok"] and out["minutes"] == 22.0 and out["line"][0] == [45.5017, -73.5673] and "routed-foot" in seen[0]

    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(M, "_http", boom)
    out = M.measure({"op": "route", "points": [MTL, OLD_PORT]})
    assert out["ok"] is False and out["error"] == "routing_unavailable" and "op=distance" in out["message"]


def test_isochrone_valhalla(monkeypatch):
    ring = [[-73.57, 45.50], [-73.56, 45.50], [-73.56, 45.51], [-73.57, 45.51], [-73.57, 45.50]]
    feats = {"features": [{"properties": {"contour": 10}, "geometry": {"type": "Polygon", "coordinates": [ring]}},
                          {"properties": {"contour": 5}, "geometry": {"type": "MultiPolygon", "coordinates": [[ring[:4]], [ring]]}}]}
    monkeypatch.setattr(M, "_http", lambda url, data=None, timeout=25: (200, json.dumps(feats)) if "valhalla" in url else (500, ""))
    out = M.isochrone({"lat": MTL[0], "lon": MTL[1], "minutes": [10, 5], "mode": "walk"})
    assert out["ok"] and not out["approximate"] and [c["minutes"] for c in out["contours"]] == [5, 10]
    assert out["contours"][0]["polygon"][0] == [45.5, -73.57] and out["contours"][1]["areaKm2"] > 0


def test_isochrone_osrm_fallback(monkeypatch):
    def http(url, data=None, timeout=25):
        if "valhalla" in url:
            raise OSError("no route to host")
        n = url.split("/table/v1/")[1].split("?")[0].count(";") + 1
        return 200, json.dumps({"code": "Ok", "durations": [[0] + [(i % 4 + 1) * 200 for i in range(n - 1)]]})
    monkeypatch.setattr(M, "_http", http)
    out = M.isochrone({"lat": MTL[0], "lon": MTL[1], "minutes": 10, "mode": "walk"})
    assert out["ok"] and out["approximate"] and "OSRM" in out["source"] and out["fallbackFrom"]
    assert len(out["contours"][0]["polygon"]) == 16  # 16 bearings; 600 s limit reaches the 3rd ring (600 s)


def test_isochrone_all_down(monkeypatch):
    monkeypatch.setattr(M, "_http", lambda *a, **k: (503, ""))
    out = M.isochrone({"lat": MTL[0], "lon": MTL[1], "minutes": [15], "mode": "drive"})
    assert out["ok"] is False and out["error"] == "routing_unavailable" and "straight-line" in out["message"]
    assert M.isochrone({"lat": 999, "lon": 0})["error"] == "bad_args"


# ---- what is here ------------------------------------------------------------------------------
def test_reverse_geocode_and_nearby(monkeypatch):
    calls = []

    def nom(path, params):
        calls.append((path, params))
        return {"name": "Marché Bonsecours", "display_name": "Marché Bonsecours, 350 Rue Saint-Paul Est, Montréal",
                "category": "building", "type": "public", "osm_type": "way", "osm_id": 1, "lat": "45.5089", "lon": "-73.5520",
                "address": {"city": "Montréal"}, "extratags": {"wikidata": "Q1", "secret": "x"}}
    monkeypatch.setattr(poi, "nominatim", nom)
    monkeypatch.setattr(poi, "overpass", lambda q: {"_endpoint": "x", "elements": [
        {"type": "node", "id": 2, "lat": 45.5095, "lon": -73.5530, "tags": {"name": "Far Café", "amenity": "cafe"}},
        {"type": "way", "id": 3, "center": {"lat": 45.5088, "lon": -73.5541}, "tags": {"name": "Near Hall", "building": "yes"}}]})
    out = M.what_is_here(*OLD_PORT)
    assert calls[0][0] == "reverse" and calls[0][1]["lat"] == OLD_PORT[0]
    assert out["place"]["name"] == "Marché Bonsecours" and out["place"]["tags"] == {"wikidata": "Q1"}
    assert [p["name"] for p in out["nearby"]["places"]] == ["Near Hall", "Far Café"]
    assert out["nearby"]["places"][0]["kind"] == "building=yes"


def test_nearby_falls_back_to_reverse_sampling(monkeypatch):
    def op(q):
        raise RuntimeError("All Overpass endpoints failed")
    hits = iter([{"name": "Marché Bonsecours", "osm_type": "way", "osm_id": 1, "lat": "45.5089", "lon": "-73.5518", "category": "building", "type": "yes"},
                 {"name": "Marché Bonsecours", "osm_type": "way", "osm_id": 1, "lat": "45.5089", "lon": "-73.5518"},
                 {"display_name": "no name"}, {"name": "Café X", "osm_type": "node", "osm_id": 7, "lat": "45.5085", "lon": "-73.5530", "category": "amenity", "type": "cafe"},
                 {"error": "Unable to geocode"}])
    monkeypatch.setattr(poi, "overpass", op)
    monkeypatch.setattr(poi, "nominatim", lambda path, params: next(hits))
    out = M.nearby_named(*OLD_PORT, radius_m=100)
    assert out["ok"] and "reverse sampling" in out["source"] and [p["name"] for p in out["places"]] == ["Café X", "Marché Bonsecours"] or \
        [p["name"] for p in out["places"]] == ["Marché Bonsecours", "Café X"]
    assert len(out["places"]) == 2


def test_what_is_here_degrades(monkeypatch):
    def fail(*a, **k):
        raise RuntimeError("Nominatim HTTP 429")
    monkeypatch.setattr(poi, "nominatim", fail)
    monkeypatch.setattr(poi, "overpass", fail)
    out = M.what_is_here(*OLD_PORT)
    assert out["ok"] and out["place"]["error"] == "geocoder_unavailable" and out["nearby"]["error"] == "osm_unavailable"


def test_nominatim_reverse_is_rate_limited_and_identified():
    assert poi.NOMINATIM_GAP_S >= 1.0 and "godseye" in poi.USER_AGENT.lower() or "hermes" in poi.USER_AGENT.lower()


# ---- context tools (fake pane) -----------------------------------------------------------------
CTX = {"ok": True, "action": "hermes_context", "camera": {"center": {"lat": 45.5, "lon": -73.56}, "rangeM": 800, "headingDeg": 0, "pitchDeg": -45,
                                                           "bbox": {"south": 45.49, "west": -73.58, "north": 45.51, "east": -73.54}},
       "selectedPin": None, "lastClick": None,
       "drawn": [{"id": "shape-1", "kind": "box", "by": "user", "label": "4.0 ha", "points": [{"lat": 45.5, "lon": -73.56}, {"lat": 45.5, "lon": -73.557},
                                                                                         {"lat": 45.502, "lon": -73.557}, {"lat": 45.502, "lon": -73.56}]}],
       "pins": [{"id": "anno-1", "label": "Kinton Ramen", "latitude": 45.495, "longitude": -73.62},
                {"id": "anno-2", "label": "Sansotei", "latitude": 45.528, "longitude": -73.578}],
       "focus": {"kind": "box", "lat": 45.501, "lon": -73.5585, "label": "4.0 ha"}}


class Pane:
    def __init__(self, ctx=CTX, ok=True):
        self.ctx, self.calls, self.ok = ctx, [], ok

    def __call__(self, name, args, sid=None, **_):
        self.calls.append((name, args))
        if not self.ok:
            return {"ok": False, "error": "no_pane", "message": "No God's Eye View pane is open."}
        if name == "hermes_context":
            return dict(self.ctx)
        if name == "hermes_draw":
            return {"ok": True, "shape": {"id": args.get("id") or "shape-9", "kind": args.get("kind"), "points": args.get("points")}}
        return {"ok": True, "action": name, **args}


def test_live_context_enriches_focus(monkeypatch):
    seen = []
    monkeypatch.setattr(M, "what_is_here", lambda lat, lon, **k: seen.append((lat, lon, k)) or {"ok": True, "place": {"name": "X"}})
    out = context.live_context({}, live=Pane())
    assert out["ok"] and out["camera"]["bbox"]["north"] == 45.51 and out["here"]["place"]["name"] == "X"
    assert seen[0][:2] == (45.501, -73.5585) and seen[0][2]["radius_m"] == 150 and "focus" in out["hint"]
    out = context.live_context({"describe": False}, live=Pane())
    assert "here" not in out


def test_live_context_no_pane():
    out = context.live_context({}, live=Pane(ok=False))
    assert out["ok"] is False and out["error"] == "no_pane"


def test_measure_pins_and_drawn():
    pane = Pane()
    out = context.measure({"pins": ["anno-1", "sansotei"]}, live=pane)
    assert out["ok"] and out["op"] == "distance" and out["meters"] > 3000
    assert pane.calls[-1][0] == "hermes_draw" and pane.calls[-1][1]["kind"] == "line" and pane.calls[-1][1]["label"] == out["label"]
    pane = Pane()
    out = context.measure({"use_drawn": "latest"}, live=pane)
    assert out["op"] == "area" and out["areaM2"] > 30000 and all(c[0] != "hermes_draw" for c in pane.calls)
    assert context.measure({"pins": ["nope"]}, live=Pane())["error"] == "pin_not_found"
    assert context.measure({"use_drawn": "latest"}, live=Pane({**CTX, "drawn": []}))["error"] == "no_drawing"


def test_isochrone_uses_focus_and_draws(monkeypatch):
    monkeypatch.setattr(M, "isochrone", lambda a: {"ok": True, "mode": "walk", "approximate": False, "center": a,
                                                  "contours": [{"minutes": 5, "polygon": [[1, 2], [1, 3], [2, 3]]}, {"minutes": 10, "polygon": [[1, 2], [1, 4], [3, 3]]}]})
    pane = Pane()
    out = context.isochrone({"minutes": [5, 10]}, live=pane)
    assert out["center"]["lat"] == 45.501 and out["drawn"] is True
    draws = [c[1] for c in pane.calls if c[0] == "hermes_draw"]
    assert [d["label"] for d in draws] == ["10 min walk", "5 min walk"] and "polygon" not in out["contours"][0]


def test_compare_resolves_wayback_and_refuses_stacks(monkeypatch):
    monkeypatch.setattr(M, "wayback_releases", lambda: [{"release": 3, "date": "2024-01-01", "title": "t"},
                                                       {"release": 2, "date": "2016-06-01", "title": "t"},
                                                       {"release": 1, "date": "2014-02-20", "title": "t"}])
    pane = Pane()
    out = context.compare({"left": "wayback:2015", "right": "current"}, live=pane)
    sent = pane.calls[-1][1]
    assert out["ok"] and sent["left"]["release"] == 1 and "/tile/1/{z}/{y}/{x}" in sent["left"]["url"] and sent["right"]["kind"] == "current"
    assert out["sides"]["left"]["date"] == "2014-02-20"
    assert context.compare({"left": "photoreal"}, live=Pane())["error"] == "unsupported_side"
    assert context.compare({"action": "list_dates"}, live=Pane())["newest"]["release"] == 3
    assert context.compare({"action": "toggle"}, live=pane)["ok"] and pane.calls[-1] == ("hermes_compare", {"action": "toggle"})


def test_pick_release_and_wayback_down(monkeypatch):
    rels = [{"release": 9, "date": "2020-05-01"}, {"release": 1, "date": "2014-02-20"}]
    assert M.pick_release(rels, "2019-12")["release"] == 1 and M.pick_release(rels, "2010")["release"] == 1
    assert M.pick_release(rels, "2021-01-01")["release"] == 9
    monkeypatch.setattr(M, "_http", lambda *a, **k: (500, ""))
    assert M.compare_layer("wayback:2015")["kind"] == "error"
    assert M.compare_layer("osm")["kind"] == "xyz" and M.compare_layer("now")["kind"] == "current"


def test_wayback_config_parse(monkeypatch):
    cfg = {"10": {"itemTitle": "World Imagery (Wayback 2014-02-20)"}, "64776": {"itemTitle": "World Imagery (Wayback 2025-01-30)"}, "x": {}}
    monkeypatch.setattr(M, "_http", lambda *a, **k: (200, json.dumps(cfg)))
    assert M.wayback_releases() == [{"release": 64776, "date": "2025-01-30", "title": "World Imagery (Wayback 2025-01-30)"},
                                    {"release": 10, "date": "2014-02-20", "title": "World Imagery (Wayback 2014-02-20)"}]


# ---- scene persistence of Hermes shapes --------------------------------------------------------
def test_scene_keeps_and_restores_shapes(tmp_path):
    st = SceneStore(tmp_path / "s.json")
    st.record("s1", "hermes_draw", {"action": "add", "kind": "line", "label": "2.1 km"},
              {"ok": True, "shape": {"id": "shape-3", "kind": "line", "points": [{"lat": 1, "lon": 2}, {"lat": 1, "lon": 3}]}})
    cmds = SceneStore(tmp_path / "s.json").restore_commands("s1")
    assert ("hermes_draw", {"action": "add", "id": "shape-3", "kind": "line", "points": [{"lat": 1, "lon": 2}, {"lat": 1, "lon": 3}],
                            "label": "2.1 km", "color": None}) in cmds
    assert st.pick(["s1"]) == "s1"
    st.record("s1", "hermes_draw", {"action": "clear"}, {"ok": True})
    assert not [c for c in st.restore_commands("s1") if c[0] == "hermes_draw"]


# ---- tool registration -------------------------------------------------------------------------
def test_new_tools_registered_with_schemas():
    for name in ("godseye_live_context", "godseye_what_is_here", "godseye_measure", "godseye_isochrone", "godseye_live_draw", "godseye_live_compare"):
        desc, schema, _emoji = h.EXTRA_TOOLS[name]
        assert schema["type"] == "object" and desc
    assert "this" in h.EXTRA_TOOLS["godseye_live_context"][0]
    assert h.EXTRA_TOOLS["godseye_measure"][1]["properties"]["op"]["enum"] == ["distance", "area", "route"]


def test_extra_handler_routes_measure():
    out = json.loads(h.extra_handler("godseye_measure")({"points": [list(MTL), list(OLD_PORT)], "draw": False}))
    assert out["ok"] and out["op"] == "distance"


def test_skill_teaches_context():
    md = (ROOT / "skills" / "godseye-analyst" / "SKILL.md").read_text()
    assert "op=context" in md and "godseye_measure" in md and "godseye_isochrone" in md and "godseye_live_compare" in md


def test_panel_injects_context_layer():
    html = panel_inject.prepare("<html><head></head><body></body></html>")
    assert "__hermesContext" in html and html.index("__hermesPins") < html.index("__hermesContext") < html.index("hermes_scene_info")


# ---- pane JS (headless Cesium/DOM stand-in) ----------------------------------------------------
MOCK = r"""
const posted = [], listeners = {}
global.addEventListener = (t, f) => { (listeners[t] = listeners[t] || []).push(f) }
const mkEl = (tag) => { const el = { tagName: tag, children: [], style: {}, dataset: {}, attrs: {}, set innerHTML(v) { this._h = v },
  get innerHTML() { return this._h || '' }, textContent: '', setAttribute(k, v) { this.attrs[k] = v }, appendChild(c) { this.children.push(c); return c },
  addEventListener(t, f) { (this.l = this.l || {})[t] = f }, querySelector() { return null }, querySelectorAll() { return [] }, remove() { this.removed = true },
  insertBefore() {}, setPointerCapture() {} }; return el }
global.document = { createElement: mkEl, head: mkEl('head'), body: mkEl('body'), addEventListener() {}, getElementById() { return null } }
const inputs = {}
const ents = []
class Cart3 { constructor(lon, lat) { this.lon = lon; this.lat = lat } static fromDegrees(lon, lat) { return new Cart3(lon, lat) } }
const layers = []
global.window = { parent: { postMessage: (m) => posted.push(m) }, innerWidth: 1000,
  Cesium: { Cartesian3: Cart3, Cartesian2: class {}, PolygonHierarchy: class { constructor(p) { this.p = p } },
    Color: { fromCssColorString: () => ({ withAlpha: () => ({}) }), YELLOW: {} }, ClassificationType: { BOTH: 2 }, HeightReference: { CLAMP_TO_GROUND: 1 },
    CustomDataSource: class { constructor() { this.entities = { values: ents, add: (e) => (ents.push(e), e), remove: (e) => { const i = ents.indexOf(e); if (i >= 0) ents.splice(i, 1) } } } },
    ScreenSpaceEventHandler: class { setInputAction(f, t) { inputs[t] = f } }, ScreenSpaceEventType: { LEFT_CLICK: 'click', LEFT_DOUBLE_CLICK: 'dbl' },
    Cartographic: { fromCartesian: (c) => ({ latitude: c.lat * Math.PI / 180, longitude: c.lon * Math.PI / 180, height: 12 }) },
    SplitDirection: { LEFT: -1, RIGHT: 1 }, UrlTemplateImageryProvider: class { constructor(o) { this.url = o.url } } },
  __hermesPins: { state: () => ({ open: 'anno-2', pins: [{ id: 'anno-2', label: 'Sansotei', latitude: 45.528, longitude: -73.578 }] }) } }
const tileset = { show: true }
window.__godsEyeView = { tileset, viewer: { canvas: { clientWidth: 900, clientHeight: 800 }, dataSources: { add() {} },
  imageryLayers: { addImageryProvider: (p) => { const l = { p }; layers.push(l); return l }, remove: (l) => layers.splice(layers.indexOf(l), 1) },
  scene: { canvas: {}, globe: { ellipsoid: {} }, pickPositionSupported: true, pickPosition: (pos) => new Cart3(pos.y / 1000, pos.x / 1000), requestRender() {}, splitPosition: 0.5 },
  camera: { heading: Math.PI / 2, pitch: -Math.PI / 4, positionCartographic: { latitude: 0.7942, longitude: -1.2839, height: 1500 }, positionWC: { x: 0, y: 0, z: 0 },
    pickEllipsoid: () => new Cart3(-73.56, 45.5), computeViewRectangle: () => ({ south: 0.794, west: -1.284, north: 0.795, east: -1.283 }) } } }
global.Cesium = window.Cesium
global.setInterval = (f) => { f(); return 1 }
global.clearInterval = () => {}
"""


def run_js(body: str):
    src = (ROOT / "gev" / "panel_context.js").read_text()
    prog = MOCK + "\n;" + src + "\n;(async () => { const H = window.__hermesContext;" + body + "})().catch((e) => { console.error(e); process.exit(1) })"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_js_geometry_matches_python():
    r = run_js("console.log(JSON.stringify({ d: H._geo.hav({lat:0,lon:0},{lat:0,lon:1}), a: H._geo.area([{lat:0,lon:0},{lat:0,lon:1},{lat:1,lon:1},{lat:1,lon:0}]) }))")
    assert r["d"] == pytest.approx(M.haversine_m((0, 0), (0, 1)), rel=1e-9)
    assert r["a"] == pytest.approx(M.polygon_area_m2([(0, 0), (0, 1), (1, 1), (1, 0)]), rel=1e-9)


@needs_node
def test_js_user_draws_box_and_context_reports_it():
    r = run_js("""
      H.start('box'); H.onClick({x: 45500, y: -73560}); H.onClick({x: 45502, y: -73557})
      const c = H.command('hermes_context')
      console.log(JSON.stringify(c))""")
    assert r["ok"] and r["drawn"][0]["kind"] == "box" and len(r["drawn"][0]["points"]) == 4 and r["drawn"][0]["measure"]["areaM2"] > 50000
    assert r["selectedPin"]["id"] == "anno-2" and r["focus"]["kind"] == "pin"  # an open pin beats a drawing
    assert r["camera"]["headingDeg"] == 90 and r["camera"]["pitchDeg"] == -45 and r["camera"]["center"]["lat"] == 45.5
    assert r["camera"]["bbox"]["south"] < r["camera"]["bbox"]["north"]


@needs_node
def test_js_line_click_finish_and_last_click():
    r = run_js("""
      H.onClick({x: 45501, y: -73561})
      H.start('line'); H.onClick({x: 45500, y: -73560}); H.onClick({x: 45510, y: -73560}); H.onClick({x: 45510, y: -73560})
      const f = H.finish(); window.__hermesPins = null
      console.log(JSON.stringify({ f, c: H.context() }))""")
    assert r["f"]["shape"]["kind"] == "line" and len(r["f"]["shape"]["points"]) == 2  # double-click duplicate dropped
    assert r["f"]["shape"]["measure"]["meters"] == pytest.approx(1112, abs=2) and r["f"]["shape"]["label"] == "1.11 km"
    assert r["c"]["lastClick"]["lat"] == 45.501 and r["c"]["focus"]["kind"] == "line"


@needs_node
def test_js_hermes_draw_list_remove_clear():
    r = run_js("""
      const a = H.command('hermes_draw', { kind: 'polygon', points: [{lat:1,lon:1},{lat:1,lon:2},{lat:2,lon:2}], label: '10 min walk', id: 'reach-10' })
      const bad = H.command('hermes_draw', { kind: 'line', points: [{lat:1,lon:1}] })
      const l = H.command('hermes_draw', { action: 'list' })
      const rm = H.command('hermes_draw', { action: 'remove', id: 'reach-10' })
      const cl = H.command('hermes_draw', { action: 'clear' })
      console.log(JSON.stringify({ a, bad, n: l.shapes.length, rm, cl }))""")
    assert r["a"]["ok"] and r["a"]["shape"]["label"] == "10 min walk" and r["a"]["shape"]["by"] == "hermes"
    assert r["bad"]["ok"] is False and r["n"] == 1 and r["rm"]["ok"] and r["cl"]["remaining"] == 0


@needs_node
def test_js_compare_swipe_layers():
    r = run_js("""
      const s = H.command('hermes_compare', { left: { kind: 'xyz', url: 'https://x/{z}/{y}/{x}', label: 'Wayback 2014' }, right: { kind: 'current', label: 'Current view' } })
      const pos = H.command('hermes_compare', { action: 'position', position: 0.3 })
      const split = window.__godsEyeView.viewer.scene.splitPosition, n = window.__godsEyeView.viewer.imageryLayers
      const tilesDuring = window.__godsEyeView.tileset.show
      const bad = H.command('hermes_compare', { left: { kind: 'stack', label: 'photoreal' } })
      const ok2 = H.command('hermes_compare', { left: { kind: 'xyz', url: 'https://x/{z}', label: 'A' }, right: { kind: 'xyz', url: 'https://y/{z}', label: 'B' } })
      const stop = H.command('hermes_compare', { action: 'stop' })
      console.log(JSON.stringify({ s, pos, split, tilesDuring, bad, ok2, stop, tilesAfter: window.__godsEyeView.tileset.show }))""")
    assert r["s"]["ok"] and r["s"]["hidPhotorealTiles"] and r["pos"]["position"] == 0.3 and r["split"] == 0.3
    assert r["tilesDuring"] is False and r["bad"]["ok"] is False and "split" in r["bad"]["error"]
    assert r["ok2"]["ok"] and r["stop"]["active"] is False and r["tilesAfter"] is True


@needs_node
def test_js_ask_prompt_posts_to_host():
    r = run_js("""
      const p = H.askPrompt()
      console.log(JSON.stringify({ p }))""")
    assert "Sansotei" in r["p"] and "45.52800" in r["p"]


STUBS = ("const sdk = { SandboxedFrame: () => null, Button: () => null }, useEffect = () => {}, useRef = () => ({}), "
         "useState = (v) => [v, () => {}], jsx = () => null, jsxs = () => null\n")


@needs_node
def test_plugin_ask_hermes_copies_and_focuses(tmp_path):
    import re
    src = re.sub(r"^import .*$", "", (ROOT / "desktop" / "plugin.js").read_text(), flags=re.M)
    mod = tmp_path / "plugin.mjs"
    mod.write_text(STUBS + src)
    prog = """
      import('%s').then(async (P) => {
        const seen = []
        const ctx = { os: { writeClipboard: async (t) => (seen.push(t), true) }, runAction: (id) => (seen.push(id), { ok: true }) }
        const r = await P.askHermes(ctx, 'What is here?')
        const e = await P.askHermes({ os: {} }, '')
        const n = await P.askHermes({ os: { writeClipboard: async () => false } }, 'x')
        console.log(JSON.stringify({ r, e, n, seen }))
      })""" % mod.as_uri()
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["r"] == {"ok": True, "copied": True, "focused": True, "prompt": "What is here?"} and r["seen"] == ["What is here?", "composer.focus"]
    assert r["e"]["ok"] is False and r["n"] == {"ok": False, "copied": False, "focused": False, "prompt": "x"}
    assert "hermes-gev:ask" in src
