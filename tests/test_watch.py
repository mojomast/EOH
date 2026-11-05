"""Area watches (gev/watch.py): areas, dedupe of already-seen items, KEY_MISSING, delivery."""
import json

import pytest

from gev import hermes_tools, watch
from gev.scene import SceneStore


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def quake(i, lat=45.5, lon=-73.6):
    return {"id": f"usgs:{i}", "lat": lat, "lon": lon, "title": f"M3 quake {i}", "description_md": "x", "links": []}


def mk(tmp_path, items, pin=None, **kw):
    feed = {"items": items}
    st = watch.WatchStore(tmp_path / "w.json", fetchers={"earthquakes": lambda a, c: {"status": "OK", "items": list(feed["items"])},
                                                         **kw.pop("fetchers", {})}, pin=pin, clock=Clock())
    return st, feed


@pytest.fixture(autouse=True)
def no_loop(monkeypatch):
    monkeypatch.setenv("GODSEYE_WATCH_LOOP", "0")


def test_area_forms_and_containment():
    b = watch.normalize_area({"bbox": [45, -74, 46, -73]})
    assert watch.contains(b, 45.5, -73.5) and not watch.contains(b, 47, -73.5)
    c = watch.normalize_area({"lat": 45.5, "lon": -73.6, "radius_km": 10})
    assert watch.contains(c, 45.55, -73.6) and not watch.contains(c, 45.5, -73.4)  # in bbox corner, outside circle
    p = watch.normalize_area({"polygon": [[45, -74], [46, -74], [45, -73]]})
    assert watch.contains(p, 45.2, -73.8) and not watch.contains(p, 45.9, -73.1)
    for bad in ({}, {"bbox": [46, -74, 45, -73]}, {"lat": 1, "lon": 2}, {"polygon": [[1, 2]]}):
        with pytest.raises(ValueError):
            watch.normalize_area(bad)
    v = watch.view_area({"ok": True, "result": {"camera": {"targetLat": 45.5, "targetLon": -73.6, "rangeM": 5000}}})
    assert v["kind"] == "circle" and v["radius_km"] == 5


def test_baseline_then_only_new_items_alert_and_seen_are_never_realerted(tmp_path):
    pins = []
    st, feed = mk(tmp_path, [quake(1), quake(2)], pin=lambda s, a: pins.append(a) or {"ok": True})
    w = st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["earthquakes"]}, session_id="s1")["watch"]
    r1 = st.check(w["id"])
    assert r1["new"] == 0 and r1["baseline_run"] and r1["feeds"]["earthquakes"]["baselined"] == 2 and not pins
    feed["items"] = [quake(1), quake(2), quake(3), quake(9, lat=10, lon=10)]  # 9 is outside the area
    r2 = st.check(w["id"])
    assert r2["new"] == 1 and r2["delivered"] == "queued" and pins[0][0]["title"] == "M3 quake 3"
    assert pins[0][0]["tags"] == {"category": "earthquakes", "watch": w["name"]} and "Watch:" in pins[0][0]["description_md"]
    r3 = st.check(w["id"])  # same items again: dedupe
    assert r3["new"] == 0 and len(pins) == 1
    # persisted last-seen state survives a restart
    st2 = watch.WatchStore(tmp_path / "w.json", fetchers=st.fetchers, clock=Clock())
    assert st2.check(w["id"])["new"] == 0


def test_baseline_false_alerts_on_first_run(tmp_path):
    st, _ = mk(tmp_path, [quake(1)])
    w = st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["earthquakes"], "baseline": False})["watch"]
    assert st.check(w["id"])["new"] == 1


def test_key_missing_is_reported_not_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "key_value", lambda k: None)
    st = watch.WatchStore(tmp_path / "w.json", clock=Clock())
    out = st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["fires", "ships"]})
    assert out["key_missing"] == {"fires": "FIRMS_MAP_KEY", "ships": "AISSTREAM_API_KEY"}
    r = st.check(out["watch"]["id"])
    assert r["feeds"]["fires"]["status"] == "KEY_MISSING" and r["feeds"]["fires"]["key"] == "FIRMS_MAP_KEY"
    assert r["feeds"]["ships"]["status"] == "KEY_MISSING"
    assert st.list()["watches"][0]["status"]["fires"]["status"] == "KEY_MISSING"


def test_firms_error_never_leaks_the_key(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "key_value", lambda k: "SECRETKEY123")
    def boom(url, timeout=20):
        raise RuntimeError(url)
    monkeypatch.setattr(watch, "_get", boom)
    out = watch.feed_fires(watch.normalize_area({"bbox": [45, -74, 46, -73]}), {})
    assert out["status"] == "ERROR" and "SECRETKEY123" not in json.dumps(out)


def test_pause_delete_needs_confirm_and_due(tmp_path):
    st, _ = mk(tmp_path, [])
    wid = st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["earthquakes"], "interval_s": 5})["watch"]["id"]
    assert st.data["watches"][wid]["interval_s"] == watch.MIN_INTERVAL_S
    assert st.due() == [wid]
    st.check(wid)
    assert st.due() == []
    st.clock.t += 61
    assert st.due() == [wid]
    st.set_active(wid, False)
    assert st.due() == []
    assert st.delete(wid, confirm=False)["code"] == "CONFIRM_REQUIRED" and wid in st.data["watches"]
    assert st.delete(wid, confirm=True)["ok"] and not st.data["watches"]
    assert st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["nope"]})["code"] == "BAD_FEEDS"


def test_alerts_queue_for_the_chat_turn_and_inject_when_asked(tmp_path):
    st, feed = mk(tmp_path, [])
    a = st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["earthquakes"]}, session_id="s1")["watch"]["id"]
    b = st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["earthquakes"], "deliver": "inject"}, session_id="s2")["watch"]["id"]
    st.check(a); st.check(b)
    feed["items"] = [quake(5)]
    sent = []
    st.deliver = lambda w, text: sent.append((w["session_id"], text)) or True
    assert st.check(a)["delivered"] == "queued"
    assert st.check(b)["delivered"] == "inject" and sent[0][0] == "s2" and "M3 quake 5" in sent[0][1]
    assert st.drain_alerts("other") == []
    got = st.drain_alerts("s1")
    assert len(got) == 1 and "M3 quake 5" in watch.alerts_context(got)
    assert st.drain_alerts("s1") == []  # delivered once


def test_pre_llm_call_hook_and_inject_binding(monkeypatch, tmp_path):
    st, feed = mk(tmp_path, [])
    monkeypatch.setattr(watch, "_store", st)

    class Ctx:
        hooks, injected = {}, []
        def register_hook(self, n, cb): self.hooks[n] = cb
        def inject_message(self, content, role="user", session_key=None): self.injected.append(session_key); return True
    ctx = Ctx()
    assert hermes_tools.bind_watch_delivery(ctx) == {"hook": True, "inject": True}
    wid = st.create({"area": {"bbox": [45, -74, 46, -73]}, "feeds": ["earthquakes"]}, session_id="s1")["watch"]["id"]
    st.check(wid); feed["items"] = [quake(7)]; st.check(wid)
    assert ctx.hooks["pre_llm_call"](session_id="s1")["context"].count("M3 quake 7") == 1
    assert ctx.hooks["pre_llm_call"](session_id="s1") is None


def test_watch_tools_via_handler(monkeypatch, tmp_path):
    st, _ = mk(tmp_path, [quake(1)])
    monkeypatch.setattr(watch, "_store", st)
    h = lambda n, a: json.loads(hermes_tools.extra_handler(n)(a, session_id="s9"))
    out = h("godseye_watch_create", {"area": {"lat": 45.5, "lon": -73.6, "radius_km": 20}, "feeds": ["earthquakes"]})
    assert out["ok"] and out["first_check"]["baseline_run"] and out["watch"]["session_id"] == "s9"
    wid = out["watch"]["id"]
    assert h("godseye_watch_list", {})["watches"][0]["seen_counts"] == {"earthquakes": 1}
    assert h("godseye_watch_list", {"check": wid})["new"] == 0
    assert h("godseye_watch_pause", {"id": wid})["watch"]["active"] is False
    assert h("godseye_watch_delete", {"id": wid})["code"] == "CONFIRM_REQUIRED"
    assert h("godseye_watch_delete", {"id": wid, "confirm": True})["ok"]


def test_scene_keeps_filters_and_restores_them_after_pins():
    s = SceneStore()
    s.record("k", "annotate_map", {"annotations": [{"type": "pin", "latitude": 1, "longitude": 2, "label": "A"}]},
             {"ok": True, "result": {"items": [{"ok": True, "latitude": 1, "longitude": 2}]}})
    f = {"categories": ["ramen"], "min_rating": None, "open_now": True, "include_unknown": True, "cluster": True}
    s.record("k", "hermes_pin_filter", {"categories": ["ramen"], "open_now": True}, {"ok": True, "filters": f})
    assert s.list("k")["filters"] == f
    names = [n for n, _ in s.restore_commands("k")]
    assert names.index("hermes_pin_filter") > names.index("annotate_map")
    s.record("k", "hermes_pin_filter", {"action": "state"}, {"ok": True, "filters": {}})
    assert s.list("k")["filters"] == f  # state reads do not change it
    s.note_filters("k", {"categories": [], "min_rating": None, "open_now": False})
    assert s.list("k")["filters"] is None and "hermes_pin_filter" not in [n for n, _ in s.restore_commands("k")]


def test_poi_pins_carry_filter_tags():
    from gev import poi
    a = poi.pin_annotations([{"name": "Sumo Ramen", "lat": 45.5, "lon": -73.6, "cuisine": "ramen;japanese",
                              "opening_hours": "Mo-Su 11:00-22:00", "amenity": "restaurant"}])[0]
    assert a["tags"] == {"cuisine": "ramen;japanese", "opening_hours": "Mo-Su 11:00-22:00", "amenity": "restaurant"}
    assert "tags" in hermes_tools.extend_live_schema("annotate_map", {"properties": {"annotations": {"items": {"properties": {}}}}})[
        "properties"]["annotations"]["items"]["properties"]
