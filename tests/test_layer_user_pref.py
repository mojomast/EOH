"""User-disabled layers stay off when the scene is restored or the composer shows a view."""
from __future__ import annotations

from gev import composer, home, live
from gev.scene import SceneStore, default_path


def _store(tmp_path):
    st = SceneStore(tmp_path / "scene.json")
    st.record("s1", "set_layer_visibility", {"layerId": "flights", "enabled": True}, {"ok": True})
    st.record("s1", "set_layer_visibility", {"layerId": "earthquakes", "enabled": True}, {"ok": True})
    st.record("s1", "set_layer_visibility", {"layerId": "flights", "enabled": False}, {"ok": True})
    return st


def _enabled_layers(commands):
    return [args["layerId"] for name, args in commands if name == "set_layer_visibility"]


def test_scene_restore_keeps_disabled_layer_off(tmp_path):
    st = _store(tmp_path)
    assert st.disabled_layers("s1") == ["flights"]
    assert st.list("s1")["disabled_layers"] == ["flights"]
    # A new store reads the preference back from disk.
    restored = SceneStore(tmp_path / "scene.json").restore_commands("s1")
    assert _enabled_layers(restored) == ["earthquakes"]
    assert ("set_layer_visibility", {"layerId": "flights", "enabled": True}) not in restored


def test_restore_only_enables_a_disabled_layer_when_explicitly_requested(tmp_path):
    st = _store(tmp_path)
    requested = st.restore_commands("s1", requested_layers=["flights"])
    assert _enabled_layers(requested) == ["earthquakes", "flights"]
    assert st.disabled_layers("s1") == ["flights"]  # reading a restore never changes the preference


def test_view_state_layer_off_is_stored_as_a_user_preference(tmp_path):
    st = SceneStore(tmp_path / "scene.json")
    st.record("s", "get_current_view_state", {}, {"ok": True, "result": {
        "camera": {"latitude": 35.6, "longitude": 139.7, "heightM": 20000},
        "layers": [{"id": "flights", "enabled": False}, {"id": "earthquakes", "enabled": True}]}})
    assert st.disabled_layers("s") == ["flights"]
    assert _enabled_layers(st.restore_commands("s")) == ["earthquakes"]


class _FakeLive:
    def __init__(self):
        self.calls = []

    def __call__(self, name, args=None, session_id=None, **_kw):
        self.calls.append((name, args, session_id))
        return {"ok": True, "pane": "P", "result": {}}


def _brief():
    return {"content": [{"type": "text", "text": "Situation around Tokyo."}],
            "structuredContent": {"view": {
                "camera": {"lat": 35.68, "lon": 139.69, "altitude_m": 180000},
                "layers": ["flights", "earthquakes"]},
                "sections": {}}}


def _seed_default_scene(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(live, "_relay", None)
    st = SceneStore(default_path())
    st.record("s1", "set_layer_visibility", {"layerId": "flights", "enabled": False}, {"ok": True})
    return st


def test_brief_and_show_does_not_re_enable_a_disabled_layer(tmp_path, monkeypatch):
    _seed_default_scene(monkeypatch, tmp_path)
    fake = _FakeLive()
    out = composer.brief_and_show({"place": "Tokyo", "top_n": 1}, session_id="s1",
                                  live=fake, mcp=lambda _name, _args: _brief())
    assert out["shown"]["globe_open"] is True
    assert _enabled_layers([(name, args) for name, args, _sid in fake.calls]) == ["earthquakes"]
    assert out["shown"]["layers"] == ["earthquakes"]


def test_go_home_does_not_re_enable_a_disabled_layer(tmp_path, monkeypatch):
    _seed_default_scene(monkeypatch, tmp_path)
    home.bind(None)
    try:
        home.set({"lat": 20, "lon": 0, "altitude_m": 15000000,
                  "layers": ["flights", "earthquakes"], "style": "normal"})
        fake = _FakeLive()
        out = composer.go_home(session_id="s1", live=fake)
        assert out["ok"] is True
        assert _enabled_layers([(name, args) for name, args, _sid in fake.calls]) == ["earthquakes"]
    finally:
        home.bind(None)
