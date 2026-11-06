"""Director scenes: a brief becomes shots, the pane merges them, missing Director is an error."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gev import ext_director, ext_ops, panel_inject
from gev.router import CATALOG

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node") or str(Path.home() / ".local/node24/bin/node")
needs_node = pytest.mark.skipif(not Path(NODE).exists() and not shutil.which("node"), reason="node not installed")


def _load(text: str):
    if isinstance(text, dict):
        return text
    return json.loads(text)


def test_catalog_stays_eleven_and_director_ops_are_extensions():
    assert len({spec.composable for spec in CATALOG}) == 11
    found = {(record.tool, record.op) for record in ext_ops.discover()}
    assert ("godseye_scene", "director") in found
    assert ("godseye_scene", "director_update") in found
    assert ("godseye_scene", "director_list") in found
    assert ("godseye_scene", "director_play") in found
    assert ("godseye_scene", "director_stop") in found
    html = panel_inject.prepare("<html><head></head><body></body></html>")
    assert html.index("window.__hermesDirector") < html.index("hermes-gev:run")
    agent = (ROOT / "gev" / "panel_live_agent.js").read_text()
    assert agent.index("m.name === 'hermes_director'") < agent.index("const run = await ready()")
    assert "hermes_scene_info" not in (ROOT / "gev" / "panel_director.js").read_text()


def test_brief_builds_three_shots_and_the_same_title_keeps_the_id():
    first, notes, error = ext_director.build_scene({
        "brief": "night fire approach over the basin",
        "place": "Austin", "lat": 30.27, "lon": -97.74,
    })
    assert error is None
    assert first["id"] == "hermes-night-fire-approach-over-the-basin"
    assert [shot["id"] for shot in first["shots"]] == ["approach", "overhead", "detail"]
    assert "local-firms" in first["releaseLayerIds"]
    assert notes["third_party_media"] == []
    again, _, error = ext_director.build_scene({
        "title": "night fire approach over the basin", "lat": 30.27, "lon": -97.74,
    })
    assert error is None and again["id"] == first["id"]


def test_youtube_is_not_a_director_pack_and_a_local_asset_is(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    scene, notes, error = ext_director.build_scene({
        "title": "Look",
        "lat": 1, "lon": 2,
        "brief": "see https://www.youtube.com/watch?v=abcdefghijk",
        "scene": {"media": [
            {"url": "https://youtu.be/abcdefghijk", "title": "Look", "start_sec": 12},
            {"asset": "scene/clip.mp4", "attribution": "Station", "license": "CC-BY", "title": "Local"},
        ]},
    })
    assert error is None
    assert "dataPacks" in scene and scene["dataPacks"][0]["source"]["path"] == "scene/clip.mp4"
    assert scene["dataPacks"][0]["attribution"]["license"] == "CC-BY"
    assert scene["anchors"][0]["altitudeReference"] == "ellipsoid"
    assert scene["shots"][0]["dataPackIds"] == ["pack-1"]
    urls = [clip["url"] for clip in notes["third_party_media"]]
    assert "https://www.youtube.com/watch?v=abcdefghijk" in urls
    assert "https://youtu.be/abcdefghijk" in urls
    assert "youtube" not in json.dumps(scene.get("dataPacks"))
    refused, _, error = ext_director.build_scene({
        "title": "Bad pack", "lat": 1, "lon": 2,
        "scene": {"media": [{"asset": "scene/clip.mp4"}]},
    })
    assert refused is None and "attribution" in error


def test_compose_reports_a_missing_director_and_remembers_the_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    calls = []

    def live(name, args=None, session_id=None):
        calls.append((name, args))
        return {
            "ok": False, "error": "director_unavailable", "code": "director_unavailable",
            "message": "This pane has no Director.",
            "fix": "Open a God's Eye View v0.3 pane and retry.",
        }

    monkeypatch.setattr(ext_director, "live", live)
    body = _load(ext_director.compose({"brief": "overhead of the port", "lat": 1, "lon": 2}))
    assert body["ok"] is False and body["code"] == "director_unavailable"
    assert body["id"] == "hermes-overhead-of-the-port"
    assert body["applied"] is False and len(body["shots"]) == 3
    assert calls[0][0] == "hermes_director" and calls[0][1]["action"] == "apply"
    listed = _load(ext_director.director_list({}))
    assert listed["ok"] is False
    assert listed["local"][0]["id"] == body["id"] and listed["local"][0]["applied"] is False


def test_play_stop_and_update_pass_the_pane_result(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    script = {"phase": "apply"}

    def live(name, args=None, session_id=None):
        action = (args or {}).get("action")
        if action == "apply" and script["phase"] == "apply":
            return {"ok": True, "applied": True, "scene": {"id": args["scene"]["id"]}}
        if action == "play":
            return {
                "ok": False, "error": "scene_running", "code": "scene_running",
                "message": "A Director scene is already running.",
                "fix": "Call godseye_scene op=director_stop, then play again.",
            }
        if action == "stop":
            return {"ok": True, "running": False}
        if action == "apply" and script["phase"] == "missing":
            return {
                "ok": False, "error": "scene_not_found", "code": "scene_not_found",
                "message": "No Director scene missing.",
                "fix": "Compose the scene first.",
            }
        if action == "list":
            return {"ok": True, "scenes": [{"id": "hermes-port", "title": "Port", "shots": 3}], "running": False}
        return {"ok": False, "error": "director_unavailable", "code": "director_unavailable",
                "message": "no", "fix": "open"}

    monkeypatch.setattr(ext_director, "live", live)
    made = _load(ext_director.compose({"title": "Port", "brief": "overhead of the port", "lat": 1, "lon": 2}))
    assert made["ok"] is True and made["applied"] is True
    running = _load(ext_director.director_play({"id": made["id"]}))
    assert running["ok"] is False and running["code"] == "scene_running"
    stopped = _load(ext_director.director_stop({}))
    assert stopped == {"ok": True, "op": "director_stop", "running": False}
    script["phase"] = "missing"
    missing = _load(ext_director.update({"id": "not-on-the-pane", "title": "Nope", "lat": 1, "lon": 2}))
    assert missing["ok"] is False and missing["code"] == "scene_not_found"
    listed = _load(ext_director.director_list({}))
    assert listed["ok"] is True
    assert listed["scenes"][0]["hermes"] is True


@needs_node
def test_pane_merges_a_scene_and_replaces_the_same_id():
    src = (ROOT / "gev" / "panel_director.js").read_text()
    prog = r"""
const scenes = [{ id: 'builtin', title: 'Built in', shots: [{ id: 's1', title: 'One' }] }]
const director = {
  _project: { version: 6, createdAt: '2020-01-01T00:00:00.000Z', scenes: JSON.parse(JSON.stringify(scenes)) },
  running: false,
  started: null,
  fileName: null,
  listScenes() { return this._project.scenes.map((s) => ({ id: s.id, title: s.title, shots: s.shots.length })) },
  getPlaybackStatus() { return { running: this.running, selectedSceneId: this._project.scenes[0].id } },
  findSceneByQuery(q) { return this.listScenes().find((s) => s.id === q || s.title === q) || null },
  startScene(id) { this.started = id; this.running = true },
  stopScene() { this.running = false },
  async importProjectFile(file, opts) {
    this.fileName = file && file.name
    this._project = opts.prepared.project
    return true
  },
}
global.window = { __godsEyeView: { sceneDirector: director } };
""" + src + r"""
;(async () => {
  const scene = { id: 'hermes-port', title: 'Port', shots: [{ id: 'approach', title: 'Approach', durationSec: 4, holdSec: 1, camera: { lat: 1, lon: 2, alt: 800, heading: 0, pitch: -30, roll: 0 } }] }
  const first = await window.__hermesDirector.command({ action: 'apply', mode: 'compose', scene })
  const second = await window.__hermesDirector.command({ action: 'apply', mode: 'compose', scene: { ...scene, title: 'Port again' } })
  const ids = director._project.scenes.map((s) => s.id)
  director.running = true
  const blocked = await window.__hermesDirector.command({ action: 'play', id: 'hermes-port' })
  const missing = await window.__hermesDirector.command({ action: 'apply', mode: 'update', scene: { ...scene, id: 'missing' } })
  window.__godsEyeView.sceneDirector = null
  const gone = await window.__hermesDirector.command({ action: 'list' })
  console.log(JSON.stringify({
    first: first.ok, second: second.ok, ids, title: director._project.scenes.find((s) => s.id === 'hermes-port').title,
    fileName: director.fileName, started: director.started, blocked: blocked.code, missing: missing.code, gone: gone.code,
    version: director._project.version,
  }))
})().catch((e) => { console.error(e); process.exit(1) })
"""
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    body = json.loads(out.stdout.strip().splitlines()[-1])
    assert body["first"] is True and body["second"] is True
    assert body["ids"] == ["builtin", "hermes-port"]
    assert body["title"] == "Port again"
    assert body["fileName"] == "hermes-director.json"
    assert body["started"] is None and body["blocked"] == "scene_running"
    assert body["missing"] == "scene_not_found" and body["gone"] == "director_unavailable"
    assert body["version"] == 6
