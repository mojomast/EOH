import os, time
import pytest
from gev import composer, tour
from gev.scene import SceneStore


def test_scene_record_list_delete_restore(tmp_path):
    st = SceneStore(tmp_path / "s.json")
    st.record("s1", "annotate_map", {"annotations": [{"type": "pin", "target": "Kazu"}, {"type": "route", "points": [[1, 2], [3, 4]]}]},
              {"ok": True, "result": {"items": [{"ok": True, "latitude": 45.5, "longitude": -73.5}, {"ok": True}]}})
    st.record("s1", "get_current_view_state", {}, {"ok": True, "result": {"camera": {
        "latitude": 45.4, "longitude": -73.6, "heightM": 900, "pitchDeg": -35, "headingDeg": 10,
        "targetLat": 45.51, "targetLon": -73.57, "rangeM": 9000}}})
    st.record("s1", "hermes_caption", {"text": "hi"}, {"ok": True})
    ls = st.list("s1")
    assert len(ls["annotations"]) == 2 and ls["annotations"][0]["latitude"] == 45.5
    assert ls["camera"]["lat"] == 45.51 and ls["camera"]["heightM"] == 9000
    cmds = st.restore_commands("s1")
    names = [c[0] for c in cmds]
    assert names == ["fly_to_location", "hermes_frame", "annotate_map", "hermes_caption"]
    assert "target" not in cmds[2][1]["annotations"][0]  # never re-geocodes
    assert cmds[1][1]["pitchDeg"] == -35
    # persisted + pick
    st2 = SceneStore(tmp_path / "s.json")
    assert st2.pick(["nope", "s1"]) == "s1"
    assert st2.delete("s1", ls["annotations"][0]["id"]) and not st2.delete("s1", "zzz")
    assert [c[0] for c in st2.restore_commands("s1", redraw_only=True)] == ["clear_annotations", "annotate_map"]


def test_scene_ignores_failures(tmp_path):
    st = SceneStore(tmp_path / "s.json")
    st.record("k", "annotate_map", {"annotations": [{"type": "pin"}]}, {"ok": False})
    assert st.list("k")["annotations"] == []


def test_tour_plan_validation():
    with pytest.raises(ValueError):
        tour.plan({})
    with pytest.raises(ValueError):
        tour.plan({"stops": [{"lat": 1, "lon": 2}], "framing": "space"})
    p = tour.plan({"stops": [{"lat": 1, "lon": 2}], "orbit": True})
    assert p["orbit"] == "slow" and p["rangeM"] == tour.FRAMING["street"][0]


def test_tour_runs_all_stops_frames_and_stops():
    calls = []
    def run(name, args):
        calls.append((name, args))
        return {"ok": True, "result": {"camera": {"pitchDeg": -15}}}
    sp = tour.plan({"stops": [{"lat": 1, "lon": 2, "name": "a"}, {"lat": 3, "lon": 4, "name": "b"}],
                    "dwellMs": 0, "orbit": "slow", "framing": "street"})
    t = tour.Tour(sp, run, sleep=lambda s: False)
    t()
    st = t.status()
    assert st["state"] == "done" and st["errors"] == [] and len(st["visited"]) == 2
    frames = [a for n, a in calls if n == "hermes_frame"]
    assert len(frames) == 2 and all(f["pitchDeg"] == -15 for f in frames)
    assert calls[-1][0] == "move_camera" and calls[-1][1].get("motion") == "stop"


def _setup(tmp_path, bundle_text, env_text):
    root = tmp_path / "gev"; (root / "dist/panel/assets").mkdir(parents=True)
    b = root / "dist/panel/assets/index-x.js"; b.write_text(bundle_text)
    envf = tmp_path / "e.env"; envf.write_text(env_text)
    return root, b, envf


def test_panel_checks_missing_and_stale(tmp_path):
    root, b, envf = _setup(tmp_path, "nothing", "")
    os.utime(b, (time.time() - 100, time.time() - 100))
    codes = {c.get("code") for c in composer.panel_checks(root, envf)}
    assert "KEY_MISSING" in codes and "PANEL_STALE" in codes


def test_panel_checks_scopes_never_leak_value(tmp_path):
    import base64, json
    pl = base64.urlsafe_b64encode(json.dumps({"scopes": ["assets:read", "assets:write"]}).encode()).decode().rstrip("=")
    tok = f"eyJhbGciOiJIUzI1NiJ9.{pl}.sig"
    root, b, envf = _setup(tmp_path, f"x{tok}x", f"CESIUM_ION_TOKEN={tok}\n")
    os.utime(envf, (time.time() - 100, time.time() - 100))
    out = composer.panel_checks(root, envf)
    codes = {c.get("code") for c in out}
    assert "ION_SCOPES" in codes and "PANEL_STALE" not in codes
    assert tok not in repr(out)


def test_map_stack_remediation():
    out = composer.remediate_map_stack({"ok": True, "result": {"ok": False, "error": "Cesium ion token missing"}})
    assert "godseye_rebuild_panel" in out["remediation"]
    assert "remediation" not in composer.remediate_map_stack({"ok": True, "result": {"ok": True}})
