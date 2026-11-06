"""Overlay sidebar: one tree for pins, routes, areas, watches, and saved scenes."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gev import hermes_tools, live, panel_inject, router
from gev.overlays import SCHEMA, Book, handle
from gev.scene import SceneStore
from gev.watch import WatchStore

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node") or str(Path.home() / ".local/node24/bin/node")
needs_node = pytest.mark.skipif(not Path(NODE).exists() and not shutil.which("node"), reason="node not installed")


def _seed(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    st = SceneStore(tmp_path / "scene.json")
    items = [
        {"ok": True, "id": "anno-1", "latitude": 45.5, "longitude": -73.5},
        {"ok": True, "id": "anno-2"},
        {"ok": True, "id": "anno-3"},
    ]
    st.record("s", "annotate_map", {"annotations": [
        {"type": "pin", "latitude": 45.5, "longitude": -73.5, "label": "Sumo Ramen",
         "description_md": "bowl", "tags": {"cuisine": "ramen"}},
        {"type": "route", "label": "Canal trail", "points": [
            {"latitude": 45.5, "longitude": -73.5}, {"latitude": 45.51, "longitude": -73.49}]},
        {"type": "area", "label": "Yard", "points": [
            {"latitude": 45.5, "longitude": -73.5}, {"latitude": 45.51, "longitude": -73.5},
            {"latitude": 45.51, "longitude": -73.49}]},
    ]}, {"ok": True, "result": {"items": items}})
    line = [{"lat": 45.5, "lon": -73.5}, {"lat": 45.52, "lon": -73.48}]
    ring = line + [{"lat": 45.52, "lon": -73.5}]
    st.record("s", "hermes_draw", {"action": "add", "kind": "line", "label": "canal walk", "points": line},
              {"ok": True, "result": {"shape": {"id": "shape-line", "kind": "line", "points": line, "label": "canal walk"}}})
    st.record("s", "hermes_draw", {"action": "add", "kind": "polygon", "label": "basin", "points": ring},
              {"ok": True, "result": {"shape": {"id": "shape-area", "kind": "polygon", "points": ring, "label": "basin"}}})
    watches = WatchStore(tmp_path / "watches.json")
    watches.data["watches"]["w-0123abcd"] = {
        "id": "w-0123abcd", "name": "Harbor quakes", "description": "usgs",
        "area": {"bbox": [45.4, -73.7, 45.6, -73.4]}, "feeds": ["earthquakes"],
        "conditions": {}, "interval_s": 900, "active": False, "deliver": "queue",
        "pin": True, "baseline": True, "seen": {"earthquakes": ["old"]}, "status": {},
    }
    inv = tmp_path / "plugin-data" / "godseye" / "investigations"
    inv.mkdir(parents=True)
    (inv / "harbor.json").write_text(json.dumps({
        "name": "Harbor", "title": "Harbor watch", "notes": "dock",
        "overlayVisible": True, "pins": [], "routes": [], "path": "/secret/not-exported",
    }))
    return st, watches


def _book(st, watches):
    return Book(st, "s", watches=watches)


def _kind(tree, kind):
    return next(group for group in tree["groups"] if group["kind"] == kind)


def test_tree_groups_pins_routes_areas_watches_and_scenes(tmp_path, monkeypatch):
    st, watches = _seed(tmp_path, monkeypatch)
    tree = _book(st, watches).tree()
    assert tree["schema"] == SCHEMA and tree["migration"]
    pins = _kind(tree, "pins")
    assert pins["count"] == 1 and pins["children"][0]["label"] == "ramen"
    assert pins["children"][0]["children"][0]["label"] == "Sumo Ramen"
    routes = _kind(tree, "routes")
    labels = [node["label"] for node in routes["children"]]
    assert "Canal trail" in labels and "canal walk" in labels
    areas = _kind(tree, "areas")
    assert {node["label"] for node in areas["children"]} >= {"Yard", "basin"}
    assert _kind(tree, "watches")["count"] == 1
    assert _kind(tree, "scenes")["children"][0]["children"][0]["id"] == "scene:Harbor"
    assert "path" not in json.dumps(tree)


def test_hide_and_show_round_trip_and_group_delete_needs_confirm(tmp_path, monkeypatch):
    st, watches = _seed(tmp_path, monkeypatch)
    book = _book(st, watches)
    hidden = book.set_visible("kind:routes", False)
    assert hidden["ok"] and hidden["visible"] is False
    specs = [spec for _name, args in st.restore_commands("s") if _name == "annotate_map" for spec in args["annotations"]]
    assert all(spec.get("type") != "route" for spec in specs)
    assert all(sh.get("kind") != "line" for _name, sh in st.restore_commands("s") if _name == "hermes_draw")
    pin = next(row for row in book.entries() if row["label"] == "Sumo Ramen")
    assert book.set_visible(pin["id"], False)["ok"]
    assert st.annotations("s")[0]["spec"]["visible"] is False
    assert all(spec.get("label") != "Sumo Ramen" for _n, args in st.restore_commands("s")
               if _n == "annotate_map" for spec in args["annotations"])
    assert book.set_visible("kind:routes", True)["ok"]
    shown = [spec.get("label") for _n, args in st.restore_commands("s") if _n == "annotate_map" for spec in args["annotations"]]
    assert "Canal trail" in shown
    refused = book.delete("kind:pins", False)
    assert refused["error"] == "confirm_required"
    assert book.delete(pin["id"], False)["ok"]
    assert book.tree()["counts"]["pins"] == 0
    empty = book.delete("kind:pins", True)
    assert empty["ok"] and empty["removed"] == 0


def test_update_label_title_and_description(tmp_path, monkeypatch):
    st, watches = _seed(tmp_path, monkeypatch)
    book = _book(st, watches)
    pin = next(row for row in book.entries() if row["source"] == "annotation" and row["kind"] == "pins")
    out = book.update(pin["id"], {"label": "Sumo", "title": "Sumo", "description_md": "rich broth"})
    assert out["ok"]
    spec = st.annotations("s")[0]["spec"]
    assert spec["label"] == "Sumo" and spec["title"] == "Sumo" and spec["description_md"] == "rich broth"
    watch = next(row for row in book.entries() if row["source"] == "watch")
    assert book.update(watch["id"], {"title": "Dock", "description_md": "hourly"})["ok"]
    saved = watches.data["watches"]["w-0123abcd"]
    assert saved["name"] == "Dock" and saved["description"] == "hourly"
    scene = next(row for row in book.entries() if row["source"] == "scene")
    assert book.update(scene["id"], {"title": "Old port", "description_md": "notes"})["ok"]
    data = json.loads((tmp_path / "plugin-data" / "godseye" / "investigations" / "harbor.json").read_text())
    assert data["title"] == "Old port" and data["notes"] == "notes"


def test_export_import_round_trip_scrubs_secrets_and_rejects_bad_files(tmp_path, monkeypatch):
    st, watches = _seed(tmp_path, monkeypatch)
    book = _book(st, watches)
    with st.lock:
        spec = st.scene("s")["annotations"][0]["spec"]
        spec["api_key"] = "sekret"
        spec["description_md"] = "token=sekret and a normal sentence"
    first = book.document()
    blob = json.dumps(first)
    assert "sekret" not in blob and "api_key" not in blob
    assert "[redacted]" in blob
    exported = book.export("round.json")
    assert exported["ok"] and exported["document"]["schema"] == SCHEMA
    assert str(tmp_path) in exported["path"] and exported["path"].endswith("round.json")
    assert book.export("/tmp/not-here.json")["ok"] is False
    st.record("s", "clear_annotations", {}, {"ok": True})
    watches.data["watches"].clear()
    imported = book.import_doc(json.loads(Path(exported["path"]).read_text()), True, False, False)
    assert imported["ok"]
    second = book.document()
    assert second["annotations"] == first["annotations"]
    assert second["shapes"] == first["shapes"]
    assert second["filters"] == first["filters"]
    assert second["watches"] == first["watches"]
    assert second["scenes"] == first["scenes"]
    assert book.import_doc({"schema": "nope"}, True, False, False)["error"] == "bad_args"
    bad = tmp_path / "plugin-data" / "godseye" / "overlays" / "keys.env.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{}")
    refused = handle(st, "s", {"action": "import", "source": str(bad)}, watches=watches)
    assert refused["ok"] is False


def test_tool_stays_on_annotate_and_dispatch_returns_a_tree(tmp_path, monkeypatch):
    st, watches = _seed(tmp_path, monkeypatch)
    spec, forwarded, _extra, err = router.prepare("godseye_annotate", {"op": "overlays", "action": "tree"}, "s")
    assert err is None and spec.op == "overlays" and spec.legacy == "godseye_overlays"
    assert forwarded.get("action") == "tree" and "op" not in forwarded
    relay = live.Relay(st)
    monkeypatch.setattr(live, "current", lambda: relay)
    # The relay's watch list is the process store. Pass the fixture through the tool by
    # calling the relay with the same scene the Book uses, then the extra handler.
    raw = hermes_tools.extra_handler("godseye_overlays")({"action": "tree", "detail": "full"}, session_id="s")
    body = json.loads(raw)
    assert body["ok"] is True and body["schema"] == SCHEMA
    assert {group["kind"] for group in body["groups"]} == {"pins", "routes", "areas", "watches", "scenes"}

    class Ctx:
        def __init__(self):
            self.tools = {}

        def register_tool(self, **kw):
            self.tools[kw["name"]] = kw

    ctx = Ctx()
    hermes_tools.register_tools(ctx)
    assert len(ctx.tools) == 11
    assert "godseye_overlays" not in ctx.tools
    assert "overlays" in ctx.tools["godseye_annotate"]["schema"]["parameters"]["properties"]["op"]["enum"]


def test_pane_retires_the_chip_strip_and_mounts_the_button():
    html = panel_inject.prepare("<html><head></head><body></body></html>")
    assert "__hermesOverlays" in html and "hermes-gev-overlays-btn" in html
    assert "The bottom pin-category strip was retired." in html
    pins = (ROOT / "gev" / "panel_pins.js").read_text()
    assert "hermes-gev-filters" not in pins and "setOverlay" in pins
    host = (ROOT / "desktop" / "plugin.js").read_text()
    assert "Hide overlays" in host and "hermes-gev:overlays" in host and "id: 'hermes-overlays'" in host
    assert host.index("aria-controls': 'hermes-overlays'") < host.index("Hide control room")


@needs_node
def test_js_overlay_hides_a_pin_the_filters_would_show():
    from test_pins import MOCK
    src = (ROOT / "gev" / "panel_pins.js").read_text() + "\n;\n" + (ROOT / "gev" / "panel_overlays.js").read_text()
    body = r"""
      document.getElementById = (id) => (document.body.children || []).find((el) => el.id === id || el.attrs?.id === id) || null
      const P = window.__hermesPins
      P.add('anno-1', 45.5, -73.5, { label: 'Sumo', tags: { cuisine: 'ramen' } })
      const off = window.__hermesOverlays.apply([{ id: 'ann:a1', source: 'annotation', kind: 'pins', paneId: 'anno-1', visible: false }])
      const hidden = entities.filter((e) => e._hermesPin === 'anno-1').every((e) => e.show === false)
      P.setFilters({ categories: ['ramen'] })
      const still = entities.filter((e) => e._hermesPin === 'anno-1').every((e) => e.show === false)
      window.__godsEyeView.annotations = {}
      const redraw = window.__hermesOverlays.apply([{ id: 'ann:a2', source: 'annotation', kind: 'routes', paneId: 'anno-2', visible: false }])
      window.__hermesOverlays.mount()
      const btn = document.getElementById('hermes-gev-overlays-btn')
      console.log(JSON.stringify({ off, hidden, still, redraw, mounted: Boolean(btn), title: btn && btn.title }))
    """
    prog = MOCK + "\n;" + src + "\n;(async () => {" + body + "})().catch((e) => { console.error(e); process.exit(1) })"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["off"]["ok"] and got["hidden"] and got["still"]
    assert got["redraw"]["needsRedraw"] is True
    assert got["mounted"] and "retired" in got["title"]


class _RecordingRelay:
    """Minimal relay: records pane commands and can report no open pane."""

    def __init__(self, panes=True):
        self.sent = []
        self._panes = panes

    def open_panes(self):
        return [object()] if self._panes else []

    def submit(self, name, args, **kw):
        self.sent.append((name, args, kw))
        return {"ok": True, "action": "open"}


def test_run_uses_http_relay_when_live_current_is_none(tmp_path, monkeypatch):
    from gev import bridge, overlays as overlays_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    calls = {}

    def fake_call(path, token, body=None, timeout=None):
        calls.update(path=path, token=token, body=body, timeout=timeout)
        return {"ok": True, "schema": SCHEMA, "scene": body.get("session_id")}

    monkeypatch.setattr(live, "current", lambda: None)
    monkeypatch.setattr(live, "call", fake_call)
    monkeypatch.setattr(bridge, "ensure_token", lambda: "test-token")

    out = overlays_mod.run({"op": "overlays", "action": "tree"}, "s")
    assert out["ok"] is True
    assert calls["path"] == "/overlays"
    assert calls["token"] == "test-token"
    assert calls["body"]["action"] == "tree"
    assert calls["body"]["session_id"] == "s"


def test_run_returns_relay_unavailable_only_for_a_true_outage(tmp_path, monkeypatch):
    from gev import bridge, overlays as overlays_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(live, "current", lambda: None)
    monkeypatch.setattr(live, "call", lambda *a, **k: {
        "ok": False, "error": "relay_unavailable", "message": "down"})
    monkeypatch.setattr(bridge, "ensure_token", lambda: "test-token")

    out = overlays_mod.run({"action": "tree"}, "s")
    assert out["ok"] is False and out["error"] == "relay_unavailable"
    assert out.get("fix")


def test_run_prefers_the_in_process_relay(tmp_path, monkeypatch):
    from gev import overlays as overlays_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class Relay:
        def overlays_op(self, body):
            return {"ok": True, "via": "relay", "body": body}

    monkeypatch.setattr(live, "current", lambda: Relay())
    monkeypatch.setattr(live, "call", lambda *a, **k: (_ for _ in ()).throw(AssertionError("HTTP fallback used")))
    out = overlays_mod.run({"action": "tree"}, "s")
    assert out["via"] == "relay" and out["body"]["session_id"] == "s"


def test_open_action_drives_the_pane_sidebar_hook(tmp_path, monkeypatch):
    st, watches = _seed(tmp_path, monkeypatch)
    relay = _RecordingRelay()
    out = handle(st, "s", {"action": "open"}, watches=watches, relay=relay)
    assert out["ok"] is True and out["action"] == "open" and out["sidebar"] == "overlays"
    assert relay.sent[0][0] == "hermes_overlay" and relay.sent[0][1]["action"] == "open"
    assert relay.sent[0][2].get("record") is False


def test_show_without_an_id_opens_the_sidebar(tmp_path, monkeypatch):
    st, watches = _seed(tmp_path, monkeypatch)
    relay = _RecordingRelay()
    out = handle(st, "s", {"action": "show"}, watches=watches, relay=relay)
    assert out["ok"] is True and out["action"] == "open"
    assert relay.sent[0][0] == "hermes_overlay" and relay.sent[0][1]["action"] == "open"


def test_open_without_a_pane_is_a_clear_failure(tmp_path, monkeypatch):
    st, watches = _seed(tmp_path, monkeypatch)
    out = handle(st, "s", {"action": "open"}, watches=watches, relay=_RecordingRelay(panes=False))
    assert out["ok"] is False and out["error"] == "no_pane" and out.get("fix")


@needs_node
def test_overlay_open_hook_posts_the_sidebar_message():
    from test_pins import MOCK
    src = (ROOT / "gev" / "panel_overlays.js").read_text()
    body = r"""
      const viaApply = window.__hermesOverlays.apply({ action: 'open' })
      const viaCall = window.__hermesOverlays.open()
      console.log(JSON.stringify({ viaApply, viaCall, posted }))
    """
    prog = MOCK + "\n;" + src + "\n;(async () => {" + body + "})().catch((e) => { console.error(e); process.exit(1) })"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["viaApply"]["ok"] and got["viaApply"]["action"] == "open"
    assert got["viaCall"]["ok"] and got["viaCall"]["action"] == "open"
    messages = [m for m in got["posted"] if m.get("type") == "hermes-gev:overlays"]
    assert messages and all(m.get("detail", {}).get("open") is True for m in messages)
