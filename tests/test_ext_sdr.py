"""Public SDR receiver query on godseye_query op=sdr_receivers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gev import ext_ops, ext_sdr, hermes_tools, router  # noqa: E402

BERLIN = (52.52, 13.405)
# A playable OpenWebRX in Berlin, a Kiwi in Hamburg (HF), a SpyServer farther out.
CATALOG = [
    {
        "id": "openwebrx:example.berlin:8073",
        "platform": "openwebrx",
        "name": "Berlin JO62QJ",
        "lat": 52.52,
        "lon": 13.405,
        "url": "http://example.berlin:8073/",
        "playable": True,
        "waterfall": True,
        "embed": "allowed",
        "online": True,
        "users": 2,
        "usersMax": 8,
        "bandsHz": [1e6, 2e9],
    },
    {
        "id": "kiwi:example.hamburg:8073",
        "platform": "kiwi",
        "name": "Hamburg HF",
        "lat": 53.55,
        "lon": 9.99,
        "url": "http://example.hamburg:8073/",
        "playable": True,
        "waterfall": True,
        "embed": "unknown",
        "online": True,
        "bandsHz": [0, 30e6],
    },
    {
        "id": "spyserver:example.prague:5555",
        "platform": "spyserver",
        "name": "Prague SpyServer",
        "lat": 50.08,
        "lon": 14.44,
        "url": "https://example.prague:5555/",
        "playable": False,
        "waterfall": False,
        "embed": "blocked",
        "online": True,
        "bandsHz": [100e6, 200e6],
    },
]


class Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw

    def register_system_prompt_section(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass


def _payload(records=None, **extra):
    body = {"receivers": list(records if records is not None else CATALOG), "stale": False}
    body.update(extra)
    return body


def _fetch(payload, status=200):
    raw = json.dumps(payload).encode()

    def fetch(url):
        fetch.urls.append(url)
        return status, raw

    fetch.urls = []
    return fetch


def _call(args, fetch=None, session_id="s1", **kw):
    ctx = Ctx()
    hermes_tools.register_tools(ctx)
    handler = ctx.tools["godseye_query"]["handler"]
    extra = dict(kw)
    if fetch is not None:
        # The composable handler does not forward fetch; call the op directly
        # after the router has accepted the arguments, matching invoke().
        return json.loads(ext_sdr.sdr_receivers(args, fetch=fetch, session_id=session_id, **extra))
    return json.loads(handler({"op": "sdr_receivers", **args}, session_id=session_id))


def test_op_is_registered_on_query():
    ctx = Ctx()
    hermes_tools.register_tools(ctx)
    assert set(ctx.tools) == {
        "godseye_view", "godseye_query", "godseye_layers", "godseye_feeds",
        "godseye_annotate", "godseye_nav", "godseye_track", "godseye_scene",
        "godseye_watch", "godseye_capture", "godseye_admin",
    }
    enum = ctx.tools["godseye_query"]["schema"]["parameters"]["properties"]["op"]["enum"]
    assert "sdr_receivers" in enum
    assert enum == router.phase_a_ops("godseye_query")
    assert ext_ops.find("godseye_query", "sdr_receivers") is not None
    props = ctx.tools["godseye_query"]["schema"]["parameters"]["properties"]
    assert props["platform"]["enum"]
    assert "vhf" in props["band"]["enum"]
    assert "kiwi" in props["platform"]["enum"]
    desc = ctx.tools["godseye_query"]["schema"]["description"]
    assert "op=sdr_receivers:" in desc
    assert "Do not start audio" in desc or "Does not start audio" in desc


def test_ranks_playable_receivers_by_distance():
    out = ext_sdr.sdr_receivers(
        {"lat": BERLIN[0], "lon": BERLIN[1], "band": "vhf"},
        fetch=_fetch(_payload()),
    )
    body = json.loads(out)
    assert body["ok"] is True
    assert body["receivers"][0]["id"] == "openwebrx:example.berlin:8073"
    assert body["receivers"][0]["km"] == 0.0
    assert body["receivers"][0]["playable"] is True
    assert "deep_link" in body["receivers"][0]
    # Hamburg is HF-only; Prague is SpyServer and dropped by the waterfall default.
    ids = [row["id"] for row in body["receivers"]]
    assert "kiwi:example.hamburg:8073" not in ids
    assert "spyserver:example.prague:5555" not in ids
    assert body["waterfall_only"] is True
    assert "start audio" in body["listen"].lower() or "pin click" in body["listen"].lower()
    assert isinstance(out, str)


def test_all_includes_spyserver_inside_the_radius():
    body = json.loads(ext_sdr.sdr_receivers(
        {"lat": BERLIN[0], "lon": BERLIN[1], "all": True, "radius_km": 400, "band": "vhf"},
        fetch=_fetch(_payload()),
    ))
    ids = [row["id"] for row in body["receivers"]]
    assert "spyserver:example.prague:5555" in ids
    assert body["waterfall_only"] is False


def test_frequency_keeps_receivers_that_cover_it():
    body = json.loads(ext_sdr.sdr_receivers(
        {"lat": BERLIN[0], "lon": BERLIN[1], "frequency_mhz": 7.14, "radius_km": 500, "all": True},
        fetch=_fetch(_payload()),
    ))
    ids = [row["id"] for row in body["receivers"]]
    assert "kiwi:example.hamburg:8073" in ids
    assert "openwebrx:example.berlin:8073" in ids
    assert "spyserver:example.prague:5555" not in ids
    assert "f=7.14" in body["receivers"][0]["deep_link"]


def test_band_alias_2m_is_vhf():
    assert ext_sdr.canonical_band("2m") == "vhf"
    body = json.loads(ext_sdr.sdr_receivers(
        {"lat": BERLIN[0], "lon": BERLIN[1], "band": "2m"},
        fetch=_fetch(_payload()),
    ))
    assert body["ok"] is True
    assert body["receivers"][0]["id"] == "openwebrx:example.berlin:8073"


def test_unknown_platform_is_bad_args_with_fix():
    body = json.loads(ext_sdr.sdr_receivers({"platform": "rtlsdr"}, fetch=_fetch(_payload())))
    assert body["ok"] is False
    assert body["error"] == "bad_args"
    assert body["fix"]
    assert "kiwi" in body["fix"]


def test_app_down_is_sdr_unavailable_with_fix():
    def fetch(url):
        raise ConnectionRefusedError("connection refused")

    body = json.loads(ext_sdr.sdr_receivers({"lat": 52.5, "lon": 13.4}, fetch=fetch))
    assert body["ok"] is False
    assert body["error"] == "sdr_unavailable"
    assert "app_start" in body["fix"] or "npm run dev" in body["fix"]


def test_old_checkout_is_sdr_unsupported():
    body = json.loads(ext_sdr.sdr_receivers(
        {"lat": 52.5, "lon": 13.4},
        fetch=_fetch({"error": "not found"}, status=404),
    ))
    assert body["ok"] is False
    assert body["error"] == "sdr_unsupported"
    assert "b361a25" in body["fix"]


def test_here_uses_the_pane_camera(monkeypatch):
    monkeypatch.setattr(router, "camera", lambda sid: ({"lat": BERLIN[0], "lon": BERLIN[1], "rangeM": 8000}, None))
    fetch = _fetch(_payload())
    body = json.loads(ext_sdr.sdr_receivers({"here": True}, fetch=fetch, session_id="chat-1"))
    assert body["ok"] is True
    assert body["origin"]["source"] == "pane"
    assert body["receivers"][0]["km"] == 0.0
    assert "/api/sdr/receivers" in fetch.urls[0]


def test_here_without_a_pane_returns_the_camera_error(monkeypatch):
    err = {"ok": False, "error": "no_pane", "message": "No pane.", "fix": "Open the pane."}
    monkeypatch.setattr(router, "camera", lambda sid: (None, err))
    body = json.loads(ext_sdr.sdr_receivers({"here": True}, fetch=_fetch(_payload())))
    assert body["ok"] is False
    assert body["error"] == "no_pane"


def test_place_uses_geocode(monkeypatch):
    monkeypatch.setattr(
        "gev.poi.geocode",
        lambda place: {"ok": True, "name": "Berlin, Germany", "lat": BERLIN[0], "lon": BERLIN[1]},
    )
    body = json.loads(ext_sdr.sdr_receivers({"place": "Berlin"}, fetch=_fetch(_payload())))
    assert body["ok"] is True
    assert body["origin"]["source"] == "place"
    assert body["receivers"][0]["name"] == "Berlin JO62QJ"


def test_empty_area_has_a_fix():
    body = json.loads(ext_sdr.sdr_receivers(
        {"lat": 0.1, "lon": 0.1, "radius_km": 1},
        fetch=_fetch(_payload()),
    ))
    assert body["ok"] is True
    assert body["total"] == 0
    assert body["fix"]
    assert body["receivers"] == []


def test_pin_annotates_and_does_not_claim_audio(monkeypatch):
    calls = []

    def live_call(name, args, session_id=None, **kw):
        calls.append((name, args, session_id))
        return {"ok": True, "added": len(args.get("annotations") or [])}

    monkeypatch.setattr("gev.composer.live_call", live_call)
    body = json.loads(ext_sdr.sdr_receivers(
        {"lat": BERLIN[0], "lon": BERLIN[1], "pin": True},
        fetch=_fetch(_payload()),
        session_id="chat-9",
    ))
    assert body["ok"] is True
    assert calls and calls[0][0] == "annotate_map"
    assert calls[0][2] == "chat-9"
    assert body["pinned"]["ok"] is True
    assert "listen" in body


def test_handler_result_is_a_json_string():
    result = ext_sdr.sdr_receivers({"lat": BERLIN[0], "lon": BERLIN[1]}, fetch=_fetch(_payload()))
    assert isinstance(result, str)
    json.loads(result)


def test_no_pane_fix_mentions_composable_tools():
    from gev import errors, live
    assert "without live_" not in errors.TABLE["no_pane"]["fix"]
    assert "godseye_query" in errors.TABLE["no_pane"]["fix"]
    assert "without live_" not in live.NO_PANE["fix"]
    assert "godseye_feeds" in live.NO_PANE["fix"]


def test_admin_schema_does_not_advertise_key_install():
    ctx = Ctx()
    hermes_tools.register_tools(ctx)
    desc = ctx.tools["godseye_admin"]["schema"]["description"]
    assert "key install" not in desc.lower()
