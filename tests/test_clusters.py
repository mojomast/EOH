"""Pane pin clustering, filter chips/state and the opening_hours subset parser."""
import json
import subprocess
from datetime import datetime

from test_pins import MOCK, NODE, ROOT, needs_node


def run_js(body: str):
    src = (ROOT / "gev" / "panel_oh.js").read_text() + "\n;" + (ROOT / "gev" / "panel_pins.js").read_text()
    setup = r"""
    let flew = null
    window.__godsEyeView.viewer.camera.flyTo = (o) => { flew = o }
    window.Cesium.Rectangle = { fromDegrees: (w, s, e, n) => ({ w, s, e, n }) }
    window.Cesium.SceneTransforms = { worldToWindowCoordinates: (scene, c) => ({ x: c.lon * 2e7 / camH, y: -c.lat * 2e7 / camH }) }
    """
    prog = MOCK + setup + "\n;" + src + "\n;(async () => {" + body + "})().catch((e) => { console.error(e); process.exit(1) })"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def oh(value, when):
    prog = (ROOT / "gev" / "panel_oh.js").read_text() + f"\nconsole.log(JSON.stringify(module.exports.isOpen({json.dumps(value)}, new Date({json.dumps(when)}))))"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_opening_hours_subset_and_unknowns():
    wed_noon, wed_late, sat_1am, sun_noon = "2026-11-04T12:00:00", "2026-11-04T23:30:00", "2026-11-07T01:00:00", "2026-11-08T12:00:00"
    assert oh("24/7", wed_late) is True
    assert oh("Mo-Fr 09:00-17:00", wed_noon) is True and oh("Mo-Fr 09:00-17:00", sun_noon) is False
    assert oh("Mo-Fr 11:00-14:00,17:00-22:00", wed_late) is False
    assert oh("Fr 18:00-02:00", sat_1am) is True                     # past midnight
    assert oh("Mo-Su 10:00-22:00; We off", wed_noon) is False         # later rule wins for its days
    assert oh("11:00-23:00", sun_noon) is True                        # no days = every day
    assert oh("Sa,Su 10:00-16:00", wed_noon) is False
    for unknown in ("Mo-Fr 08:00-17:00; PH off", "sunrise-sunset", "Jan-Mar Mo 10:00-12:00", "by appointment", "",
                    "Mo-Fr 10:00+", "Mo 25:00-26:00x"):
        assert oh(unknown, wed_noon) is None, unknown


@needs_node
def test_dense_pins_cluster_when_zoomed_out_and_expand_on_click():
    r = run_js(r"""
      const P = window.__hermesPins
      camH = 2.0e6
      P.add('a1', 45.50, -73.57, { label: 'A' }); P.add('a2', 45.51, -73.56, { label: 'B' }); P.add('a3', 45.52, -73.58, { label: 'C' })
      P.add('far', 48.85, 2.35, { label: 'Paris' })
      P.refreshView(true)
      const s1 = P.state()
      const ex = P.expandCluster(s1.clusters[0].id)
      camH = 900; P.refreshView(true)
      const s2 = P.state()
      const shown = entities.filter((e) => e._hermesPin === 'a1').map((e) => e.show)
      console.log(JSON.stringify({ s1, ex, flew, s2, shown, bubbles: entities.filter((e) => e._hermesCluster).length }))""")
    assert r["s1"]["clusters"] == [{"id": r["s1"]["clusters"][0]["id"], "count": 3}]
    by = {p["id"]: p for p in r["s1"]["pins"]}
    assert by["a1"]["clustered"] and not by["far"]["clustered"]
    assert r["ex"]["ok"] and r["ex"]["count"] == 3 and r["flew"]["destination"]["s"] < 45.5
    assert r["s2"]["clusters"] == [] and r["bubbles"] == 0 and r["shown"] == [True, True]  # street level: no bubbles


@needs_node
def test_filters_by_category_rating_and_open_now():
    r = run_js(r"""
      const P = window.__hermesPins
      camH = 500
      P.add('r', 45.5, -73.5, { label: 'Ramen', tags: { cuisine: 'ramen;japanese', amenity: 'restaurant', opening_hours: 'Mo-Su 11:00-22:00', rating: '4.6' } })
      P.add('c', 45.6, -73.5, { label: 'Cafe', tags: { amenity: 'cafe', opening_hours: 'Mo-Fr 07:00-10:00', rating: '3.9' } })
      P.add('u', 45.7, -73.5, { label: 'Mystery', tags: { amenity: 'bar', opening_hours: 'PH off' } })
      P.add('q', 45.8, -73.5, { label: 'Quake', tags: { category: 'earthquakes', watch: 'W' } })
      const all = P.filterCommand({ action: 'state' })
      const cat = P.filterCommand({ categories: ['Ramen'] })
      const rating = P.filterCommand({ categories: [], min_rating: 4 })
      const open = P.filterCommand({ min_rating: null, open_now: true, now: '2026-11-04T12:00:00' })
      const strict = P.filterCommand({ include_unknown: false })
      const hidden = entities.filter((e) => e._hermesPin === 'c').every((e) => e.show === false)
      const reset = P.filterCommand({ clear: true })
      console.log(JSON.stringify({ all, cat, rating, open, strict, hidden, reset }))""")
    assert set(r["all"]["categories"]) >= {"ramen", "japanese", "restaurant", "cafe", "bar"} and r["all"]["shown"] == 4
    assert r["cat"]["shown"] == 2 and r["cat"]["filters"]["categories"] == ["ramen"]
    assert r["rating"]["shown"] == 3                      # unrated bar kept (unknown), 3.9 cafe hidden
    assert {o["id"]: o["open"] for o in r["open"]["openState"]} == {"r": True, "c": False, "u": None, "q": None}
    assert r["open"]["shown"] == 3 and r["strict"]["shown"] == 2 and r["hidden"]  # + the watch alert pin
    assert r["reset"]["shown"] == 4 and r["reset"]["filters"]["open_now"] is False


def test_filter_tool_and_pane_wiring():
    from gev import hermes_tools as h, panel_inject
    html = panel_inject.prepare("<html><head></head><body></body></html>")
    assert "__hermesOpeningHours" in html and html.index("__hermesOpeningHours") < html.index("__hermesPins =")
    agent = (ROOT / "gev" / "panel_live_agent.js").read_text()
    assert "hermes_pin_filter" in agent and "'tags'" in agent
    host = (ROOT / "desktop" / "plugin.js").read_text()
    assert "hermes-gev:filters" in host and "/live/filters" in host
    assert "/live/filters" in (ROOT / "dashboard" / "plugin_api.py").read_text()
    props = h.EXTRA_TOOLS["godseye_live_filter_pins"][1]["properties"]
    assert {"categories", "min_rating", "open_now", "cluster", "fanout"} <= set(props)
