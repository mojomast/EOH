"""Serve-time config.json and the fork embed messages. Values never come back out."""
import json
import os
import stat

from gev import composer, panel_inject, runtime_config


def test_perf_switch(monkeypatch):
    assert runtime_config.perf_mode("embed") == "embed"
    for raw in ("0", "off", "no", "false", "default", ""):
        assert runtime_config.perf_mode(raw) is None
    monkeypatch.setenv("GODSEYE_PERF", "off")
    assert runtime_config.perf_mode() is None
    monkeypatch.setenv("GODSEYE_PERF", "embed")
    assert runtime_config.perf_mode() == "embed"


def test_overlay_omits_empty_and_can_disable_photon():
    out = runtime_config.overlay_from_env({
        "CESIUM_ION_TOKEN": "  ion-token-value  ",
        "GOOGLE_MAPS_API_KEY": "",
        "MAPILLARY_CLIENT_TOKEN": "map-token",
        "PHOTON_URL": "off",
        "OTHER": "nope",
    })
    assert out == {"cesiumIonToken": "ion-token-value", "mapillaryToken": "map-token", "photonUrl": ""}
    assert "googleMapsApiKey" not in out and "OTHER" not in out


def test_write_is_private_and_returns_names_not_values(tmp_path):
    root = tmp_path / "gev"
    (root / "dist" / "panel").mkdir(parents=True)
    (root / "public").mkdir()
    (root / "index.html").write_text("<html></html>")
    token = "ion-secret-value-not-for-logs"
    result = runtime_config.write_files(root, {
        "CESIUM_ION_TOKEN": token,
        "GOOGLE_MAPS_API_KEY": "AIza-secret-value",
        "PHOTON_URL": "https://photon.example/api",
    })
    assert result["ok"] is True
    assert result["fields"] == ["cesiumIonToken", "googleMapsApiKey", "photonUrl"]
    assert token not in json.dumps(result)
    for rel in ("dist/panel/config.json", "public/config.json"):
        path = root / rel
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        data = json.loads(path.read_text())
        assert data["cesiumIonToken"] == token
        assert "perf" not in data
    assert runtime_config.scrub("token " + token + " tail", {"CESIUM_ION_TOKEN": token}) == "token *** tail"


def test_config_message_shape():
    msg = runtime_config.config_message(
        {"cesiumIonToken": "ion-secret-value", "photonUrl": "", "extra": "no"},
        "embed",
    )
    assert msg["type"] == "gev:config"
    assert msg["cesiumIonToken"] == "ion-secret-value" and msg["photonUrl"] == ""
    assert msg["perf"] == "embed"
    assert msg["config"]["cesiumIonToken"] == "ion-secret-value"
    assert "extra" not in msg and "extra" not in msg["config"]
    bare = runtime_config.config_message({}, None)
    assert bare == {"type": "gev:config"}
    assert len(runtime_config.revision({"cesiumIonToken": "a"}, "embed")) == 16


def test_panel_checks_runtime_config_satisfies_key_without_a_rebuild(tmp_path):
    root = tmp_path / "gev"
    (root / "dist" / "panel" / "assets").mkdir(parents=True)
    bundle = root / "dist" / "panel" / "assets" / "index-x.js"
    bundle.write_text("gev:config GEV_RUNTIME_CONFIG waitForConfig")
    envf = tmp_path / "e.env"
    token = "ion-secret-value-not-for-logs"
    envf.write_text(f"CESIUM_ION_TOKEN={token}\n")
    # Env is newer than the bundle, and the token is not baked. config.json matches.
    os.utime(bundle, (1000, 1000))
    runtime_config.write_files(root, {"CESIUM_ION_TOKEN": token})
    out = composer.panel_checks(root, envf)
    codes = {c.get("code") for c in out}
    assert "KEY_MISSING" not in codes and "PANEL_STALE" not in codes
    assert token not in repr(out)
    fresh = next(c for c in out if c["check"] == "key CESIUM_ION_TOKEN")
    assert fresh["ok"] is True and "config.json" in fresh["detail"]


def test_panel_checks_runtime_missing_file_names_config_json(tmp_path):
    root = tmp_path / "gev"
    (root / "dist" / "panel" / "assets").mkdir(parents=True)
    bundle = root / "dist" / "panel" / "assets" / "index-x.js"
    bundle.write_text("waitForConfig")
    envf = tmp_path / "e.env"
    token = "ion-secret-value-not-for-logs"
    envf.write_text(f"CESIUM_ION_TOKEN={token}\n")
    out = composer.panel_checks(root, envf)
    codes = {c.get("code") for c in out}
    assert "KEY_MISSING" in codes and "PANEL_STALE" in codes
    blob = repr(out)
    assert token not in blob
    assert "config.json" in blob
    assert "op=rebuild" in blob


def test_prepare_sets_perf_and_does_not_live_in_the_desktop_bundle():
    token = "ion-secret-value-not-for-logs"
    html = panel_inject.prepare("<html><head></head><body></body></html>", perf="embed",
                                 config={"cesiumIonToken": token + "<script>alert(1)</script>", "note": "drop-me"})
    assert "waitForConfig=1" in html and "GEV_RUNTIME_CONFIG" in html and token in html
    assert "\\u003cscript>alert" in html and "<script>alert" not in html
    assert "drop-me" not in html
    assert "'gev:config'" in html and "'gev:visibility'" in html
    plugin = (panel_inject._HERE.parent / "desktop" / "plugin.js").read_text()
    assert token not in plugin
    off = panel_inject.prepare("<head></head>", perf=None, config=None)
    assert "var wait = false" in off and "var perf = null" in off
