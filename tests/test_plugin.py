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
