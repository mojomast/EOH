"""WebGL soft re-init: flag, scene commands, relay notices, and the headless harness."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gev import composer, errors, gl_recover, live, turn_state
from gev.scene import SceneStore

ROOT = Path(__file__).parents[1]


def test_flag_defaults_on_and_env_wins_over_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv(gl_recover.ENV, raising=False)
    assert gl_recover.enabled() is True
    cfg = tmp_path / "plugin-data" / "godseye" / "config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"glRecover": False, "token": "should-not-leak"}))
    assert gl_recover.enabled() is False
    assert "should-not-leak" not in repr(gl_recover.file_config())
    monkeypatch.setenv(gl_recover.ENV, "1")
    assert gl_recover.enabled() is True
    monkeypatch.setenv(gl_recover.ENV, "off")
    assert gl_recover.enabled() is False
    assert gl_recover.enabled(env="0", config={"gl_recover": True}) is False
    assert gl_recover.enabled(env="", config={"gl_recover": True}) is True


def test_context_lost_notice_matches_fix_it_errors():
    note = gl_recover.context_lost_notice("recovering")
    assert note["ok"] is False and note["code"] == "CONTEXT_LOST" and note["error"] == "CONTEXT_LOST"
    assert note["message"] and note["fix"] and "click" in note["fix"].lower()
    bare = errors.annotate({"ok": False, "error": "CONTEXT_LOST"})
    assert bare["code"] == "CONTEXT_LOST" and bare["fix"] and bare["message"]
    stopped = gl_recover.context_lost_notice("exhausted")
    assert "card" in stopped["message"].lower() and stopped["code"] == "CONTEXT_LOST"
    done = gl_recover.context_restored_notice({"pins": 2})
    assert done["ok"] is True and done["code"] == "CONTEXT_RESTORED" and "Pins restored: 2" in done["message"]
    assert "error" not in done and "fix" not in done


def test_restore_commands_keep_roll_pins_layers_and_drawer():
    cmds = gl_recover.fixture_commands()
    by = {}
    for name, args in cmds:
        by.setdefault(name, []).append(args)
    assert by["hermes_frame"][0]["headingDeg"] == 120
    assert by["hermes_frame"][0]["pitchDeg"] == -45
    assert by["hermes_frame"][0]["rollDeg"] == 12
    pins = by["annotate_map"][0]["annotations"]
    assert [p["label"] for p in pins] == ["Tokyo", "Port"]
    assert by["set_layer_visibility"][0] == {"layerId": "flights", "enabled": True}
    assert by["hermes_pin_drawer"][0]["label"] == "Tokyo"
    assert "clear_annotations" in by
    js = (ROOT / "gev" / "gl_recover.js").read_text()
    assert f"MAX_FAILURES = {gl_recover.MAX_FAILURES}" in js
    assert f"WINDOW_MS = {gl_recover.WINDOW_MS}" in js


def test_relay_returns_restore_commands_and_drains_notices():
    store = SceneStore(None)
    store.record("chat", "fly_to_location", {"latitude": 1, "longitude": 2, "rangeM": 500},
                 {"ok": True, "result": {"camera": {
                     "latitude": 1, "longitude": 2, "heightM": 500,
                     "pitchDeg": -15, "headingDeg": 90, "rollDeg": 8}}})
    store.record("chat", "annotate_map", {"annotations": [
        {"type": "pin", "label": "A", "latitude": 1, "longitude": 2}]},
                 {"ok": True, "result": {"items": [{"ok": True, "latitude": 1, "longitude": 2}]}})
    relay = live.Relay(store)
    relay.next(wait=0, pane_id="P", sessions=["chat"])
    restored = relay.gl_event({"pane": "P", "event": "restore", "sessions": ["chat"]})
    assert restored["ok"] is True and restored["scene"] == "chat"
    frame = next(args for name, args in restored["commands"] if name == "hermes_frame")
    assert frame["rollDeg"] == 8 and frame["headingDeg"] == 90
    lost = relay.gl_event({"pane": "P", "event": "lost", "kind": "recovering", "sessions": ["chat"]})
    assert lost["code"] == "CONTEXT_LOST" and lost["fix"]
    ok = relay.gl_event({"pane": "P", "event": "recovered", "detail": {"pins": 1}, "sessions": ["chat"]})
    assert ok["code"] == "CONTEXT_RESTORED" and ok["ok"] is True
    assert relay.gl_event({"pane": "missing", "event": "nope"})["ok"] is False


def test_turn_sees_context_lost_once(monkeypatch):
    relay = live.Relay()
    relay.next(wait=0, pane_id="P", sessions=["chat"])
    relay.gl_event({"pane": "P", "event": "lost", "kind": "recovering", "sessions": ["chat"]})
    relay.gl_event({"pane": "P", "event": "recovered", "detail": {"pins": 2}, "sessions": ["chat"]})
    monkeypatch.setattr(turn_state, "fetch_line", lambda sid: None)
    monkeypatch.setattr("gev.watch.store", lambda: type("S", (), {"drain_alerts": lambda self, sid: []})())
    monkeypatch.setattr(live, "current", lambda: relay)
    got = turn_state.pre_llm_call(session_id="chat")
    assert got and '"code":"CONTEXT_LOST"' in got["context"] and '"fix":' in got["context"]
    assert '"code":"CONTEXT_RESTORED"' in got["context"]
    again = turn_state.pre_llm_call(session_id="chat")
    assert again is None or "CONTEXT_LOST" not in again.get("context", "")


def test_doctor_reports_the_recovery_switch(monkeypatch, tmp_path):
    from gev import config
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv(gl_recover.ENV, raising=False)
    monkeypatch.setattr(config, "node_bin", lambda: None)
    monkeypatch.setattr(config, "checkout", lambda: None)
    monkeypatch.setattr(config, "api_base", lambda: "http://127.0.0.1:9")
    monkeypatch.setattr(composer, "relay_status", lambda: {"paneOpen": False, "panes": []})
    monkeypatch.setattr(composer, "panel_checks", lambda **kw: [])
    monkeypatch.setattr(composer, "provider_key_checks", lambda *a, **k: [])
    monkeypatch.setattr(composer, "service_started_at", lambda: None)
    monkeypatch.setattr(composer, "scene_op", lambda *a, **k: {})
    on = {c["check"]: c for c in composer.doctor()["checks"]}["webgl recover"]
    assert on["ok"] and on["detail"].startswith("on;")
    monkeypatch.setenv(gl_recover.ENV, "0")
    off = {c["check"]: c for c in composer.doctor()["checks"]}["webgl recover"]
    assert off["ok"] and off["detail"].startswith("off")


def test_panel_injects_recovery_and_the_host_keeps_the_card():
    from gev import panel_inject
    html = panel_inject.prepare("<html><head></head><body></body></html>", gl_recover=True)
    assert "window.__HERMES_GL_RECOVER = true" in html
    assert html.index("MAX_FAILURES") < html.index("hermes-gev:run")
    off = panel_inject.prepare("<head></head>", gl_recover=False)
    assert "window.__HERMES_GL_RECOVER = false" in off
    plugin = (ROOT / "desktop" / "plugin.js").read_text()
    assert "hermes-gev:gl-recovering" in plugin and "hermes-gev:gl-need-scene" in plugin
    assert "Click to reload the globe" in plugin
    agent = (ROOT / "gev" / "panel_live_agent.js").read_text()
    assert agent.index("releaseContext") < agent.index("destroyViewer")
    assert "recreateViewer" in agent and "hermes-gev:gl-recovered" in agent


def test_gl_recover_js_unit():
    proc = subprocess.run(
        ["node", "--test", "gev/gl_recover.test.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for the WebGL harness")
def test_gl_recover_harness_accepts_chromium_when_chrome_is_missing():
    script = r"""
import { resolveChrome } from './scripts/gl_recover_harness.mjs'
const exists = (p) => p === '/usr/bin/chromium'
const lookup = (name) => name === 'chromium-browser' ? '/usr/bin/chromium-browser' : ''
const picked = resolveChrome({}, exists, lookup)
if (picked !== '/usr/bin/chromium') {
  console.error('picked ' + picked)
  process.exit(1)
}
const fromEnv = resolveChrome({ CHROME_PATH: '/opt/chrome' }, () => false, () => '')
if (fromEnv !== '/opt/chrome') process.exit(2)
const fromPath = resolveChrome({}, () => false, (name) => name === 'chromium' ? '/opt/bin/chromium' : '')
if (fromPath !== '/opt/bin/chromium') process.exit(3)
console.log('ok')
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert proc.stdout.strip() == "ok"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for the WebGL harness")
def test_gl_recover_harness_forces_lose_context():
    proc = subprocess.run(
        ["node", "scripts/gl_recover_harness.mjs"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["oldLost"] is True and report["newLost"] is False
    assert report["camera"]["headingDeg"] == 120 and report["camera"]["rollDeg"] == 12
    assert [p["label"] for p in report["pins"]] == ["Tokyo", "Port"]
