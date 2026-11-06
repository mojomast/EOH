"""YouTube and X clips stay on the pin and come back from list."""
from __future__ import annotations

import json

import pytest

from gev import ext_clips
from gev.marks import public_meta
from gev.scene import SceneStore, _public_annotation


def _book(tmp_path, monkeypatch):
    store = SceneStore(tmp_path / "scenes.json")
    monkeypatch.setattr(ext_clips, "store", lambda: store)
    store.record("s", "annotate_map", {"annotations": [{
        "type": "pin", "label": "Basin", "latitude": 30.2, "longitude": -97.7,
    }]}, {"ok": True, "result": {"items": [{"ok": True, "id": "pane-1", "latitude": 30.2, "longitude": -97.7, "label": "Basin"}]}})
    return store


def test_parse_youtube_and_x_and_refuse_other_hosts():
    clip, error = ext_clips.parse_clip("https://youtu.be/abcdefghijk?t=1m30s", title="Look")
    assert error is None
    assert clip["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert clip["embed"] == "https://www.youtube-nocookie.com/embed/abcdefghijk?start=90"
    assert clip["start_sec"] == 90 and clip["title"] == "Look"
    short, error = ext_clips.parse_clip("https://www.youtube.com/shorts/abcdefghijk", start_sec=12)
    assert error is None and short["start_sec"] == 12
    status, error = ext_clips.parse_clip("https://x.com/nasa/status/12345", title="Post")
    assert error is None
    assert status["embed"] == "https://platform.twitter.com/embed/Tweet.html?id=12345"
    assert status["url"] == "https://x.com/i/status/12345"
    assert ext_clips.parse_clip("https://x.com/nasa/status/12345", start_sec=4)[0] is None
    assert ext_clips.parse_clip("https://vimeo.com/1")[0] is None
    assert ext_clips.parse_clip("http://youtu.be/abcdefghijk")[0] is None


def test_clip_is_stored_idempotently_and_list_returns_it(tmp_path, monkeypatch):
    book = _book(tmp_path, monkeypatch)
    pane = []

    def live(name, args=None, session_id=None):
        pane.append((name, args, session_id))
        return {"ok": True, "action": "hermes_pin_update"}

    monkeypatch.setattr(ext_clips, "live", live)
    first = json.loads(ext_clips.attach({"label": "Basin", "url": "https://youtu.be/abcdefghijk", "title": "Look", "start_sec": 30}, session_id="s"))
    second = json.loads(ext_clips.attach({"id": first["id"], "url": "https://www.youtube.com/watch?v=abcdefghijk", "title": "Look again"}, session_id="s"))
    assert first["ok"] is True and first["pane_updated"] is True
    assert len(second["clips"]) == 1
    assert second["clips"][0]["title"] == "Look again"
    assert second["clips"][0]["start_sec"] == 30
    assert pane[0][0] == "hermes_pin_update" and pane[0][2] == "s"
    record = book.annotations("s")[0]
    public = _public_annotation({"id": record["id"], "spec": record["spec"]}, full=False)
    assert public["clips"][0]["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert "description_md" not in public
    full = public_meta(record["spec"])
    assert full["clips"][0]["title"] == "Look again"


def test_missing_pin_and_a_closed_pane_stay_honest(tmp_path, monkeypatch):
    book = _book(tmp_path, monkeypatch)
    monkeypatch.setattr(ext_clips, "live", lambda *a, **k: {"ok": False, "error": "no_pane", "code": "no_pane"})
    missing = json.loads(ext_clips.attach({"label": "Nowhere", "url": "https://youtu.be/abcdefghijk"}, session_id="s"))
    assert missing["ok"] is False and missing["code"] == "pin_not_found"
    stored = json.loads(ext_clips.attach({"label": "Basin", "url": "https://x.com/i/status/99", "title": "X"}, session_id="s"))
    assert stored["ok"] is True and stored["pane_updated"] is False
    assert book.annotations("s")[0]["spec"]["clips"][0]["provider"] == "x"


def test_a_pin_holds_four_clips(tmp_path, monkeypatch):
    _book(tmp_path, monkeypatch)
    monkeypatch.setattr(ext_clips, "live", lambda *a, **k: {"ok": True})
    for index in range(4):
        body = json.loads(ext_clips.attach({
            "label": "Basin", "url": f"https://x.com/i/status/{index + 1}", "title": str(index),
        }, session_id="s"))
        assert body["ok"] is True
    extra = json.loads(ext_clips.attach({"label": "Basin", "url": "https://x.com/i/status/9"}, session_id="s"))
    assert extra["ok"] is False and extra["code"] == "too_many"
    replaced = json.loads(ext_clips.attach({"label": "Basin", "url": "https://x.com/i/status/1", "title": "kept"}, session_id="s"))
    assert replaced["ok"] is True and len(replaced["clips"]) == 4
    assert replaced["clips"][0]["title"] == "kept"
