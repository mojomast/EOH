"""P05: Wikipedia/geosearch grounding must prefer the exact/closer article and never
rewrite a pin id (only drawer fields may change). No network: every source is faked."""
from __future__ import annotations

import pytest

from gev import research
from gev.scene import SceneStore

LOUVRE = {"title": "Louvre", "dist": 95.0, "lat": 48.8606, "lon": 2.3376, "pageid": 1}
PAVILLON = {"title": "Pavillon de l'Horloge", "dist": 18.0, "lat": 48.8607, "lon": 2.3372, "pageid": 2}
MUSEUM = {"title": "Louvre Museum", "dist": 120.0, "lat": 48.8605, "lon": 2.3375, "pageid": 3}


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    research._mem.clear()
    return tmp_path / "hermes"


def test_ranking_prefers_exact_title_over_closer_homonym():
    hit = research._rank_wikipedia_hits([PAVILLON, LOUVRE, MUSEUM], label="Louvre")
    assert hit and hit["title"] == "Louvre"


def test_ranking_rejects_far_off_homonym_when_closer_title_exists():
    far = {"title": "Louvre", "dist": 5200.0, "lat": 10.0, "lon": 10.0}
    near = {"title": "Louvre Museum", "dist": 80.0, "lat": 48.86, "lon": 2.337}
    hit = research._rank_wikipedia_hits([far, near], label="Louvre")
    assert hit and hit["title"] == "Louvre Museum"


def test_geosearch_grounds_on_preferred_title(hermes_home, monkeypatch):
    import json

    hits = [PAVILLON, LOUVRE, MUSEUM]

    def fake_http(url, *a, **k):
        assert "list=geosearch" in url or "list=geosearch" in url.replace("%3D", "=") or "action=query" in url
        return 200, json.dumps({"query": {"geosearch": hits}})

    monkeypatch.setattr(research, "_http", fake_http)
    out = research.wikipedia_geosearch(48.8606, 2.3376, prefer_title="Louvre", label="Louvre")
    assert out["ok"] and out["title"] == "Louvre"
    assert [h["title"] for h in out["hits"]][:1] == ["Pavillon de l'Horloge"]


def test_research_one_uses_ranked_geosearch_not_first_hit(hermes_home, monkeypatch):
    monkeypatch.setattr(
        research, "wikipedia_geosearch",
        lambda *a, **k: {"ok": True, "title": PAVILLON["title"], "dist": PAVILLON["dist"],
                         "hits": [PAVILLON, LOUVRE, MUSEUM]})
    seen = []

    def fake_summary(title, lang="en"):
        seen.append(title)
        if title == "Louvre":
            return {"ok": True, "title": "Louvre", "extract": "The Louvre is a museum in Paris.",
                    "url": "https://en.wikipedia.org/wiki/Louvre", "image": None}
        return {"ok": True, "title": title, "extract": "The clock pavilion.",
                "url": "https://en.wikipedia.org/wiki/Pavillon_de_l%27Horloge", "image": None}

    monkeypatch.setattr(research, "wikipedia_summary", fake_summary)
    monkeypatch.setattr(research, "commons_search", lambda *a, **k: [])
    out = research.research_one(
        {"type": "pin", "label": "Louvre", "latitude": 48.8606, "longitude": 2.3376}, budget=[4])
    assert out["ok"]
    assert seen == ["Louvre"]
    assert "The Louvre is a museum" in out["description_md"]
    assert any(s.get("title") == "Louvre" for s in out["sources"] if s.get("name") == "Wikipedia")


def test_research_only_writes_drawer_fields_and_keeps_pin_ids(hermes_home, monkeypatch):
    st = SceneStore(hermes_home / "scenes.json")
    st.record("s", "annotate_map",
              {"annotations": [{"type": "pin", "id": "client-pin-7", "latitude": 48.8606,
                                "longitude": 2.3376, "label": "Louvre"}]},
              {"ok": True, "result": {"items": [{"ok": True, "id": "pane-pin-7",
                                                 "latitude": 48.8606, "longitude": 2.3376}]}})
    before = st.annotations("s")[0]
    assert before["id"] and before["spec"]["id"] == "client-pin-7"

    # A malicious/loose research result that carries an id must never leak it into the pin.
    monkeypatch.setattr(research, "research_one", lambda spec, budget=None: {
        "ok": True, "id": "should-not-be-written", "title": "Louvre",
        "label": "Louvre", "description_md": "### Sources\n- **Wikipedia**: ok",
        "images": [], "links": [], "sources": [], "filled": True, "channels": ["wikipedia"],
    })
    out = research.research_pins(st, "s", all_pins=True, persist=False)
    assert out["ok"] and out["updated"] == 1
    after = st.annotations("s")[0]
    assert after["id"] == before["id"]
    assert after["spec"]["id"] == "client-pin-7"
    assert after["spec"]["description_md"].startswith("### Sources")


def test_persist_redraw_keeps_scene_pin_ids(hermes_home, monkeypatch):
    st = SceneStore(hermes_home / "scenes.json")
    st.record("s", "annotate_map",
              {"annotations": [
                  {"type": "pin", "latitude": 48.8606, "longitude": 2.3376, "label": "Louvre"},
                  {"type": "pin", "latitude": 48.8607, "longitude": 2.3372, "label": "Horloge"},
              ]},
              {"ok": True, "result": {"items": [
                  {"ok": True, "id": "pane-a", "latitude": 48.8606, "longitude": 2.3376},
                  {"ok": True, "id": "pane-b", "latitude": 48.8607, "longitude": 2.3372},
              ]}})
    before = st.annotations("s")
    before_ids = [a["id"] for a in before]

    def live(name, args):
        items = args.get("annotations") if isinstance(args, dict) else None
        result = {"ok": True, "result": {"items": [
            {"ok": True, "id": f"new-pane-{i}", "latitude": a.get("latitude"),
             "longitude": a.get("longitude")} for i, a in enumerate(items or [])
        ]}} if name == "annotate_map" else {"ok": True}
        st.record("s", name, args or {}, result)
        return result

    monkeypatch.setattr(research, "research_one", lambda spec, budget=None: {
        "ok": True, "title": spec.get("label"), "label": spec.get("label"),
        "description_md": "### Sources\n- **Wikipedia**: ok",
        "images": [], "links": [], "sources": [], "filled": True, "channels": ["wikipedia"],
    })
    out = research.research_pins(st, "s", all_pins=True, live=live, persist=True)
    assert out["ok"] and out["updated"] == 2
    after = st.annotations("s")
    assert [a["id"] for a in after] == before_ids
    assert [a["spec"]["label"] for a in after] == ["Louvre", "Horloge"]
