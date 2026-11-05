"""Research pins, investigations/reports, and always-on intent-shaped drawer enrichment."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gev import composer, enrich, hermes_tools as h, investigations, poi, research
from gev.scene import SceneStore

ROOT = Path(__file__).parents[1]


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    return tmp_path / "hermes"


def test_light_drawer_never_empty_and_shapes_by_intent():
    ramen = enrich.light_drawer(
        {"type": "pin", "label": "Sumo", "latitude": 45.5, "longitude": -73.5,
         "cuisine": "ramen", "opening_hours": "11-22", "website": "https://sumo.example",
         "menu": "https://sumo.example/menu", "phone": "+1"},
        intent="ramen near me")
    assert ramen["description_md"]
    assert "Cuisine" in ramen["description_md"]
    assert "Hours" in ramen["description_md"]
    assert any(l["label"] == "Menu" for l in ramen["links"])

    fire = enrich.light_drawer(
        {"type": "pin", "label": "Hotspot", "latitude": 1, "longitude": 2,
         "confidence": 85, "frp": 12.3, "acq_date": "2026-11-05", "source": "NASA FIRMS"},
        intent="active fires watch")
    assert "Confidence" in fire["description_md"] and "FIRMS" in fire["description_md"]

    bare = enrich.ensure_drawer({"type": "pin", "label": "X", "latitude": 0, "longitude": 1},
                               intent="look here")
    assert enrich.has_drawer(bare)


def test_enrich_annotations_fills_every_pin():
    anns = enrich.enrich_annotations([
        {"type": "pin", "label": "A", "latitude": 1, "longitude": 2},
        {"type": "route", "points": [], "label": "R"},
        {"type": "pin", "label": "B", "latitude": 3, "longitude": 4, "description_md": "kept"},
    ], intent="museums in Paris")
    assert enrich.has_drawer(anns[0])
    assert anns[1]["type"] == "route" and "description_md" not in anns[1]
    assert anns[2]["description_md"].startswith("kept") or "kept" in anns[2]["description_md"]


def test_pin_annotations_and_hit_drawer_use_intent():
    places = [{"name": "Sumo", "lat": 45.5, "lon": -73.5, "cuisine": "ramen",
               "opening_hours": "11-22", "website": "https://s.example", "osm": "node/1"}]
    pins = poi.pin_annotations(places, intent="ramen restaurants in Montreal")
    assert enrich.has_drawer(pins[0])
    assert "Cuisine" in pins[0]["description_md"] or "ramen" in pins[0]["description_md"].lower()

    d = composer.hit_drawer(
        {"section": "fires", "label": "F1", "lat": 10.0, "lon": 20.0, "distance_km": 3.2, "confidence": 90},
        "Tokyo", intent="FIRMS fires last 24h")
    assert enrich.has_drawer(d)
    assert "Confidence" in d["description_md"] or "fires" in d["description_md"].lower()


def test_annotate_map_schema_requires_intent_docs():
    cat = {t["name"]: t for t in h.load_live_catalog()}
    ann = h.extend_live_schema("annotate_map", h.live_schema(cat["annotate_map"].get("parameters")))
    assert "intent" in ann["properties"] and "context" in ann["properties"]
    assert "intent" in h.EXTRA_TOOLS["godseye_find_places"][1]["properties"]
    assert "intent" in h.EXTRA_TOOLS["godseye_brief_and_show"][1]["properties"]
    assert "intent" in h.EXTRA_TOOLS["godseye_live_run_tour"][1]["properties"]


def test_research_one_marks_unavailable(monkeypatch, hermes_home):
    monkeypatch.setattr(research, "_http", lambda *a, **k: (404, ""))
    research._mem.clear()
    out = research.research_one({"label": "Nowhereville", "latitude": 1.0, "longitude": 2.0}, budget=[4])
    assert out["ok"] and "Sources" in out["description_md"]
    assert any(s.get("status") == "unavailable" for s in out["sources"])


def test_research_pins_writes_drawer_and_persists(hermes_home, monkeypatch):
    st = SceneStore(hermes_home / "plugin-data" / "godseye" / "scenes.json")
    st.record("s", "annotate_map",
              {"annotations": [{"type": "pin", "latitude": 45.5, "longitude": -73.5, "label": "Sumo",
                                "links": [{"url": "https://en.wikipedia.org/wiki/Ramen", "label": "Wikipedia"}]}]},
              {"ok": True, "result": {"items": [{"ok": True, "id": "anno-1", "latitude": 45.5, "longitude": -73.5}]}})
    monkeypatch.setattr(research, "wikipedia_summary",
                        lambda title, lang="en": {"ok": True, "title": "Ramen", "extract": "A Japanese noodle soup.",
                                                  "image": "https://upload.wikimedia.org/x.jpg",
                                                  "url": "https://en.wikipedia.org/wiki/Ramen", "lang": lang})
    monkeypatch.setattr(research, "wikipedia_geosearch", lambda *a, **k: {"ok": False, "unavailable": True})
    monkeypatch.setattr(research, "commons_search", lambda *a, **k: [])
    monkeypatch.setattr(research, "website_meta", lambda *a, **k: {"ok": False, "unavailable": True})
    monkeypatch.setattr(research, "wikidata_entity", lambda *a, **k: {"ok": False, "unavailable": True})
    calls = []
    out = research.research_pins(st, "s", all_pins=True, live=lambda n, a: calls.append((n, a)) or {"ok": True},
                                 mode="full")
    assert out["ok"] and out["updated"] == 1
    spec = st.annotations("s")[0]["spec"]
    assert "Ramen" in spec["description_md"] or "noodle" in spec["description_md"]
    assert "### Sources" in spec["description_md"]
    assert calls and calls[0][0] == "clear_annotations"


def test_investigation_save_list_load_delete_confirm(hermes_home):
    st = SceneStore(hermes_home / "plugin-data" / "godseye" / "scenes.json")
    st.record("s", "annotate_map",
              {"annotations": [{"type": "pin", "latitude": 1, "longitude": 2, "label": "A",
                                "description_md": "hello", "links": [{"url": "https://a.example", "label": "A"}]}]},
              {"ok": True, "result": {"items": [{"ok": True, "id": "anno-1", "latitude": 1, "longitude": 2}]}})
    st.record("s", "fly_to_location", {"latitude": 1, "longitude": 2, "rangeM": 500},
              {"ok": True, "result": {"camera": {"latitude": 1, "longitude": 2, "heightM": 500, "pitchDeg": -30}}})
    saved = investigations.save(st, "s", "Montreal Ramen", notes="tour stops")
    assert saved["ok"] and Path(saved["path"]).is_file()
    listed = investigations.list_all()
    assert listed["count"] >= 1 and any(i["name"] == "Montreal Ramen" for i in listed["investigations"])
    st2 = SceneStore(hermes_home / "plugin-data" / "godseye" / "scenes2.json")
    loaded = investigations.load(st2, "other", "Montreal Ramen", restore=False)
    assert loaded["ok"] and st2.list("other")["annotations"]
    refused = investigations.delete("Montreal Ramen", confirm=False)
    assert refused["error"] == "confirm_required"
    gone = investigations.delete("Montreal Ramen", confirm=True)
    assert gone["ok"] and not Path(saved["path"]).is_file()


def test_investigation_report_html_md(hermes_home):
    st = SceneStore(hermes_home / "plugin-data" / "godseye" / "scenes.json")
    st.record("s", "annotate_map",
              {"annotations": [{"type": "pin", "latitude": 1, "longitude": 2, "label": "Pin",
                                "description_md": "Body\n\n### Sources\n- **Wikipedia**: ok",
                                "images": [{"url": "https://img.example/a.jpg", "alt": "A"}],
                                "links": [{"url": "https://en.wikipedia.org/wiki/X", "label": "Wikipedia"}]}]},
              {"ok": True, "result": {"items": [{"ok": True, "id": "anno-1", "latitude": 1, "longitude": 2}]}})
    investigations.save(st, "s", "Report Demo", notes="n")
    out = investigations.export_report("Report Demo", screenshot_note="Canvas capture unavailable (test).")
    assert out["ok"]
    html = Path(out["html"]).read_text()
    md = Path(out["markdown"]).read_text()
    assert "Report Demo" in html and "Pin" in html and "Sources" in html
    assert "Report Demo" in md and "Canvas capture unavailable" in md
    assert "screenshot" in (out.get("screenshot_note") or "Canvas").lower() or out["screenshot"] is False


def test_gev_command_investigation_and_research_help():
    assert "research" in composer.HELP and "save" in composer.HELP and "report" in composer.HELP
    assert "confirm" in composer.gev_command("delete Foo")
    # delete without confirm is refused
    # (uses disk under HERMES_HOME if set by other tests — just check message path)
    msg = composer.gev_command("delete Nosuch --confirm")
    assert "⚠️" in msg or "Deleted" in msg or "not" in msg.lower()


def test_schemas_and_docs_mention_new_tools_and_intent():
    for name in ("godseye_research_pins", "godseye_investigation_save", "godseye_investigation_list",
                 "godseye_investigation_load", "godseye_investigation_delete", "godseye_investigation_report",
                 "godseye_investigation_export"):
        assert name in h.EXTRA_TOOLS
    skill = (ROOT / "skills" / "godseye-analyst" / "SKILL.md").read_text()
    readme = (ROOT / "README.md").read_text()
    cl = (ROOT / "CHANGELOG.md").read_text()
    assert "op=research" in skill and "intent" in skill
    assert "description_md" in skill
    assert "op=save" in readme
    assert "Research pins" in cl or "godseye_research_pins" in cl


def test_scene_update_annotation(hermes_home):
    st = SceneStore(hermes_home / "s.json")
    st.record("s", "annotate_map",
              {"annotations": [{"type": "pin", "latitude": 1, "longitude": 2, "label": "A"}]},
              {"ok": True, "result": {"items": [{"ok": True, "id": "anno-1", "latitude": 1, "longitude": 2}]}})
    aid = st.list("s")["annotations"][0]["id"]
    assert st.update_annotation("s", aid, {"description_md": "**hi**", "links": [{"url": "https://x.example"}]})
    assert st.annotations("s")[0]["spec"]["description_md"] == "**hi**"


def test_auto_enrich_schedules_for_empty_pin(hermes_home):
    st = SceneStore(hermes_home / "s.json")
    st.record("s", "annotate_map",
              {"annotations": [{"type": "pin", "latitude": 1, "longitude": 2, "label": "Bare"}]},
              {"ok": True, "result": {"items": [{"ok": True, "id": "anno-1", "latitude": 1, "longitude": 2}]}})
    # Force empty (record may have been enriched by light_drawer if submit path ran — scene.record does not)
    aid = st.list("s")["annotations"][0]["id"]
    assert not enrich.has_drawer(st.annotations("s")[0]["spec"])
    pushed = []
    enrich.schedule_auto_enrich(st, "s", aid, st.annotations("s")[0]["spec"],
                                intent="look at Bare", live=lambda n, a: pushed.append((n, a)) or {"ok": True})
    # wait briefly for daemon
    import time
    for _ in range(50):
        if enrich.has_drawer(st.annotations("s")[0]["spec"]):
            break
        time.sleep(0.05)
    assert enrich.has_drawer(st.annotations("s")[0]["spec"])
