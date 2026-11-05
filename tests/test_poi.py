import json
import pytest
from gev import poi


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    poi._breaker.clear(); poi._cache.clear()
    monkeypatch.setattr(poi, "NOMINATIM_GAP_S", 0)
    monkeypatch.delenv("OVERPASS_UPSTREAMS", raising=False)
    monkeypatch.delenv("GODSEYE_OVERPASS_URL", raising=False)


GEO = [{"lat": "45.50", "lon": "-73.56", "display_name": "Montréal", "type": "city",
        "boundingbox": ["45.45", "45.55", "-73.60", "-73.50"]}]
OP = {"elements": [{"type": "node", "id": i, "lat": 45.5 + i / 1000, "lon": -73.56,
                    "tags": {"name": f"Ramen {i}", "cuisine": "ramen", "website": "https://x.example",
                             "opening_hours": "Mo-Su 11:00-22:00", "addr:street": "Rue A", "addr:housenumber": str(i)}}
                   for i in range(12)]}


def test_parse_query():
    assert poi.parse_query("ramen restaurants in Montreal") == ("restaurant", "ramen", None, "Montreal")
    assert poi.parse_query("cafes near Plateau")[0] == "cafe"


def test_chain_order(monkeypatch):
    monkeypatch.setenv("OVERPASS_UPSTREAMS", "https://mine.example/api/interpreter")
    c = poi.overpass_chain()
    assert c[0] == "https://mine.example/api/interpreter" and "overpass.kumi.systems" in c[1] and len(c) == 4


def test_find_places_overpass_fallback(monkeypatch):
    calls = []
    def http(url, data=None, timeout=20):
        calls.append(url)
        if "nominatim" in url:
            return 200, json.dumps(GEO)
        if "kumi" in url:
            raise TimeoutError("timed out")
        return 200, json.dumps(OP)
    monkeypatch.setattr(poi, "_http", http)
    r = poi.find_places({"query": "ramen restaurants in Montreal"})
    assert r["ok"] and r["count"] == 12 and r["source"] == "overpass:overpass.private.coffee"
    p = r["places"][0]
    assert {"lat", "lon", "name", "address", "website", "opening_hours"} <= set(p)


def test_circuit_breaker(monkeypatch):
    hits = {"n": 0}
    def http(url, data=None, timeout=20):
        if "kumi" in url:
            hits["n"] += 1
            raise TimeoutError("t")
        return 200, json.dumps(OP)
    monkeypatch.setattr(poi, "_http", http)
    for q in ("a", "b", "c", "d"):
        poi.overpass(q)
    assert hits["n"] == poi.BREAKER_FAILS


def test_nominatim_fallback_when_all_overpass_down(monkeypatch):
    rows = [{"place_id": i, "lat": "45.5", "lon": "-73.5", "name": f"R{i}", "type": "restaurant",
             "osm_type": "node", "osm_id": i, "extratags": {"website": "https://r.example"}, "address": {}}
            for i in range(11)]
    def http(url, data=None, timeout=20):
        if data:
            raise TimeoutError("down")
        return 200, json.dumps(rows if "q=ramen&" in url else GEO)
    monkeypatch.setattr(poi, "_http", http)
    r = poi.find_places({"query": "ramen restaurants in Montreal"})
    assert r["ok"] and r["count"] == 11 and "nominatim" in r["source"]


def test_remediation_texts():
    assert "rangeM <= 15000" in poi.remediate("osm_query", {"code": "AREA_TOO_LARGE"})["remediation"]
    t = poi.remediate("osm_query", {"code": "OVERPASS_NOT_CONFIGURED"})["remediation"]
    assert "OVERPASS_UPSTREAMS=" in t and "op=find_places" in t


def test_resolve_area_degrades(monkeypatch):
    seen = []
    def call(a):
        seen.append(a); return {"ok": True, "areaId": "a1"}
    out = poi.resolve_area_fallback({"query": "Kinton Ramen Montreal"}, {"ok": False, "code": "AREA_NOT_FOUND"}, call)
    assert seen[0]["around"] is True and out["degraded"]


def test_poi_details_no_invention(monkeypatch):
    def http(url, data=None, timeout=20):
        if data:
            return 200, json.dumps(OP)
        if "nominatim" in url:
            return 200, json.dumps([{**GEO[0], "name": "Ramen 0", "extratags": {"website": "https://x.example"}}])
        if url.endswith("/menu"):
            return 200, "<p>Tonkotsu ramen $16.50</p>"
        return 200, '<a href="/menu">Menu</a>'
    monkeypatch.setattr(poi, "_http", http)
    r = poi.poi_details({"name": "Ramen 0", "city": "Montreal"})
    assert r["menu_url"] == "https://x.example/menu" and any("$16.50" in x for x in r["prices"])


def test_pins_shape():
    pins = poi.pin_annotations([{"name": "A", "lat": 1.0, "lon": 2.0}])
    assert pins[0]["latitude"] == 1.0 and pins[0]["type"] == "pin"
