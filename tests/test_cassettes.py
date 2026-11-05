"""Contract tests replay recorded fixtures. Public demo servers are never contacted."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from gev import geoexport, measure as M, poi, watch
import netblock
from netblock import REQUIRED, playing

ROOT = Path(__file__).parents[1]
FIRMS_KEY = "fixture-firms-key-1"
PUBLIC = {
    "osrm": "https://routing.openstreetmap.de",
    "valhalla": "https://valhalla1.openstreetmap.de",
    "nominatim": "https://nominatim.openstreetmap.org",
}
AREA = {"bbox": [45.4, -74.0, 45.7, -73.4]}
_FORBIDDEN = (
    re.compile(r"api[_-]?key", re.I),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{8}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}"),
    re.compile(r"Bearer\s+\S"),
    re.compile(r"Authorization\s*:"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"MAP_KEY"),
    re.compile(r"sk_live_|ghp_|github_pat_|x-access-token", re.I),
    re.compile(r"\.(?:internal|local|lan)\b", re.I),
)


@pytest.fixture(autouse=True)
def _reset_poi(monkeypatch):
    monkeypatch.setattr(poi, "NOMINATIM_GAP_S", 0)
    monkeypatch.setattr(poi, "NOMINATIM", PUBLIC["nominatim"])
    monkeypatch.setattr(M, "OSRM", PUBLIC["osrm"])
    monkeypatch.setattr(M, "VALHALLA", PUBLIC["valhalla"])
    monkeypatch.delenv("OVERPASS_UPSTREAMS", raising=False)
    monkeypatch.delenv("GODSEYE_OVERPASS_URL", raising=False)
    poi._cache.clear()
    poi._breaker.clear()
    poi._last_nominatim[0] = 0.0
    yield
    poi._cache.clear()
    poi._breaker.clear()


def test_cassettes_are_the_recorded_set_and_are_scrubbed():
    names = netblock.cassette_names()
    assert names == sorted(REQUIRED)
    for path in netblock.CASSETTE_DIR.glob("*.json"):
        text = path.read_text()
        for pat in _FORBIDDEN:
            assert not pat.search(text), f"{path.name} matches {pat.pattern}"
        assert FIRMS_KEY not in text
        json.loads(text)


def test_public_hosts_are_blocked_and_loopback_is_not():
    import urllib.request
    with pytest.raises(netblock.NetworkBlocked) as ei:
        urllib.request.urlopen(
            f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{FIRMS_KEY}/VIIRS_SNPP_NRT/-74,45,-73,46/1",
            timeout=1)
    assert FIRMS_KEY not in str(ei.value)
    assert "firms.modaps.eosdis.nasa.gov" in str(ei.value)
    with pytest.raises(netblock.NetworkBlocked):
        urllib.request.urlopen("https://routing.openstreetmap.de/routed-foot/route/v1/foot/0,0;1,1", timeout=1)
    try:
        urllib.request.urlopen("http://127.0.0.1:9/", timeout=1)
    except netblock.NetworkBlocked:
        pytest.fail("loopback was blocked")
    except Exception:
        pass


def test_route_without_a_cassette_does_not_call_out():
    out = M.measure({"op": "route", "points": [(45.5, -73.56), (45.51, -73.55)], "mode": "walk"})
    assert out["ok"] is False and out["error"] == "routing_unavailable"


def test_measure_route_replays_osrm():
    with playing("osrm_route"):
        out = M.measure({"op": "route", "mode": "walk", "points": [(45.5017, -73.5673), (45.5088, -73.554)]})
    assert out["ok"] and out["meters"] == 1240.5 and out["minutes"] == 14.8
    assert out["line"][0] == [45.5017, -73.5673]
    assert "routing.openstreetmap.de" in out["source"]


def test_isochrone_replays_valhalla():
    with playing("valhalla_isochrone"):
        out = M.isochrone({"lat": 45.5017, "lon": -73.5673, "minutes": [10], "mode": "walk"})
    assert out["ok"] and out["approximate"] is False
    assert [c["minutes"] for c in out["contours"]] == [10]
    assert out["contours"][0]["polygon"][0] == [45.49, -73.58]
    assert "Valhalla" in out["source"] and out["contours"][0]["areaKm2"] > 0


def test_find_places_replays_nominatim_and_overpass():
    with playing("nominatim", "overpass_poi"):
        out = poi.find_places({"query": "ramen restaurants in Montreal"})
    assert out["ok"] and out["source"] == "overpass:overpass.kumi.systems"
    names = [p["name"] for p in out["places"]]
    assert "Sansotei Ramen" in names and "Yokato Yokabai" in names
    hit = next(p for p in out["places"] if p["name"] == "Sansotei Ramen")
    assert hit["lat"] == 45.5088 and hit["lon"] == -73.554
    assert hit["opening_hours"] == "Mo-Su 11:30-22:00" and hit["website"] == "https://sansotei.example"


def test_watch_checks_replay_usgs_firms_and_opensky(monkeypatch, tmp_path):
    monkeypatch.setenv("FIRMS_MAP_KEY", FIRMS_KEY)
    monkeypatch.setenv("GODSEYE_WATCH_LOOP", "0")
    with playing("usgs_quakes", "firms_csv", "opensky_states"):
        quakes = watch.feed_earthquakes(watch.normalize_area(AREA), {})
        fires = watch.feed_fires(watch.normalize_area(AREA), {})
        flights = watch.feed_flights(watch.normalize_area(AREA), {})
        st = watch.WatchStore(tmp_path / "w.json", clock=lambda: 1000.0)
        made = st.create({"area": AREA, "feeds": ["earthquakes", "fires", "flights"], "baseline": False,
                          "name": "Montreal hazards"}, session_id="s")
        report = st.check(made["watch"]["id"])
    assert quakes["status"] == "OK" and quakes["items"][0]["lat"] == 45.55 and "Montreal" in quakes["items"][0]["title"]
    assert fires["status"] == "OK" and fires["items"][0]["lat"] == 45.52 and "12.5" in fires["items"][0]["title"]
    assert flights["status"] == "OK" and flights["items"][0]["title"] == "Flight ACA123"
    assert report["new"] == 3
    assert {k: v["status"] for k, v in report["feeds"].items()} == {
        "earthquakes": "OK", "fires": "OK", "flights": "OK"}
    blob = json.dumps({"quakes": quakes, "fires": fires, "flights": flights, "report": report})
    assert FIRMS_KEY not in blob


def test_export_runs_while_public_network_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    data = {"name": "Offline", "notes": "no network", "annotations": [
        {"id": "a1", "spec": {"type": "pin", "latitude": 45.5, "longitude": -73.5, "label": "Pin",
                              "description_md": "local note"}}]}
    out = geoexport.export_data(data, fmt="both")
    assert out["ok"] and not geoexport.validate_geojson(json.loads(Path(out["geojson"]).read_text()))
    assert not geoexport.validate_kml(Path(out["kml"]).read_text())
    assert "Offline" in out["summary"]
