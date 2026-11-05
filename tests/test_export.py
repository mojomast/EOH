"""godseye_investigation_export: GeoJSON (RFC 7946, lon/lat), KML, and the optional pack."""
from __future__ import annotations

import base64
import json
import zipfile
from pathlib import Path

import pytest

from gev import composer, geoexport, hermes_tools, investigations
from gev.scene import SceneStore

ROOT = Path(__file__).parents[1]
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
TILE = "https://tile.googleapis.com/v1/3dtiles/root.json"


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _scene(home: Path) -> SceneStore:
    st = SceneStore(home / "plugin-data" / "godseye" / "scenes.json")
    st.record("s", "annotate_map", {"annotations": [
        {"type": "pin", "latitude": 45.5088, "longitude": -73.554, "label": "Sansotei",
         "title": "Sansotei Ramen",
         "description_md": "Tonkotsu.\n\nSee " + TILE,
         "links": [{"label": "Menu", "url": "https://sansotei.example/menu"},
                   {"label": "Tiles", "url": TILE}],
         "images": [{"url": "https://upload.wikimedia.org/wikipedia/commons/a.jpg", "alt": "bowl"},
                    {"url": TILE, "alt": "tiles"},
                    {"url": "data:image/png;base64,aaaa", "alt": "inline"}],
         "cuisine": "ramen", "amenity": "restaurant", "opening_hours": "Mo-Su 11:30-22:00",
         "website": "https://sansotei.example", "osm": "node/101", "api_key": "should-not-export",
         "tags": {"amenity": "restaurant", "cuisine": "ramen"}},
        {"type": "route", "label": "Walk", "points": [
            {"latitude": 45.5017, "longitude": -73.5673},
            {"latitude": 45.5088, "longitude": -73.5540}]},
        {"type": "line", "label": "Trace", "points": [[45.50, -73.57], [45.51, -73.56]]},
    ]}, {"ok": True, "result": {"items": [
        {"ok": True, "id": "anno-1", "latitude": 45.5088, "longitude": -73.554},
        {"ok": True, "id": "anno-2"},
        {"ok": True, "id": "anno-3"},
    ]}})
    st.record("s", "hermes_draw", {"action": "add", "label": "Old Port", "color": "#ffe45a"},
              {"ok": True, "result": {"shape": {"id": "shape-1", "kind": "polygon", "points": [
                  {"lat": 45.50, "lon": -73.56}, {"lat": 45.51, "lon": -73.56},
                  {"lat": 45.51, "lon": -73.55}, {"lat": 45.50, "lon": -73.55}]}}})
    st.record("s", "hermes_pin_drawer", {"action": "open"},
              {"ok": True, "result": {"open": True, "pin": {"latitude": 45.5088, "longitude": -73.554,
                                                            "label": "Sansotei"}}})
    return st


def _by_role(fc, role):
    return [f for f in fc["features"] if f["properties"]["role"] == role]


def test_photoreal_urls_are_recognized():
    assert geoexport.is_photoreal_imagery(TILE)
    assert geoexport.is_photoreal_imagery("https://khms0.google.com/kh/v=946")
    assert geoexport.is_photoreal_imagery("https://mt1.google.com/vt/lyrs=s&x=1&y=2&z=3")
    assert not geoexport.is_photoreal_imagery("https://sansotei.example/menu")
    assert not geoexport.is_photoreal_imagery("https://upload.wikimedia.org/wikipedia/commons/a.jpg")
    assert not geoexport.is_photoreal_imagery("https://earthquake.usgs.gov/earthquakes/eventpage/usdemo0001")
    assert not geoexport.is_photoreal_imagery("https://www.google.com/maps/place/Montreal")


def test_clockwise_ring_is_rewound_and_validator_rejects_swapped_axes():
    data = {"name": "Ring", "annotations": [{"id": "p", "spec": {
        "type": "polygon", "label": "Clockwise",
        "points": [[45.50, -73.56], [45.51, -73.56], [45.51, -73.55], [45.50, -73.55]]}}]}
    fc, counts = geoexport.build_feature_collection(data)
    assert counts["polygons"] == 1 and not geoexport.validate_geojson(fc)
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert geoexport._signed_area(ring) > 0 and ring[0] == ring[-1]
    assert ring[0][0] < -70 and ring[0][1] > 40  # lon, then lat

    swapped = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"latitude": 45.5, "longitude": -73.5},
        "geometry": {"type": "Point", "coordinates": [45.5, -73.5]}}]}
    assert any("longitude, latitude" in e for e in geoexport.validate_geojson(swapped))
    assert any("crs" in e for e in geoexport.validate_geojson({**fc, "crs": {"type": "name"}}))
    open_ring = json.loads(json.dumps(fc))
    open_ring["features"][0]["geometry"]["coordinates"][0] = open_ring["features"][0]["geometry"]["coordinates"][0][:-1]
    assert any("closed" in e for e in geoexport.validate_geojson(open_ring))
    assert any("latitude" in e for e in geoexport.validate_geojson(
        {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {},
            "geometry": {"type": "Point", "coordinates": [-73.5, 95]}}]}))


def test_saved_investigation_exports_user_data_only(hermes_home):
    st = _scene(hermes_home)
    saved = investigations.save(st, "s", "Montreal Ramen", notes="ramen walk")
    assert "shapes" in json.loads(Path(saved["path"]).read_text())
    out = geoexport.export_investigation("Montreal Ramen", fmt="both")
    assert out["ok"] and out["counts"]["points"] == 1 and out["counts"]["lines"] == 2 and out["counts"]["polygons"] == 1
    fc = json.loads(Path(out["geojson"]).read_text())
    kml = Path(out["kml"]).read_text()
    assert not geoexport.validate_geojson(fc) and not geoexport.validate_kml(kml)
    assert fc["bbox"][0] < -70 and 40 < fc["bbox"][1] < 50  # [west, south, east, north]
    pin = _by_role(fc, "pin")[0]
    assert pin["geometry"]["coordinates"] == [-73.554, 45.5088]
    assert pin["properties"]["latitude"] == 45.5088 and pin["properties"]["longitude"] == -73.554
    drawer = pin["properties"]["drawer"]
    assert drawer["open"] is True and "Tonkotsu" in drawer["description_md"]
    assert drawer["links"] == [{"label": "Menu", "url": "https://sansotei.example/menu"}]
    assert drawer["images"] == [{"url": "https://upload.wikimedia.org/wikipedia/commons/a.jpg", "alt": "bowl"}]
    assert pin["properties"]["enrichment"]["cuisine"] == "ramen"
    assert pin["properties"]["enrichment"]["opening_hours"] == "Mo-Su 11:30-22:00"
    blob = json.dumps(fc) + kml
    assert TILE not in blob and "should-not-export" not in blob and "data:image" not in blob
    assert "paneId" not in blob and "scene_key" not in blob
    assert "-73.554000,45.508800,0" in kml
    assert _by_role(fc, "route")[0]["geometry"]["coordinates"][0] == [-73.5673, 45.5017]
    assert _by_role(fc, "area")[0]["properties"]["color"] == "#ffe45a"
    assert out["counts"]["photoreal_omitted"] >= 1
    assert "Google photoreal" in out["summary"] and len(out["summary"]) < 500


def test_current_scene_and_load_restore_shapes(hermes_home):
    st = _scene(hermes_home)
    out = geoexport.export_investigation(scene_store=st, session_key="s", fmt="kml")
    assert out["ok"] and "geojson" not in out and out["counts"]["polygons"] == 1
    investigations.save(st, "s", "Montreal Ramen")
    st2 = SceneStore(hermes_home / "other.json")
    investigations.load(st2, "other", "Montreal Ramen", restore=False)
    assert "shape-1" in (st2.scene("other").get("shapes") or {})


def test_pack_includes_report_and_skips_photoreal_screenshots(hermes_home):
    st = _scene(hermes_home)
    investigations.save(st, "s", "Montreal Ramen", notes="notes")
    shots = hermes_home / "plugin-data" / "godseye" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "montreal-ramen-map.png").write_bytes(PNG)
    (shots / "montreal-ramen-photoreal.png").write_bytes(PNG)
    outside = hermes_home / "secret.png"
    outside.write_bytes(PNG)
    data = json.loads((hermes_home / "plugin-data" / "godseye" / "investigations" / "montreal-ramen.json").read_text())
    data["screenshots"] = [str(shots / "montreal-ramen-map.png"), str(outside), "../secret.png"]
    out = geoexport.export_data(data, fmt="pack")
    assert out["ok"] and out["screenshots"] == [str((shots / "montreal-ramen-map.png").resolve())]
    with zipfile.ZipFile(out["pack"]) as zf:
        names = zf.namelist()
        geo = zf.read([n for n in names if n.endswith(".geojson")][0]).decode()
        md = zf.read([n for n in names if n.endswith(".md")][0]).decode()
    assert any(n.endswith(".kml") for n in names)
    assert "screenshots/montreal-ramen-map.png" in names
    assert not any("photoreal" in n or n.endswith("secret.png") for n in names)
    assert TILE not in geo and TILE not in md and "Tonkotsu" in md
    # A photoreal stack drops every globe screenshot, including ones with plain names.
    data["meta"] = {"map_stack": "photoreal"}
    blocked = geoexport.export_data(data, fmt="pack")
    assert blocked["ok"] and blocked.get("screenshots", []) == []
    assert blocked["screenshots_omitted"] >= 1
    with zipfile.ZipFile(blocked["pack"]) as zf:
        assert not any(n.startswith("screenshots/") for n in zf.namelist())


def test_tool_handler_missing_name_and_slash(hermes_home, monkeypatch):
    st = _scene(hermes_home)
    monkeypatch.setattr(composer, "_relay_scenes", lambda: st)
    raw = hermes_tools.extra_handler("godseye_investigation_export")(
        {"format": "geojson"}, session_id="s")
    out = json.loads(raw)
    assert out["ok"] and out["name"] == "Current scene" and Path(out["geojson"]).is_file()
    missing = json.loads(hermes_tools.extra_handler("godseye_investigation_export")(
        {"name": "Nope"}, session_id="s"))
    assert missing["ok"] is False and missing["code"] == "not_found" and missing["fix"]
    bad = geoexport.export_investigation(fmt="shp", data={"name": "X", "annotations": []})
    assert bad["error"] == "bad_args"
    investigations.save(st, "s", "Montreal Ramen")
    msg = composer.gev_command("export Montreal Ramen pack")
    assert "Montreal Ramen" in msg and ".zip" in msg
    assert "export" in composer.HELP
    assert "op=export" in (ROOT / "README.md").read_text()
    assert "godseye_investigation_export" in (ROOT / "CHANGELOG.md").read_text()
    assert "op=export" in (ROOT / "skills/godseye-analyst/SKILL.md").read_text()


def test_export_does_not_fetch_imagery(hermes_home, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("export must not fetch imagery")
    monkeypatch.setattr("urllib.request.urlopen", boom)
    st = _scene(hermes_home)
    out = geoexport.export_investigation(scene_store=st, session_key="s", fmt="both")
    assert out["ok"] and TILE not in Path(out["geojson"]).read_text()
