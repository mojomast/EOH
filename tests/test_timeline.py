"""Time slider: cached Wayback dates, event pins, scene restore, and the pane control."""
from __future__ import annotations

import calendar
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gev import errors, investigations, measure as M, timeline, watch
from gev.scene import SceneStore
import netblock
from netblock import playing

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node") or "node"
NOW = calendar.timegm((2026, 11, 5, 12, 0, 0))
AREA = [45.4, -74.0, 45.7, -73.4]
needs_node = pytest.mark.skipif(not Path(NODE).exists() and not shutil.which("node"), reason="node not installed")


class Clock:
    def __init__(self):
        self.wall = 1_700_000_000.0
        self.mono = 10_000.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono


@pytest.fixture(autouse=True)
def _fresh_wayback():
    M.reset_wayback_cache()
    yield
    M.reset_wayback_cache()


def _live(store):
    def live(name, args, session_id=None):
        store.append((name, args, session_id))
        if name != "hermes_timeline":
            return {"ok": False, "error": "unexpected"}
        if args.get("action") == "state":
            return {"ok": False, "error": "no_pane"}
        return {"ok": True, "active": True, "timeline": {
            "mode": args.get("mode"), "from": args.get("from"), "to": args.get("to"),
            "at": args.get("at"), "playing": args.get("action") == "play",
            "transition": args.get("transition") or "swipe", "index": args.get("index") or 0,
        }}
    return live


def test_wayback_catalog_is_cached_and_a_failed_refresh_keeps_it(monkeypatch):
    clock = Clock()
    calls = {"n": 0}

    def http(url, timeout=15):
        calls["n"] += 1
        if calls["n"] == 1:
            return 200, json.dumps({"10": {"itemTitle": "World Imagery (Wayback 2014-02-20)"}})
        return 500, ""

    monkeypatch.setattr(M.time, "time", clock.time)
    monkeypatch.setattr(M.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(M, "_http", http)
    first = M.wayback_releases()
    second = M.wayback_releases()
    assert calls["n"] == 1 and first[0]["date"] == "2014-02-20" and second == first
    clock.wall += 7 * 3600
    clock.mono += 1
    held = M.wayback_releases()
    assert calls["n"] == 1 and held == first
    clock.mono += 3
    again = M.wayback_releases()
    assert calls["n"] == 2 and again == first
    M.reset_wayback_cache()
    calls["n"] = 5
    assert M.wayback_releases() == [] and calls["n"] == 6
    clock.mono += 1
    assert M.wayback_releases() == [] and calls["n"] == 6


def test_list_dates_replays_the_wayback_cassette_and_does_not_fetch_tiles():
    with playing("wayback_config"):
        out = timeline.list_dates({"from": "2016-01-01", "to": "2024-12-31"}, clock=lambda: NOW)
    assert out["ok"] is True
    assert out["dates"] == ["2014-02-20", "2016-07-13", "2020-04-08", "2023-12-07"]
    assert "viewing only" in out["note"]
    with playing("wayback_config"):
        empty = timeline.list_dates({"from": "1990-01-01", "to": "2000-01-01"}, clock=lambda: NOW)
    assert empty["ok"] is False and empty["error"] == "no_dates"
    annotated = errors.annotate(empty, tool="godseye_nav")
    assert annotated["code"] == "no_dates" and "op=timeline" in annotated["fix"]
    src = (ROOT / "gev" / "timeline.py").read_text()
    pane = (ROOT / "gev" / "panel_timeline.js").read_text()
    assert "urlopen" not in src and "sampleHeight" not in pane and "getFeature" not in pane
    assert "tiles.show = false" in pane


def test_wayback_slider_is_saved_and_restored_paused(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    calls = []
    scenes = SceneStore(tmp_path / "scenes.json")
    with playing("wayback_config"):
        out = timeline.run(
            {"op": "set", "mode": "wayback", "from": "2016-01-01", "to": "2024-12-31", "date": "2020-01-01",
             "transition": "swipe"},
            session_id="s", live=_live(calls), scenes=scenes, clock=lambda: NOW)
    assert out["ok"] and out["timeline"]["at"] == "2016-07-13"
    assert out["visible"]["release"] == 13851
    assert calls[0][0] == "hermes_timeline" and calls[0][1]["action"] == "set"
    stepped = timeline.run({"op": "step", "by": 1}, session_id="s", live=_live(calls), scenes=scenes, clock=lambda: NOW)
    assert stepped["timeline"]["at"] == "2020-04-08" and stepped["timeline"]["playing"] is False
    played = timeline.run({"op": "play"}, session_id="s", live=_live(calls), scenes=scenes, clock=lambda: NOW)
    assert played["timeline"]["playing"] is True
    assert scenes.scene("s")["timeline"]["playing"] is True
    assert scenes.restore_commands("s")[-1][1]["playing"] is False
    stopped = timeline.run({"op": "stop"}, session_id="s", live=_live(calls), scenes=scenes, clock=lambda: NOW)
    assert stopped["timeline"]["playing"] is False
    state = timeline.run({"op": "state"}, session_id="s", live=_live(calls), scenes=scenes, clock=lambda: NOW)
    assert state["timeline"]["at"] == "2020-04-08" and state["pane"] is False
    investigations.save(scenes, "s", "Montreal change")
    text = investigations.render_markdown(json.loads((tmp_path / "plugin-data" / "godseye" / "investigations" / "montreal-change.json").read_text()))
    assert "## Timeline" in text and "wayback" in text and "2020-04-08" in text
    other = SceneStore(tmp_path / "other.json")
    loaded = investigations.load(other, "s2", "Montreal change", restore=False)
    assert loaded["ok"]
    cmds = other.restore_commands("s2")
    assert cmds[-1][0] == "hermes_timeline"
    assert cmds[-1][1]["playing"] is False and cmds[-1][1]["at"] == "2020-04-08"
    assert any(r["date"] == "2023-12-07" for r in cmds[-1][1]["releases"])
    missing = timeline.run({"op": "play"}, session_id="none", live=_live(calls), scenes=other, clock=lambda: NOW)
    assert missing["error"] == "no_timeline"


def test_event_slider_uses_recorded_quakes_fires_and_watch_alerts(tmp_path, monkeypatch):
    monkeypatch.setenv("FIRMS_MAP_KEY", "fixture-firms-key-1")
    monkeypatch.setenv("GODSEYE_WATCH_LOOP", "0")
    calls = []
    scenes = SceneStore(tmp_path / "scenes.json")
    watches = watch.WatchStore(tmp_path / "w.json", clock=lambda: NOW)
    watches.data["alerts"].append({
        "id": "a-old", "session_id": "s", "name": "old fire", "time": NOW,
        "items": [{
            "id": "firms:old", "feed": "fires", "lat": 45.51, "lon": -73.57,
            "time": "2018-07-04 1200", "title": "Stored fire",
            "description_md": "Stored watch alert.",
            "links": [{"label": "FIRMS", "url": "https://firms.modaps.eosdis.nasa.gov/map/"}],
        }],
    })
    with playing("usgs_quakes", "firms_csv"):
        recent = timeline.run(
            {"op": "set", "mode": "events", "feeds": ["fires"], "date": "2026-11-05", "area": AREA},
            session_id="s", live=_live(calls), scenes=scenes, watches=watches, clock=lambda: NOW)
        both = timeline.run(
            {"op": "set", "mode": "events", "from": "2025-11-01", "to": "2026-11-05", "date": "2025-11-05", "area": AREA},
            session_id="s", live=_live(calls), scenes=scenes, watches=watches, clock=lambda: NOW)
    assert recent["ok"] and recent["timeline"]["from"] == "2026-10-30"
    assert recent["visible"]["count"] == 1 and recent["visible"]["events"][0]["feed"] == "fires"
    assert "2018" not in json.dumps(recent["visible"])
    assert both["visible"]["count"] == 1 and both["visible"]["events"][0]["feed"] == "earthquakes"
    stepped = timeline.run({"op": "step", "by": 1}, session_id="s", live=_live(calls), scenes=scenes, clock=lambda: NOW)
    assert stepped["timeline"]["at"] == "2026-11-04"
    seen = timeline.run({"op": "state"}, session_id="s", live=_live(calls), scenes=scenes, clock=lambda: NOW)
    assert seen["visible"]["count"] == 2
    with playing("usgs_quakes", "firms_csv"):
        old = timeline.run(
            {"op": "set", "mode": "events", "from": "2018-07-01", "to": "2018-08-01", "date": "2018-07-01",
             "feeds": ["fires"], "area": AREA},
            session_id="s", live=_live(calls), scenes=scenes, watches=watches, clock=lambda: NOW)
    assert old["ok"] and old["visible"]["count"] == 0
    forward = timeline.run({"op": "step", "by": 1}, session_id="s", live=_live(calls), scenes=scenes, clock=lambda: NOW)
    assert forward["timeline"]["at"] == "2018-07-04" and forward["visible"]["events"][0]["title"] == "Stored fire"
    assert "last 10 days" in (old.get("note") or "")
    with playing("wayback_config"):
        saved = timeline.run(
            {"op": "set", "mode": "wayback", "from": "2016-01-01", "to": "2016-12-31"},
            session_id="closed", live=lambda *a: {"ok": False, "error": "no_pane"}, scenes=scenes, clock=lambda: NOW)
    assert saved["ok"] and saved["pane"] is False and "saved" in saved["message"]


def test_nav_op_timeline_reaches_the_handler(monkeypatch):
    from gev import hermes_tools, router
    seen = {}

    def run(args, session_id=None, **_kw):
        seen["args"] = dict(args)
        seen["sid"] = session_id
        return {"ok": True, "op": args.get("op"), "timeline": {"mode": "wayback", "at": "2016-07-13"}}

    monkeypatch.setattr(timeline, "run", run)

    class Ctx:
        def __init__(self):
            self.tools = {}

        def register_tool(self, **kw):
            self.tools[kw["name"]] = kw

        def register_system_prompt_section(self, *_a, **_k):
            pass

        def register_hook(self, *_a, **_k):
            pass

        def register_command(self, *_a, **_k):
            pass

        def register_skill(self, *_a, **_k):
            pass

    ctx = Ctx()
    hermes_tools.register_tools(ctx)
    assert "godseye_timeline" not in ctx.tools
    out = json.loads(ctx.tools["godseye_nav"]["handler"](
        {"op": "timeline", "timeline": "list_dates", "from": "2016-01-01", "to": "2024-12-31"}, session_id="s"))
    assert out["ok"] and seen["args"]["op"] == "list_dates" and "timeline" not in seen["args"]
    assert seen["sid"] == "s"
    legacy = json.loads(router.dispatch_legacy(
        "godseye_timeline", {"op": "state"}, session_id="s"))
    assert legacy["ok"] and seen["args"]["op"] == "state"


def test_slider_is_in_the_pane_and_persists_from_the_host():
    from gev import panel_inject
    html = panel_inject.prepare("<html><head></head><body></body></html>")
    assert html.index("window.__hermesContext =") < html.index("window.__hermesTimeline =") < html.index("hermes_scene_info")
    host = (ROOT / "desktop" / "plugin.js").read_text()
    api = (ROOT / "dashboard" / "plugin_api.py").read_text()
    agent = (ROOT / "gev" / "panel_live_agent.js").read_text()
    assert "hermes-gev:timeline" in host and "/live/timeline" in host
    assert "/live/timeline" in api and 'self.path == "/timeline"' in (ROOT / "gev" / "live.py").read_text()
    assert "hermes_timeline" in agent and "paintRestored" in agent


@needs_node
def test_pane_slider_swipes_fades_and_reveals_event_pins():
    helper = (ROOT / "gev" / "compare_surface.js").read_text()
    js = (ROOT / "gev" / "panel_timeline.js").read_text()
    prog = r"""
const posted = []
const imagery = []
function el(tag) {
  const node = { tag, children: [], style: {}, dataset: {}, attrs: {}, id: '', className: '', textContent: '', _html: '' }
  Object.defineProperty(node, 'innerHTML', { get() { return node._html }, set(v) { node._html = v } })
  node.setAttribute = (k, v) => { node.attrs[k] = v }
  node.appendChild = (c) => { node.children.push(c); return c }
  node.addEventListener = () => {}
  node.querySelector = (sel) => ({ textContent: '', value: '0', style: {}, addEventListener() {} })
  node.querySelectorAll = () => []
  node.closest = () => null
  return node
}
global.document = { createElement: el, body: el('body'), head: el('head'), addEventListener() {}, getElementById() { return null } }
global.setInterval = () => 1
global.clearInterval = () => {}
global.setTimeout = (fn) => { try { fn() } catch (e) {} return 1 }
global.clearTimeout = () => {}
const pins = new Map()
global.window = {
  parent: { postMessage: (m) => posted.push(m) },
  Cesium: {
    SplitDirection: { LEFT: -1, RIGHT: 1, NONE: 0 },
    UrlTemplateImageryProvider: class { constructor(o) { this.url = o.url } }
  },
  __godsEyeView: {
    tileset: { show: true },
    viewer: {
      imageryLayers: {
        get length() { return imagery.length },
        addImageryProvider(p) {
          const layer = { provider: p, show: true, alpha: 1, splitDirection: 0 }
          imagery.push(layer)
          return layer
        },
        remove(layer) { const i = imagery.indexOf(layer); if (i >= 0) imagery.splice(i, 1) },
        lowerToBottom() {}
      },
      scene: { globe: { show: false }, splitPosition: 0.5, requestRender() {} }
    }
  },
  __hermesPins: {
    add(id, lat, lon, spec) { pins.set(id, { id, lat, lon, spec }) },
    remove(id) { pins.delete(id) },
    state() { return { pins: [...pins.values()].map((p) => ({ id: p.id })) } }
  },
  __hermesContext: { compare(args) { posted.push({ compare: args }); return { ok: true } } }
}
""" + ";\n" + helper + ";\n" + js + r"""
;(async () => {
const T = window.__hermesTimeline
T._stop()
const releases = [
  { release: 13851, date: '2016-07-13', label: 'Esri Wayback 2016-07-13', url: 'https://wayback.maptiles.arcgis.com/tile/13851/{z}/{y}/{x}' },
  { release: 48376, date: '2020-04-08', label: 'Esri Wayback 2020-04-08', url: 'https://wayback.maptiles.arcgis.com/tile/48376/{z}/{y}/{x}' }
]
const set = await T.command({ action: 'set', mode: 'wayback', transition: 'swipe', from: '2016-07-13', to: '2020-04-08', at: '2016-07-13', releases })
const tilesHidden = window.__godsEyeView.tileset.show === false
const mid = await T.command({ action: 'position', position: 0.5 })
const older = imagery.find((l) => l.provider.url.includes('/13851/'))
const newer = imagery.find((l) => l.provider.url.includes('/48376/'))
const olderDir = older && older.splitDirection
const newerDir = newer && newer.splitDirection
await T.command({ action: 'set', mode: 'wayback', transition: 'fade', from: '2016-07-13', to: '2020-04-08', at: '2016-07-13', releases })
await T.command({ action: 'position', position: 0.5 })
const faded = imagery.find((l) => l.provider.url.includes('/48376/'))
const events = await T.command({ action: 'set', mode: 'events', from: '2026-11-01', to: '2026-11-05', at: '2026-11-03', events: [
  { id: 'q1', feed: 'earthquakes', lat: 45.55, lon: -73.5, time: '2026-11-03T00:00:00Z', title: 'Quake', description_md: '**Magnitude:** 3.4', links: [], color: 'amber' },
  { id: 'f1', feed: 'fires', lat: 45.52, lon: -73.58, time: '2026-11-04T18:42:00Z', title: 'Fire', description_md: '**FRP:** 12.5 MW', links: [], color: 'red' }
] })
const one = [...pins.keys()]
const step = await T.command({ action: 'step', by: 1 })
const two = [...pins.keys()]
const drawer = pins.get('tl:f1') && pins.get('tl:f1').spec.description_md
const back = await T.command({ action: 'step', by: -1 })
const bar = document.body.children.find((c) => c.id === 'hermes-gev-timeline')
console.log(JSON.stringify({
  set: set.visible.date, tilesHidden,
  split: window.__godsEyeView.viewer.scene.splitPosition,
  older: olderDir, newer: newerDir,
  mid: mid.visible.blend,
  fadeAlpha: faded && faded.alpha, fadeSplit: faded && faded.splitDirection,
  one, stepAt: step.timeline.at, two, backAt: back.timeline.at,
  drawer,
  brand: bar && bar._html.includes('EOH') && bar._html.includes('Eye Of Hermes'),
  posted: posted.some((m) => m.type === 'hermes-gev:timeline'),
  compared: posted.some((m) => m.compare && m.compare.action === 'stop')
}))
})().catch((e) => { console.error(e); process.exit(1) })
"""
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["set"] == "2016-07-13" and got["tilesHidden"] is True
    assert got["older"] == -1 and got["newer"] == 1 and got["split"] == 0.5 and got["mid"] == 0.5
    assert got["fadeSplit"] == 0 and abs(got["fadeAlpha"] - 0.5) < 0.02
    assert got["one"] == ["tl:q1"] and got["stepAt"] == "2026-11-04" and set(got["two"]) == {"tl:q1", "tl:f1"}
    assert got["backAt"] == "2026-11-03" and got["drawer"].startswith("**FRP:**")
    assert got["brand"] is True and got["posted"] is True and got["compared"] is True


@needs_node
def test_timeline_pins_survive_board_sync_and_open_a_drawer():
    pins = (ROOT / "gev" / "panel_pins.js").read_text()
    prog = r"""
const made = []
function el(tag) {
  const node = { tag, children: [], style: {}, dataset: {}, attrs: {}, className: '', textContent: '', _html: '' }
  Object.defineProperty(node, 'innerHTML', {
    get() { return node._html },
    set(v) {
      node._html = String(v)
      node.children = []
      const add = (tagName, cls) => { const c = el(tagName); c.className = cls || ''; node.children.push(c) }
      if (String(v).includes('<h2')) add('h2', '')
      if (String(v).includes('class="x"')) add('button', 'x')
      if (String(v).includes('class="body"')) add('div', 'body')
    }
  })
  node.setAttribute = (k, v) => { node.attrs[k] = v }
  node.getAttribute = (k) => node.attrs[k]
  node.appendChild = (c) => { node.children.push(c); return c }
  node.addEventListener = () => {}
  node.classList = { add() {}, remove() {}, toggle() {} }
  node.querySelector = (sel) => {
    const wantClass = sel.startsWith('.') ? sel.slice(1) : null
    const walk = (n) => {
      for (const c of n.children || []) {
        if (wantClass && c.className === wantClass) return c
        if (!wantClass && c.tag === sel) return c
        const hit = walk(c)
        if (hit) return hit
      }
      return null
    }
    return walk(node)
  }
  node.querySelectorAll = () => []
  node.remove = () => {}
  made.push(node)
  return node
}
global.document = { createElement: el, body: el('body'), head: el('head'), addEventListener() {}, getElementById() { return null } }
global.setInterval = () => 1
global.clearInterval = () => {}
global.window = {
  parent: { postMessage() {} },
  Cesium: {
    Cartographic: { fromDegrees: (lon, lat) => ({ lon, lat }) },
    Cartesian3: { fromDegrees: (lon, lat, h) => ({ lon, lat, h }) },
    Cartesian2: function () {}, NearFarScalar: function () {}, BoundingSphere: function () {},
    HeadingPitchRange: function () {}, Math: { toRadians: (d) => d },
    VerticalOrigin: { BOTTOM: 'BOTTOM' }, HorizontalOrigin: { CENTER: 'CENTER' }, LabelStyle: { FILL_AND_OUTLINE: 1 },
    Color: { fromCssColorString: (c) => c },
    CustomDataSource: function () { this.entities = { values: [], add: (e) => e, remove() {} } },
    ScreenSpaceEventHandler: function () { this.setInputAction = () => {} }, ScreenSpaceEventType: {},
  },
  __godsEyeView: {
    annotations: { list: () => [], setHidden() {}, onChange: () => () => {} },
    viewer: { dataSources: { add() {} }, camera: { positionCartographic: { height: 800 }, moveEnd: { addEventListener() {} }, heading: 0 },
      scene: { canvas: { style: {} }, sampleHeightSupported: false, requestRender() {}, globe: { show: true, getHeight: () => 0 } } }
  }
}
""" + ";\n" + pins + r"""
const P = window.__hermesPins
P.add('anno-1', 45.5, -73.5, { label: 'Place' })
P.add('tl:f1', 45.52, -73.58, { timeline: true, label: 'Fire', title: 'Fire', description_md: '**FRP:** 12.5 MW', tags: { watch: 'timeline', category: 'fires' } })
P.syncWithBoard()
const afterSync = P.state().pins.map((p) => p.id)
P.clear()
const afterClear = P.state().pins.map((p) => p.id)
const opened = P.command({ action: 'open', id: 'tl:f1' })
const body = made.map((n) => n._html).join('\n')
P.clear({ timeline: true })
console.log(JSON.stringify({ afterSync, afterClear, opened: opened.ok, pin: opened.pin && opened.pin.id, md: body.includes('FRP'), gone: P.state().pins.map((p) => p.id) }))
"""
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["afterSync"] == ["tl:f1"]
    assert got["afterClear"] == ["tl:f1"]
    assert got["opened"] is True and got["pin"] == "tl:f1" and got["md"] is True
    assert got["gone"] == []
