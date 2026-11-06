"""Wayback release resolution: never emit a tile release id that redirects.

Esri's Wayback catalog is the authority. A catalog entry may be keyed by a
stale/alias id while its ``itemURL`` (or an explicit redirect field) points at
the release the tile service actually serves. Tiles requested with the alias
return a 301 to another release, which leaves the globe black. These tests use
a hand-written redirect-style mapping; they never touch the public network.
"""
from __future__ import annotations

import json

import pytest

from gev import measure as M

TILE = "https://wayback.maptiles.arcgis.com/arcgis/rest/services/World_Imagery/WMTS/1.0.0/default028mm/MapServer/tile/{release}/{{level}}/{{row}}/{{col}}"


def _item(title: str, release: int) -> dict:
    return {"itemTitle": f"World Imagery (Wayback {title})", "itemURL": TILE.format(release=release)}


@pytest.fixture(autouse=True)
def _fresh_wayback():
    M.reset_wayback_cache()
    yield
    M.reset_wayback_cache()


@pytest.fixture
def redirect_config() -> dict:
    """43291 is a stale catalog key whose tiles are served as release 7110."""
    return {
        "10": _item("2014-02-20", 10),
        "7110": _item("2022-11-02", 7110),
        "43291": _item("2026-10-29", 7110),
    }


def _serve(monkeypatch, cfg: dict) -> None:
    monkeypatch.setattr(M, "_http", lambda *a, **k: (200, json.dumps(cfg)))


def test_redirect_style_mapping_is_resolved_to_the_served_release(monkeypatch, redirect_config):
    _serve(monkeypatch, redirect_config)

    rows = M.wayback_releases()

    assert [r["release"] for r in rows] == [7110, 10]
    assert all(r["release"] != 43291 for r in rows)
    assert len({r["release"] for r in rows}) == len(rows)


def test_pick_release_never_returns_a_redirected_release(monkeypatch, redirect_config):
    _serve(monkeypatch, redirect_config)
    rows = M.wayback_releases()

    picked = M.pick_release(rows, "2026")
    oldest = M.pick_release(rows, "2010")

    assert picked["release"] == 7110
    assert oldest["release"] == 10
    assert 43291 not in {picked["release"], oldest["release"]}


def test_tile_url_builder_refuses_a_stale_release(monkeypatch, redirect_config):
    _serve(monkeypatch, redirect_config)
    rows = M.wayback_releases()

    assert M.wayback_tile_url(43291, rows) is None
    served = M.wayback_tile_url(7110, rows)
    assert served is not None and "/tile/7110/" in served
    assert "/tile/43291/" not in served
    assert M.wayback_tile_url(7110, [{"release": 43291, "date": "2026-10-29", "title": "t"}]) is None


def test_compare_layer_emits_only_the_resolved_release(monkeypatch, redirect_config):
    _serve(monkeypatch, redirect_config)

    layer = M.compare_layer("wayback:2026")

    assert layer["kind"] == "xyz"
    assert layer["release"] == 7110
    assert "/tile/7110/" in layer["url"]
    assert "43291" not in layer["url"]


def test_explicit_redirect_field_is_honoured(monkeypatch):
    cfg = {
        "7110": _item("2022-11-02", 7110),
        "43291": {"itemTitle": "World Imagery (Wayback 2026-10-29)", "redirectTo": 7110},
    }
    _serve(monkeypatch, cfg)

    rows = M.wayback_releases()

    assert [r["release"] for r in rows] == [7110]
    assert M.wayback_tile_url(43291, rows) is None


def test_alias_without_a_canonical_key_still_resolves_to_the_served_id(monkeypatch):
    _serve(monkeypatch, {"43291": _item("2026-10-29", 7110)})

    rows = M.wayback_releases()

    assert [r["release"] for r in rows] == [7110]
    assert M.wayback_tile_url(43291, rows) is None
    assert M.wayback_tile_url(7110, rows) is not None


def test_unreachable_catalog_fails_with_a_fix(monkeypatch):
    monkeypatch.setattr(M, "_http", lambda *a, **k: (503, ""))

    layer = M.compare_layer("wayback:2015")

    assert layer["kind"] == "error"
    assert layer["error"] == "wayback_unavailable"
    assert layer.get("fix")


def test_imagery_confirmation_refuses_an_unserved_release(monkeypatch, redirect_config):
    _serve(monkeypatch, redirect_config)

    stale = {"kind": "xyz", "label": "Esri Wayback 2026-10-29", "release": 43291,
             "url": M.WAYBACK_TILES.replace("{release}", "43291")}
    bad = M.check_imagery_layer(stale)
    assert bad["ok"] is False
    assert bad["error"] == "imagery_failed"
    assert bad.get("fix")

    served = M.compare_layer("wayback:2026")
    assert M.check_imagery_layer(served)["ok"] is True


def test_osm_side_is_not_treated_as_wayback(monkeypatch):
    monkeypatch.setattr(M, "_http", lambda *a, **k: (503, ""))

    layer = M.compare_layer("osm")

    assert layer["kind"] == "xyz"
    assert M.check_imagery_layer(layer)["ok"] is True
