"""P08: live commands go to a focused/visible pane and fail fast when it is dead.

No network: these use the in-process relay and fake pane long-polls only.
"""
from __future__ import annotations

import threading
import time

from gev import live


def _register(relay, pane_id, sessions=None):
    relay.next(wait=0, pane_id=pane_id, sessions=sessions or [])
    return relay.panes[pane_id]


def _submit_to_a_pane_that_never_answers(relay, name="zoom_to_globe"):
    picked_up = threading.Event()
    result = {}

    def poll():
        if relay.next(wait=2.0, pane_id="P"):
            picked_up.set()

    def call():
        started = time.monotonic()
        result["out"] = relay.submit(name, {})
        result["elapsed"] = time.monotonic() - started

    poller = threading.Thread(target=poll, daemon=True)
    caller = threading.Thread(target=call, daemon=True)
    poller.start()
    time.sleep(0.02)
    caller.start()
    assert picked_up.wait(2.0)
    caller.join(3.0)
    assert not caller.is_alive()
    return result["out"], result["elapsed"]


def test_unbound_session_prefers_visible_pane_over_a_more_recent_hidden_one():
    relay = live.Relay()
    hidden = _register(relay, "hidden")
    visible = _register(relay, "visible")
    relay.heartbeat("hidden", {"busy": False, "visible": False})
    relay.heartbeat("visible", {"busy": False, "visible": True})
    # The hidden pane polled most recently; visibility must still outrank it.
    hidden.last_poll = time.monotonic()
    visible.last_poll = time.monotonic() - 5.0
    assert relay.targets("a-chat-with-no-binding")[0].id == "visible"
    assert hidden.visible is False and visible.visible is True


def test_most_recently_focused_visible_pane_wins():
    relay = live.Relay()
    older = _register(relay, "older")
    newer = _register(relay, "newer")
    relay.heartbeat("older", {"busy": False, "visible": True, "focused": True})
    time.sleep(0.002)
    relay.heartbeat("newer", {"busy": False, "visible": True, "focused": True})
    # Focus left the pane chrome, but the recency history stays on the records.
    relay.heartbeat("older", {"busy": False, "visible": True, "focused": False})
    relay.heartbeat("newer", {"busy": False, "visible": True, "focused": False})
    # The older pane polls more recently; last focus must still win.
    older.last_poll = time.monotonic()
    newer.last_poll = time.monotonic() - 10.0
    assert relay.targets("unbound")[0].id == "newer"


def test_session_routing_prefers_visible_among_bound_panes():
    relay = live.Relay()
    hidden = _register(relay, "hidden", sessions=["chat"])
    visible = _register(relay, "visible")
    visible.sessions.add("chat")  # a second live pane showing the same chat
    relay.heartbeat("hidden", {"busy": False, "visible": False})
    relay.heartbeat("visible", {"busy": False, "visible": True})
    hidden.last_poll = time.monotonic()
    visible.last_poll = time.monotonic() - 5.0
    assert relay.targets("chat")[0].id == "visible"


def test_hidden_pane_still_answers_when_it_is_the_only_pane():
    relay = live.Relay()
    only = _register(relay, "only")
    relay.heartbeat("only", {"busy": False, "visible": False})
    assert relay.targets("unbound")[0].id == "only"
    assert only.fresh(time.monotonic()) is True


def test_pane_info_reports_visibility_and_focus():
    relay = live.Relay()
    _register(relay, "P")
    relay.heartbeat("P", {"busy": False, "visible": False, "focused": False})
    info = {row["id"]: row for row in relay.list()}["P"]
    assert info["visible"] is False and info["focused"] is False
    relay.heartbeat("P", {"busy": False, "visible": True, "focused": True})
    info = {row["id"]: row for row in relay.list()}["P"]
    assert info["visible"] is True and info["focused"] is True


def test_next_accepts_visibility_and_focus_flags():
    relay = live.Relay()
    relay.next(wait=0, pane_id="P", sessions=["s"], visible=False, focused=False)
    assert relay.panes["P"].visible is False
    assert relay.panes["P"].focused is False
    relay.next(wait=0, pane_id="P", sessions=["s"], visible=True, focused=True)
    assert relay.panes["P"].visible is True
    assert relay.panes["P"].focused is True


def test_command_wait_budget_is_short_and_inflight_prune_sits_above_it():
    assert 20.0 <= live.COMMAND_WAIT_S <= 30.0
    assert live.INFLIGHT_STALE_S > live.COMMAND_WAIT_S
    assert live.INFLIGHT_STALE_S - live.COMMAND_WAIT_S <= 10.0
    assert live.BUSY_STALE_S >= live.INFLIGHT_STALE_S


def test_timeout_is_fast_and_keeps_a_clear_fix(monkeypatch):
    monkeypatch.setattr(live, "COMMAND_WAIT_S", 0.25)
    monkeypatch.setattr(live, "PICKUP_S", 5.0)
    relay = live.Relay()
    relay.next(wait=0, pane_id="P")
    out, elapsed = _submit_to_a_pane_that_never_answers(relay)
    assert out["error"] == "timeout" and out["code"] == "timeout"
    assert out.get("fix") and "retry" in out["fix"].lower()
    assert elapsed < 2.0


def test_a_late_result_after_timeout_is_dropped(monkeypatch):
    monkeypatch.setattr(live, "COMMAND_WAIT_S", 0.25)
    monkeypatch.setattr(live, "PICKUP_S", 5.0)
    relay = live.Relay()
    relay.next(wait=0, pane_id="P")
    assert _submit_to_a_pane_that_never_answers(relay)[0]["error"] == "timeout"
    assert relay.abandoned
    cid = next(iter(relay.abandoned))
    relay.result(cid, {"ok": True})
    assert cid not in relay.results and cid not in relay.abandoned
