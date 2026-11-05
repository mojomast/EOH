"""Pin grounding + pin info drawer (gev/panel_pins.js) and the drawer data model."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gev import composer, hermes_tools as h, panel_inject, poi
from gev.scene import SceneStore

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node") or str(Path.home() / ".local/node24/bin/node")
needs_node = pytest.mark.skipif(not Path(NODE).exists() and not shutil.which("node"), reason="node not installed")


def run_js(body: str):
    src = (ROOT / "gev" / "panel_pins.js").read_text()
    prog = MOCK + "\n;" + src + "\n;(async () => {" + body + "})().catch((e) => { console.error(e); process.exit(1) })"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


# A tiny Cesium/DOM stand-in: enough to exercise add/remove/height/drawer logic headlessly.
MOCK = r"""
const listeners = {}
global.addEventListener = (t, f) => { (listeners[t] = listeners[t] || []).push(f) }
const mkEl = (tag) => { const el = { tagName: tag, children: [], style: {}, attrs: {}, classList: { s: new Set(),
  add(c) { this.s.add(c) }, remove(c) { this.s.delete(c) }, contains(c) { return this.s.has(c) } },
  set innerHTML(v) { this._html = v }, get innerHTML() { return this._html || '' }, textContent: '',
  setAttribute(k, v) { this.attrs[k] = v }, getAttribute(k) { return this.attrs[k] }, appendChild(c) { this.children.push(c); return c },
  addEventListener() {}, querySelector(q) { if (!this._q) this._q = {}; return this._q[q] || (this._q[q] = mkEl(q)) },
  replaceWith() {} }; return el }
global.document = { createElement: mkEl, head: mkEl('head'), body: mkEl('body'), addEventListener() {} }
let sampleH = 12, globeH = 3, camH = 40000, hidden = [], tilesShow = true
const posted = []
const entities = []
global.window = {
  parent: { postMessage: (m) => posted.push(m) },
  Cesium: {
    Cartographic: { fromDegrees: (lon, lat) => ({ lon, lat }) },
    Cartesian3: { fromDegrees: (lon, lat, h) => ({ lon, lat, h }) },
    Cartesian2: function (x, y) { this.x = x; this.y = y }, NearFarScalar: function () {},
    BoundingSphere: function () {}, HeadingPitchRange: function () {}, Math: { toRadians: (d) => d },
    VerticalOrigin: { BOTTOM: 'BOTTOM' }, HorizontalOrigin: { CENTER: 'CENTER' }, LabelStyle: {},
    Color: { fromCssColorString: (c) => c },
    CustomDataSource: function () { this.entities = { values: entities, add: (e) => { entities.push(e); return e },
      remove: (e) => { const i = entities.indexOf(e); if (i >= 0) entities.splice(i, 1) } } },
    ScreenSpaceEventHandler: function () { this.setInputAction = () => {} }, ScreenSpaceEventType: {},
  },
  __godsEyeView: {
    tileset: { get show() { return tilesShow } },
    annotations: { setHidden: (id, v) => hidden.push([id, v]), onChange: () => () => {}, list: () => [] },
    viewer: { dataSources: { add() {} }, camera: { get positionCartographic() { return { height: camH } }, moveEnd: { addEventListener() {} }, heading: 0 },
      scene: { canvas: { style: {} }, sampleHeightSupported: true, sampleHeight: () => sampleH, requestRender() {},
        globe: { show: true, getHeight: () => globeH } } },
  },
}
global.setInterval = () => 0
"""


@needs_node
def test_pin_is_bottom_anchored_ground_clamped_and_hides_upstream_reticle():
    r = run_js(r"""
      const P = window.__hermesPins
      P.add('anno-1', 45.5, -73.5, { label: 'Sumo', color: 'amber' })
      const [pin, dot] = entities
      console.log(JSON.stringify({ vo: pin.billboard.verticalOrigin, dd: pin.billboard.disableDepthTestDistance,
        dotDd: dot.point.disableDepthTestDistance, h: pin.position.h, same: pin.position.h === dot.position.h, hidden }))""")
    assert r["vo"] == "BOTTOM"                           # tip of the pin = the exact spot
    assert r["dd"] is None and r["dotDd"] is None        # Infinity (JSON null): never swallowed by buildings
    assert r["h"] == 12 and r["same"]                    # on the sampled surface, tip and dot at one point
    assert r["hidden"] == [["anno-1", True]]             # upstream's screen-space reticle is hidden


@needs_node
def test_pin_height_is_resampled_when_zoom_changes_not_settled_once():
    r = run_js(r"""
      const P = window.__hermesPins
      P.add('a', 45.5, -73.5, { label: 'x' })
      const h1 = entities[0].position.h
      sampleH = 1.5; camH = 120          // street level: finer tiles report the real ground
      P.state(); // no-op
      // the 700 ms timer calls resampleAll(false); simulate it via moveEnd semantic: force resample
      P.add('b', 45.6, -73.6, {})        // new pins sample immediately
      const hb = entities[2].position.h
      console.log(JSON.stringify({ h1, hb }))""")
    assert r["h1"] == 12 and r["hb"] == 1.5


@needs_node
def test_drawer_open_close_by_label_coords_and_unknown():
    r = run_js(r"""
      const P = window.__hermesPins
      P.add('anno-1', 45.5, -73.5, { label: 'Sumo', description_md: '**hi**', links: [{ url: 'https://a.example' }, { url: 'javascript:alert(1)' }],
        images: [{ url: 'https://img.example/a.jpg', alt: 'A' }, { url: 'data:image/png;base64,AA' }] })
      const a = P.command({ action: 'open', label: 'sumo' })
      const b = P.command({ action: 'open', latitude: 45.5001, longitude: -73.5001 })
      const st = P.command({ action: 'state' })
      const c = P.command({ action: 'close' })
      const d = P.command({ action: 'open', label: 'nope' })
      P.add('anno-2', 1, 2, {}); P.open('anno-2'); P.remove('anno-2')
      console.log(JSON.stringify({ a, b: b.pin.id, st: st.open, c: c.open, d: d.ok, afterRemove: P.state().open }))""")
    assert r["a"]["ok"] and r["a"]["pin"] == {"id": "anno-1", "label": "Sumo", "latitude": 45.5, "longitude": -73.5}
    assert r["b"] == "anno-1" and r["st"] is True and r["c"] is False and r["d"] is False and r["afterRemove"] is None


@needs_node
def test_markdown_is_sanitized():
    r = run_js(r"""
      const md = window.__hermesPins.renderMarkdown
      console.log(JSON.stringify(md('# T\n<script>alert(1)</script> <img src=x onerror=alert(1)> **b** [ok](https://e.example/a_b?x=1&y=2) [bad](javascript:alert(1)) [d](data:text/html,x) `<b>`\n- one\n- two')))""")
    assert "<script" not in r and "<img" not in r and "onerror=alert" in r  # escaped, shown as text
    assert "&lt;script&gt;" in r
    assert 'href="javascript' not in r and 'href="data:' not in r
    assert '<a href="https://e.example/a_b?x=1&amp;y=2" target="_blank" rel="noopener noreferrer"' in r
    assert "<strong>b</strong>" in r and "<ul><li>one</li><li>two</li></ul>" in r and "<code>&lt;b&gt;</code>" in r


def test_links_go_through_host_open_link_and_host_allows_http_only():
    js = (ROOT / "gev" / "panel_pins.js").read_text()
    assert "ui/open-link" in js and "noopener" in js and "loading = 'lazy'" in js and "Image unavailable" in js
    assert "'Escape'" in js
    host = (ROOT / "desktop" / "plugin.js").read_text()
    assert "export function safeLink" in host and "only http(s) links can be opened" in host
    assert "hermes-gev:drawer" in host and "/live/drawer" in host


def test_inject_loads_pin_layer_before_agent():
    html = panel_inject.prepare("<html><head></head><body></body></html>")
    assert html.index("__hermesPins = {") < html.index("const prepAnnotate")


def test_poi_drawer_fields_from_tags():
    p = {"name": "Sumo", "lat": 1, "lon": 2, "address": "1 Rue A", "website": "sumo.example", "menu": "javascript:x",
         "opening_hours": "Mo-Su 11:00-22:00", "phone": "+1 514", "cuisine": "ramen;japanese",
         "wikipedia": "fr:Sumo Ramen", "wikidata": "Q42", "wikimedia_commons": "File:A b.jpg", "osm": "node/7"}
    f = poi.drawer_fields(p)
    urls = [l["url"] for l in f["links"]]
    assert urls == ["https://sumo.example", "https://fr.wikipedia.org/wiki/Sumo_Ramen",
                    "https://www.wikidata.org/wiki/Q42", "https://www.openstreetmap.org/node/7"]
    assert f["images"] == [{"url": "https://commons.wikimedia.org/wiki/Special:FilePath/A_b.jpg?width=640", "alt": "Sumo"}]
    assert "**Hours:** `Mo-Su 11:00-22:00`" in f["description_md"] and "ramen, japanese" in f["description_md"]
    pin = poi.pin_annotations([p])[0]
    assert pin["title"] == "Sumo" and pin["links"] and pin["type"] == "pin"
    assert poi.drawer_fields({"name": "x <b>", "lat": 0, "lon": 0, "address": "a*b"})["description_md"] == "**Address:** a\\*b"


def test_overpass_keeps_wiki_and_image_tags():
    el = {"type": "node", "id": 1, "lat": 1, "lon": 2, "tags": {"name": "N", "wikidata": "Q1", "image": "https://i.example/x.jpg"}}
    p = poi._poi(el)
    assert p["wikidata"] == "Q1" and poi.drawer_fields(p)["images"][0]["url"] == "https://i.example/x.jpg"


def test_schemas_expose_drawer_fields_and_tool():
    cat = {t["name"]: t for t in h.load_live_catalog()}
    ann = h.extend_live_schema("annotate_map", h.live_schema(cat["annotate_map"].get("parameters")))
    item = ann["properties"]["annotations"]["items"]["properties"]
    assert {"description_md", "images", "links", "title"} <= set(item)
    assert "godseye_live_pin_drawer" in h.EXTRA_TOOLS
    assert h.EXTRA_TOOLS["godseye_live_pin_drawer"][1]["properties"]["action"]["enum"] == ["open", "close", "state"]
    assert "pin_details" in h.EXTRA_TOOLS["godseye_brief_and_show"][1]["properties"]
    assert "op=drawer" in (ROOT / "skills" / "godseye-analyst" / "SKILL.md").read_text()
    assert "description_md" in (ROOT / "README.md").read_text()


def test_scene_persists_drawer_and_restores_it(tmp_path):
    st = SceneStore(tmp_path / "s.json")
    spec = {"type": "pin", "latitude": 45.5, "longitude": -73.5, "label": "Sumo", "description_md": "**x**",
            "links": [{"url": "https://a.example"}]}
    st.record("s", "annotate_map", {"annotations": [spec]}, {"ok": True, "result": {"items": [{"ok": True, "id": "anno-1", "latitude": 45.5, "longitude": -73.5}]}})
    st.record("s", "hermes_pin_drawer", {"action": "open", "id": "anno-1"},
              {"ok": True, "result": {"ok": True, "open": True, "pin": {"id": "anno-1", "label": "Sumo", "latitude": 45.5, "longitude": -73.5}}})
    st2 = SceneStore(tmp_path / "s.json")
    cmds = st2.restore_commands("s")
    names = [c[0] for c in cmds]
    assert names.index("annotate_map") < names.index("hermes_pin_drawer")
    assert cmds[names.index("annotate_map")][1]["annotations"][0]["description_md"] == "**x**"
    assert cmds[names.index("hermes_pin_drawer")][1] == {"action": "open", "latitude": 45.5, "longitude": -73.5, "label": "Sumo", "flyTo": False}
    assert st2.list("s")["drawer"]["label"] == "Sumo"
    st2.record("s", "hermes_pin_drawer", {"action": "close"}, {"ok": True, "result": {"ok": True, "open": False}})
    assert "hermes_pin_drawer" not in [c[0] for c in st2.restore_commands("s")]
    st2.note_drawer("s", {"latitude": 45.5, "longitude": -73.5, "label": "Sumo"})   # user click
    assert st2.list("s")["drawer"]
    aid = st2.list("s")["annotations"][0]["id"]
    st2.delete("s", aid)
    assert st2.list("s")["drawer"] is None
    st2.note_drawer("s", {"latitude": 1, "longitude": 2})
    st2.record("s", "clear_annotations", {}, {"ok": True})
    assert st2.list("s")["drawer"] is None


def test_brief_pins_carry_drawer_content():
    calls = []
    def live(name, args, sid=None, **kw):
        calls.append((name, args))
        return {"ok": True, "result": {"ok": True, "drawn": 1}}
    def mcp(name, args):
        return {"structuredContent": {"view": {"camera": {"lat": 1, "lon": 2}}, "sections": {"aircraft": {"data": {"rows": [
            {"callsign": "ABC123", "lat": 1.1, "lon": 2.2, "distance_km": 3.21}]}}}}, "content": [{"type": "text", "text": "brief"}]}
    composer.brief_and_show({"place": "Here", "pin_details": [{"description_md": "custom", "links": [{"url": "https://x.example"}], "evil": 1}]},
                            live=live, mcp=mcp)
    mark = next(a for n, a in calls if n == "annotate_map")["annotations"][0]
    assert mark["title"] == "ABC123" and mark["description_md"] == "custom" and mark["links"] and "evil" not in mark
    assert "Distance: 3.2 km" in composer.hit_drawer({"section": "aircraft", "label": "A", "lat": 1, "lon": 2, "distance_km": 3.21}, "Here")["description_md"]


def test_relay_records_user_click_drawer_for_the_panes_scene(tmp_path):
    from gev import live
    r = live.Relay()
    r.scenes = SceneStore(tmp_path / "s.json")
    r.next(wait=0, pane_id="p1", sessions=["chat-1"])
    assert r.drawer_event({"pane": "nope", "pin": None})["ok"] is False
    assert r.drawer_event({"pane": "p1", "pin": {"latitude": 1.0, "longitude": 2.0, "label": "X"}})["ok"]
    assert r.scenes.list("chat-1")["drawer"] == {"latitude": 1.0, "longitude": 2.0, "label": "X"}
    r.drawer_event({"pane": "p1", "pin": None})
    assert r.scenes.list("chat-1")["drawer"] is None
