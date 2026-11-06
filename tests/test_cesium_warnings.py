"""Cesium warning guards for the pin layer (gev/panel_pins.js).

Cesium raises one-time warnings for the two invalid combinations this covers:
  * entity geometry with ``heightReference`` but no ``height``
  * entity geometry with an outline while clamped to terrain

Pins themselves are billboards/points, but they fall back to clamp-to-ground when no
terrain height is available -- and that fallback must keep a defined height and must
never carry a terrain outline.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node") or str(Path.home() / ".local/node24/bin/node")
needs_node = pytest.mark.skipif(not Path(NODE).exists() and not shutil.which("node"), reason="node not installed")


MOCK = r"""
const entities = []
let sampleH = null, globeH = null, camH = 40000
let tick = null
global.setInterval = (f) => { tick = f; return 0 }
global.clearInterval = () => {}
const mkEl = (tag) => ({ tagName: tag, children: [], style: {}, attrs: {}, classList: { add() {}, remove() {}, contains() { return false } },
  set innerHTML(v) { this._h = v }, get innerHTML() { return this._h || '' }, textContent: '', setAttribute(k, v) { this.attrs[k] = v },
  appendChild(c) { this.children.push(c); return c }, addEventListener() {}, querySelector() { return mkEl('div') }, replaceWith() {} })
global.document = { createElement: mkEl, head: mkEl('head'), body: mkEl('body'), addEventListener() {} }
global.window = {
  parent: { postMessage: () => {} },
  Cesium: {
    Cartographic: { fromDegrees: (lon, lat) => ({ lon, lat }) },
    Cartesian3: { fromDegrees: (lon, lat, h) => ({ lon, lat, h }) },
    Cartesian2: function (x, y) { this.x = x; this.y = y }, NearFarScalar: function () {},
    BoundingSphere: function () {}, HeadingPitchRange: function () {}, Math: { toRadians: (d) => d },
    VerticalOrigin: { BOTTOM: 'BOTTOM', CENTER: 'CENTER' }, HorizontalOrigin: { CENTER: 'CENTER' }, LabelStyle: {},
    Color: { fromCssColorString: (c) => c },
    HeightReference: { NONE: 0, CLAMP_TO_GROUND: 1, RELATIVE_TO_GROUND: 2 },
    SceneTransforms: { worldToWindowCoordinates: (scene, c) => ({ x: c.lon * 2e7 / camH, y: -c.lat * 2e7 / camH }) },
    CustomDataSource: function () { this.entities = { values: entities, add: (e) => { entities.push(e); return e },
      remove: (e) => { const i = entities.indexOf(e); if (i >= 0) entities.splice(i, 1) } } },
    ScreenSpaceEventHandler: function () { this.setInputAction = () => {} }, ScreenSpaceEventType: {},
  },
  __godsEyeView: {
    tileset: { show: true },
    annotations: { setHidden: () => {}, onChange: () => () => {}, list: () => [] },
    viewer: { dataSources: { add() {} }, camera: { get positionCartographic() { return { height: camH } }, moveEnd: { addEventListener() {} }, heading: 0 },
      scene: { canvas: { style: {} }, sampleHeightSupported: true, sampleHeight: () => sampleH, requestRender() {},
        globe: { show: true, getHeight: () => globeH } } },
  },
}
"""


def run_js(body: str):
    src = (ROOT / "gev" / "panel_pins.js").read_text()
    prog = MOCK + "\n;" + src + "\n;(async () => {" + body + "})().catch((e) => { console.error(e); process.exit(1) })"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_pins_fall_back_to_clamp_to_ground_with_a_defined_height():
    r = run_js(r"""
      const P = window.__hermesPins
      P.add('anno-1', 45.5, -73.5, { label: 'Sumo' })
      const [pin, dot] = entities
      console.log(JSON.stringify({ pinH: pin.position.h, dotH: dot.position.h,
        pinRef: pin.billboard.heightReference, dotRef: dot.point.heightReference }))""")
    assert r["pinRef"] == 1 and r["dotRef"] == 1          # CLAMP_TO_GROUND, not an undefined/null ref
    assert r["pinH"] == 0 and r["dotH"] == 0              # clamped graphics still carry a height


@needs_node
def test_clamp_is_dropped_when_terrain_height_arrives():
    r = run_js(r"""
      const P = window.__hermesPins
      P.add('a', 45.5, -73.5, { label: 'x' })
      const before = entities[0].billboard.heightReference
      sampleH = 7
      tick()                                                // the 700 ms resample
      const after = { h: entities[0].position.h, ref: entities[0].billboard.heightReference }
      console.log(JSON.stringify({ before, after }))""")
    assert r["before"] == 1
    assert r["after"]["h"] == 7 and r["after"]["ref"] == 0   # sampled: no clamp, real height


@needs_node
def test_cluster_bubbles_never_clamp_without_a_height():
    r = run_js(r"""
      const P = window.__hermesPins
      camH = 2.0e6
      P.add('a', 45.50, -73.57, { label: 'A' }); P.add('b', 45.51, -73.56, { label: 'B' })
      P.refreshView(true)
      const bubbles = entities.filter((e) => e._hermesCluster)
      console.log(JSON.stringify(bubbles.map((e) => ({ h: e.position.h, ref: e.billboard.heightReference }))))""")
    assert r and all(b["ref"] == 1 and b["h"] == 0 for b in r)


@needs_node
def test_safe_graphic_drops_invalid_height_reference_and_terrain_outline():
    r = run_js(r"""
      const P = window.__hermesPins
      const g = P._safeGraphic({ heightReference: 1, outline: true, outlineWidth: 3 })
      const ok = P._safeGraphic({ height: 12, heightReference: 0, outline: true, outlineWidth: 2 })
      const clampedPoly = P._safeGraphic({ clampToGround: true, outline: true, outlineColor: 'x' })
      console.log(JSON.stringify({ g, ok, clampedPoly }))""")
    assert r["g"]["height"] == 0                       # heightReference now has a height
    assert r["g"]["outline"] is False and r["g"]["outlineWidth"] == 0   # no outline on terrain
    assert r["ok"]["height"] == 12 and r["ok"]["outline"] is True       # untouched when not clamped
    assert r["clampedPoly"]["outline"] is False and r["clampedPoly"]["outlineWidth"] == 0
