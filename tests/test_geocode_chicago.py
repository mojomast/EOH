"""P02: Nominatim geocoding for common city queries (Chicago) and clear failures."""
from __future__ import annotations

import json
import urllib.parse

import pytest

from gev import poi

CHICAGO = {
    "place_id": 1,
    "lat": "41.8781",
    "lon": "-87.6298",
    "display_name": "Chicago, Cook County, Illinois, United States",
    "type": "city",
    "category": "boundary",
    "boundingbox": ["41.64", "42.02", "-87.94", "-87.52"],
}


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    poi._cache.clear()
    poi._breaker.clear()
    monkeypatch.setattr(poi, "NOMINATIM_GAP_S", 0)


def _query(url: str) -> str:
    return (urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("q") or [""])[0]


def _fake_http(hits, fallback="[]"):
    calls: list[str] = []

    def http(url, data=None, timeout=20):
        q = _query(url)
        calls.append(q)
        if q in hits:
            return 200, json.dumps([CHICAGO])
        return 200, fallback

    return http, calls


def test_geocode_chicago_hit_shape(monkeypatch):
    http, _calls = _fake_http({"Chicago"})
    monkeypatch.setattr(poi, "_http", http)

    out = poi.geocode("Chicago")

    assert out is not None and out["lat"] == pytest.approx(41.8781)
    assert out["lon"] == pytest.approx(-87.6298)
    assert "Chicago" in out["name"]
    assert out["type"] == "city"
    assert out["bbox"] == [41.64, -87.94, 42.02, -87.52]


def test_bare_city_retries_with_country_shaping(monkeypatch):
    http, calls = _fake_http({"Chicago, USA"})
    monkeypatch.setattr(poi, "_http", http)

    out = poi.geocode("Chicago")

    assert out is not None and out["lat"] == pytest.approx(41.8781)
    assert calls == ["Chicago", "Chicago, USA"]


def test_state_abbreviation_expands_to_full_name(monkeypatch):
    http, calls = _fake_http({"Chicago, Illinois, USA"})
    monkeypatch.setattr(poi, "_http", http)

    out = poi.geocode("Chicago, IL")

    assert out is not None and out["lat"] == pytest.approx(41.8781)
    assert calls[0] == "Chicago, IL"
    assert "Chicago, Illinois, USA" in calls


def test_state_abbreviation_without_comma_is_understood(monkeypatch):
    http, calls = _fake_http({"Chicago, Illinois, USA"})
    monkeypatch.setattr(poi, "_http", http)

    out = poi.geocode("Chicago IL")

    assert out is not None and out["lat"] == pytest.approx(41.8781)
    assert "Chicago, Illinois, USA" in calls


def test_empty_nominatim_is_no_match(monkeypatch):
    http, _calls = _fake_http(set())
    monkeypatch.setattr(poi, "_http", http)

    out = poi.geocode("Atlantis")

    assert out["ok"] is False
    assert out["code"] == "no_match"
    assert out["error"] == "no_match"
    assert out["fix"]


def test_network_failure_is_geocoder_unavailable(monkeypatch):
    def http(url, data=None, timeout=20):
        raise TimeoutError("timed out")

    monkeypatch.setattr(poi, "_http", http)

    out = poi.geocode("Chicago")

    assert out["ok"] is False
    assert out["code"] == "geocoder_unavailable"
    assert out["fix"]
