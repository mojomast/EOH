"""Stream C: doctor provider keys + feed states, pane prune/label/binding, preview swap, captions."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from gev import composer, live, preview


def test_caption_for_includes_source_and_queried_extent_not_worldwide():
    data = {"sections": {
        "aircraft": {"data": {"total": 40, "rows": []}},
        "weather": {"data": {"weather": {"conditions": "clear", "temperature_c": 14.6}}},
        "fires": {"unavailable": True},
    }}
    cap = composer.caption_for("Tokyo", data)
    assert "40 aircraft" in cap and "queried extent" in cap
    assert "OpenSky" in cap or "adsb" in cap
    assert "worldwide" not in cap.lower()
    assert composer.caption_scope({"count_scope": "in view"}) == "in view"
    assert composer.caption_scope({"countScope": "worldwide"}) == "queried extent"


def test_brief_caption_scope_in_brief_and_show():
    data = {"view": {"camera": {"lat": 35.68, "lon": 139.69, "altitude_m": 181818}, "layers": ["flights"]},
            "sections": {"aircraft": {"data": {"total": 40, "rows": [
                {"callsign": "JAL1", "lat": 35.5, "lon": 139.8, "distance_km": 9}]}}}}
    brief = {"content": [{"type": "text", "text": "Situation around Tokyo: 40 aircraft."}],
             "structuredContent": data}

    class Live:
        def __call__(self, name, args=None, session_id=None, **_kw):
            return {"ok": True, "pane": "P", "result": {}}

    out = composer.brief_and_show({"place": "Tokyo", "top_n": 2}, session_id="s1", live=Live(),
                                  mcp=lambda n, a: brief)
    assert "queried extent" in out["shown"]["caption"]
    assert "worldwide" not in out["shown"]["caption"].lower()


def test_provider_key_checks_never_leak_values(tmp_path, monkeypatch):
    envf = tmp_path / "e.env"
    envf.write_text("AISSTREAM_API_KEY=super-secret-ais-key-value\nFIRMS_MAP_KEY=firms-secret-zzz\n")
    monkeypatch.setattr(composer, "env_file", lambda: envf)
    out = composer.provider_key_checks(envf)
    by = {c["check"]: c for c in out}
    assert by["key AISSTREAM_API_KEY"]["ok"] and by["key FIRMS_MAP_KEY"]["ok"]
    blob = repr(out)
    assert "super-secret" not in blob and "firms-secret" not in blob


def test_provider_key_checks_missing(tmp_path, monkeypatch):
    envf = tmp_path / "empty.env"
    envf.write_text("")
    monkeypatch.setattr(composer, "env_file", lambda: envf)
    for k in ("AISSTREAM_API_KEY", "FIRMS_API_KEY", "FIRMS_MAP_KEY", "FIRMS_KEY"):
        monkeypatch.delenv(k, raising=False)
    out = composer.provider_key_checks(envf)
    assert all(not c["ok"] and c.get("code") == "KEY_MISSING" for c in out)


def test_feed_state_checks_nominal_fallback_unavailable():
    ok = composer.feed_state_checks({"feedProvenance": {
        "overall": "nominal",
        "layers": [{"id": "flights", "name": "Flights", "feedState": "nominal"}],
    }})
    assert ok[0]["ok"] and "nominal" in ok[0]["detail"]
    bad = composer.feed_state_checks({"feedProvenance": {
        "overall": "unavailable",
        "layers": [{"id": "local-firms", "name": "Fires", "feedState": "unavailable"},
                   {"id": "flights", "name": "Flights", "feedState": "fallback"}],
    }})
    assert not bad[0]["ok"] and bad[0]["code"] == "FEED_UNAVAILABLE"
    warn = composer.feed_state_checks({"feedProvenance": {
        "overall": "fallback",
        "layers": [{"id": "ais-live-vessels", "feedState": "fallback"}],
    }})
    assert warn[0]["ok"] and warn[0].get("code") == "FEED_DEGRADED"


def test_pane_prune_removes_stale_keeps_open(monkeypatch):
    monkeypatch.setattr(live, "PANE_STALE_S", 0.05)
    monkeypatch.setattr(live, "INFLIGHT_STALE_S", 0.05)
    r = live.Relay()
    r.next(wait=0, pane_id="alive", sessions=["s-alive"])
    r.next(wait=0, pane_id="ghost", sessions=["s-ghost"])
    # Make ghost stale: rewind last_poll
    r.panes["ghost"].last_poll = time.monotonic() - 10
    r.panes["ghost"].polling = 0
    out = r.prune()
    assert "ghost" in out["removed"] and "alive" not in out["removed"]
    assert "ghost" not in r.panes and "alive" in r.panes
    assert "s-ghost" not in r.session_binding


def test_pane_one_per_session_and_stable_binding():
    r = live.Relay()
    r.next(wait=0, pane_id="p1", sessions=["chat-1"], label="Montreal")
    assert r.panes["p1"].label == "Montreal"
    assert r.session_binding["chat-1"] == "p1"
    # A second pane claiming the same session steals the binding (one pane per session).
    r.next(wait=0, pane_id="p2", sessions=["chat-1"], label="Montreal B")
    assert "chat-1" not in r.panes["p1"].sessions
    assert r.session_binding["chat-1"] == "p2"
    assert r.targets("chat-1")[0].id == "p2"
    listed = {p["id"]: p for p in r.list()}
    assert listed["p2"]["label"] == "Montreal B"


def test_panes_op_prune_and_label():
    r = live.Relay()
    r.next(wait=0, pane_id="P", sessions=["s"], label=None)
    out = r.panes_op({"label": "Briefing", "session_id": "s"})
    assert out["ok"] and out["label"]["pane"]["label"] == "Briefing"
    r.panes["P"].last_poll = time.monotonic() - 999
    r.panes["P"].polling = 0
    # Inflight stale window: force age past both thresholds
    import gev.live as L
    old_ps, old_inf = L.PANE_STALE_S, L.INFLIGHT_STALE_S
    L.PANE_STALE_S = 0.01
    L.INFLIGHT_STALE_S = 0.01
    try:
        r.panes["P"].last_poll = time.monotonic() - 10
        pruned = r.panes_op({"prune": True})
        assert pruned["ok"] and "P" in pruned["removed"]
    finally:
        L.PANE_STALE_S, L.INFLIGHT_STALE_S = old_ps, old_inf


def test_preview_swap_tracks_and_closes_previous():
    preview.clear()
    closed, opened = [], []

    def closer(url):
        closed.append(url)
        return {"ok": True}

    def opener(url, label):
        opened.append((url, label))
        return {"ok": True}

    out = preview.swap("https://example.test/a", label="A", closer=closer, opener=opener)
    assert out["ok"] and out["closed"] is None and opened == [("https://example.test/a", "A")]
    out = preview.swap("https://example.test/b", closer=closer, opener=opener)
    assert out["closed"] == "https://example.test/a" and closed == ["https://example.test/a"]
    assert opened[-1][0] == "https://example.test/b"
    assert "desktop_preview is a Hermes core tool" in out["limitation"]
    assert preview.close_tracked(closer=closer)["closed"] == "https://example.test/b"
    assert preview.last_url() is None


def test_preview_swap_defers_when_no_desktop_emitter():
    preview.clear()
    out = preview.swap("https://example.test/x")
    assert out["ok"] and out.get("desktop_preview_steps")
    assert out["desktop_preview_steps"][-1]["action"] == "open"
    assert "limitation" in out


def test_desktop_plugin_exports_stable_pane_id():
    js = Path(__file__).resolve().parents[1] / "desktop" / "plugin.js"
    text = js.read_text()
    assert "stablePaneId" in text and "hermes-gev:pane-id:" in text
    assert "label" in text and "lifecycleKeepAlive: true" in text


def test_doctor_includes_provider_keys(monkeypatch, tmp_path):
    envf = tmp_path / "e.env"
    envf.write_text("")
    monkeypatch.setattr(composer, "env_file", lambda: envf)
    monkeypatch.setattr(composer, "relay_status", lambda: {"paneOpen": False, "panes": []})
    monkeypatch.setattr(composer, "panel_checks", lambda **kw: [])
    monkeypatch.setattr(composer, "service_started_at", lambda: None)
    from gev import config
    monkeypatch.setattr(config, "node_bin", lambda: None)
    monkeypatch.setattr(config, "checkout", lambda: None)
    monkeypatch.setattr(config, "api_base", lambda: "http://127.0.0.1:9")
    for k in ("AISSTREAM_API_KEY", "FIRMS_MAP_KEY", "FIRMS_API_KEY", "FIRMS_KEY"):
        monkeypatch.delenv(k, raising=False)
    d = composer.doctor()
    names = [c["check"] for c in d["checks"]]
    assert "key AISSTREAM_API_KEY" in names and "key FIRMS_MAP_KEY" in names
