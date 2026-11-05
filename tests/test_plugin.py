import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gev import hermes_tools, hud, mcp_client, policy  # noqa: E402


class FakeCtx:
    def __init__(self, llm=None):
        self.tools, self.llm = {}, llm

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw


def test_registers_catalog_without_panel_plumbing():
    ctx = FakeCtx()
    hermes_tools.register_tools(ctx)
    assert "godseye_aircraft_in_area" in ctx.tools
    assert "godseye_panel_request" not in ctx.tools
    assert "godseye_hud_summary" in ctx.tools
    for t in ctx.tools.values():
        assert t["schema"]["parameters"]["type"] == "object"
        assert t["toolset"] == "godseye"


@pytest.mark.parametrize("text", ["run facial recognition on this", "locate a person near the port",
                                  "track someone from home", "face search"])
def test_policy_refuses_people(text):
    assert policy.refuse_reason({"q": text})


@pytest.mark.parametrize("text", ["San Diego", "military flights", "container ships"])
def test_policy_allows_places(text):
    assert policy.refuse_reason({"q": text}) is None


def test_handler_refuses_before_calling_mcp(monkeypatch):
    monkeypatch.setattr(mcp_client, "client", lambda: pytest.fail("must not call"))
    out = json.loads(hermes_tools.make_handler("find_vessel")({"query": "facial recognition"}))
    assert "Refused" in out["error"]


def test_parse_reply():
    assert hud.parse_reply('{"summary":"Busy skies over SD","feedState":"nominal"}') == {
        "summary": "Busy skies over SD", "feedState": "nominal"}
    assert hud.parse_reply("nope")["summary"] is None
    assert hud.parse_reply('{"summary":"x","feedState":"bogus"}')["feedState"] is None


def test_hud_uses_host_llm(monkeypatch):
    monkeypatch.setattr(hud, "build_prompt", lambda c: {"instructions": "I", "input": "X"})
    seen = {}

    class Llm:
        def complete(self, **kw):
            seen.update(kw)
            return SimpleNamespace(text='{"summary":"Quiet coast","feedState":"nominal"}')

    assert hud.summarize(Llm(), {})["summary"] == "Quiet coast"
    assert seen["purpose"] == "godseye.hud-summary"


def test_mcp_client_against_fake_server(tmp_path):
    fake = tmp_path / "srv.py"
    fake.write_text(
        "import sys,json\n"
        "for l in sys.stdin:\n"
        " m=json.loads(l)\n"
        " if 'id' not in m: continue\n"
        " r={'content':[{'type':'text','text':'hi '+json.dumps(m['params'].get('arguments',{}))}]}\n"
        " print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)\n")
    c = mcp_client.StdioMcpClient([sys.executable, str(fake)], timeout=5)
    try:
        assert "hi" in hermes_tools._format(c.call_tool("x", {"a": 1}))
    finally:
        c.close()


@pytest.mark.skipif(not os.environ.get("GODSEYE_E2E"), reason="set GODSEYE_E2E=1 with npm run dev running")
def test_e2e_live_tool():
    out = hermes_tools.make_handler("get_earthquakes")({})
    assert "error" not in json.loads(out) if out.startswith("{\"error") else True
    assert len(out) > 20


def test_bridge_round_trip(tmp_path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from gev import bridge

    token = bridge.ensure_token(tmp_path / "b.env")
    assert (tmp_path / "b.env").stat().st_mode & 0o777 == 0o600
    assert bridge.ensure_token(tmp_path / "b.env") == token
    srv = ThreadingHTTPServer(("127.0.0.1", 0), bridge.make_handler(lambda m, n: "pong:" + m[-1]["content"], token))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
    body = json.dumps({"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}).encode()

    def post(auth):
        req = urllib.request.Request(url, body, {"Content-Type": "application/json", **auth})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    try:
        assert post({})[0] == 401
        code, data = post({"Authorization": f"Bearer {token}"})
        assert code == 200 and data["choices"][0]["message"]["content"] == "pong:hi"
    finally:
        srv.shutdown()


def test_live_relay_no_pane_and_roundtrip():
    import threading
    from gev import live
    r = live.Relay()
    assert r.submit("get_current_view_state", {})["error"] == "no_pane"
    r.last_poll = __import__("time").monotonic()
    out = {}
    t = threading.Thread(target=lambda: out.update(r.submit("fly_to_location", {"query": "Tokyo"}, wait=5)))
    t.start()
    cmd = r.next(wait=2)
    assert cmd["name"] == "fly_to_location"
    r.result(cmd["id"], {"ok": True, "result": {"label": "Tokyo"}})
    t.join(3)
    assert out["result"]["label"] == "Tokyo"


def test_live_tools_registered_with_prompt():
    from gev import hermes_tools as h
    names = [t["name"] for t in h.load_live_catalog()]
    assert "fly_to_location" in names and "get_current_view_state" in names
    assert "set_voice_inactivity_timeout" not in names
    assert "no pane" in h.SYSTEM_PROMPT.lower() or "open the god's eye view pane" in h.SYSTEM_PROMPT.lower()


def test_live_relay_unpicked_command_reports_no_pane(monkeypatch):
    import time
    from gev import live
    monkeypatch.setattr(live, "PICKUP_S", 0.2)
    r = live.Relay(); r.last_poll = time.monotonic()
    assert r.submit("zoom_to_globe", {}, wait=5)["error"] == "no_pane"


def test_panel_inject_adds_guest_scripts_and_flag():
    from gev import panel_inject
    out = panel_inject.prepare("<html><head><title>x</title></head></html>", full_ui=True)
    assert out.index("<base") < out.index("<title>")
    assert "GEV_EMBED_FULL_UI" in out and "CESIUM_WORKERS" in out and "hermes-gev:run" in out
    assert "GEV_EMBED_FULL_UI" not in panel_inject.prepare("<head></head>", full_ui=False)


def test_patch_detection(tmp_path, monkeypatch):
    from gev import config
    monkeypatch.setenv("GODSEYE_HOME", str(tmp_path))
    (tmp_path / "src/app").mkdir(parents=True)
    (tmp_path / "src/app/embed.js").write_text("export const x = 1")
    monkeypatch.setattr(config, "checkout", lambda: tmp_path)
    assert config.patched() == {"fullUi": False, "hud": False}
    (tmp_path / "src/app/embed.js").write_text("globalThis.GEV_EMBED_FULL_UI")
    assert config.patched()["fullUi"] is True
