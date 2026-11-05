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
    r.next(wait=0)  # a pane registers (default id)
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
    r = live.Relay(); r.next(wait=0)
    t0 = time.monotonic()
    assert r.submit("zoom_to_globe", {}, wait=5)["error"] == "no_pane"
    assert time.monotonic() - t0 < 2


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


# ---------------------------------------------------------------- v0.2
class Ctx2(FakeCtx):
    def __init__(self):
        super().__init__()
        self.commands, self.skills, self.store = {}, {}, {}
        self.state = SimpleNamespace(get=lambda k, d=None: self.store.get(k, d),
                                     set=lambda k, v: self.store.__setitem__(k, v))

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_skill(self, name, path, description=""):
        assert Path(path).is_file()
        self.skills[name] = path


def _poll_in_thread(r, pane, sessions=None, reply=None, n=1):
    import threading
    seen = []

    def run():
        for _ in range(n):
            cmd = r.next(wait=3, pane_id=pane, sessions=sessions)
            if cmd:
                seen.append(cmd)
                r.result(cmd["id"], reply(cmd) if reply else {"ok": True, "result": {"pane": pane}})
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, seen


def test_relay_routes_to_session_bound_pane_not_first_poller():
    import time
    from gev import live
    r = live.Relay()
    r.next(wait=0, pane_id="A", sessions=["s-a"])
    time.sleep(0.02)
    r.next(wait=0, pane_id="B", sessions=["s-b"])  # B is the most recent
    ta, seen_a = _poll_in_thread(r, "A", ["s-a"])
    tb, seen_b = _poll_in_thread(r, "B", ["s-b"])
    time.sleep(0.1)
    out = r.submit("get_current_view_state", {}, wait=3, session_id="s-a")
    assert out["result"]["pane"] == "A" and out["pane"] == "A"
    out = r.submit("get_current_view_state", {}, wait=3, session_id="unknown")
    assert out["pane"] == "B"  # unbound session -> most recently active pane
    ta.join(4); tb.join(4)


def test_relay_fanout_and_pane_list():
    import time
    from gev import live
    r = live.Relay()
    ta, _ = _poll_in_thread(r, "A")
    tb, _ = _poll_in_thread(r, "B")
    time.sleep(0.1)
    panes = {p["id"]: p for p in r.list()}
    assert set(panes) == {"A", "B"} and panes["A"]["polling"] and panes["A"]["lastPollAgeS"] < 5
    out = r.submit("zoom_to_globe", {}, wait=3, fanout=True)
    assert out["ok"] and set(out["fanout"]) == {"A", "B"}
    ta.join(4); tb.join(4)


def test_relay_reports_pane_busy(monkeypatch):
    from gev import live
    monkeypatch.setattr(live, "PICKUP_S", 0.2)
    monkeypatch.setattr(live, "BUSY_WAIT_S", 0.3)
    r = live.Relay()
    r.next(wait=0, pane_id="A")
    r.panes["A"].queue.append({"id": 999, "name": "x", "args": {}})
    assert r.next(wait=0, pane_id="A")["id"] == 999  # pane is now running 999 and not polling
    assert r.submit("zoom_to_globe", {}, wait=2)["error"] == "pane_busy"


def test_relay_http_query_routing(tmp_path):
    import threading, time, urllib.request
    from http.server import ThreadingHTTPServer
    from gev import live
    relay = live.Relay()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), live.make_handler(relay, "tok"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    req = lambda path: urllib.request.Request(base + path, headers={"Authorization": "Bearer tok"})
    t = threading.Thread(target=lambda: urllib.request.urlopen(req("/next?pane=P1&sessions=s1,s2"), timeout=25), daemon=True)
    t.start(); time.sleep(0.2)
    st = json.loads(urllib.request.urlopen(req("/status")).read())
    assert st["paneOpen"] and st["panes"][0]["id"] == "P1" and st["panes"][0]["sessions"] == ["s1", "s2"]
    srv.shutdown()


def test_register_extras_command_skill_state():
    ctx = Ctx2()
    hermes_tools.register_tools(ctx)
    for name in ("godseye_brief_and_show", "godseye_doctor", "godseye_share_link", "godseye_home", "godseye_live_panes"):
        assert name in ctx.tools
    assert "gev" in ctx.commands and "godseye-analyst" in ctx.skills
    assert "fanout" in ctx.tools["godseye_live_fly_to_location"]["schema"]["parameters"]["properties"]
    import yaml
    provided = set(yaml.safe_load((ROOT / "plugin.yaml").read_text())["provides_tools"])
    assert set(ctx.tools) == provided


def test_register_extras_degrades_without_new_apis():
    ctx = FakeCtx()  # no register_command / register_skill / state
    done = hermes_tools.register_extras(ctx)
    assert done == {"tools": list(hermes_tools.EXTRA_TOOLS), "command": False, "skill": False, "state": False,
                    "watch": {"hook": False, "inject": False}}


def test_skill_has_policy_and_frontmatter():
    text = (ROOT / "skills/godseye-analyst/SKILL.md").read_text()
    assert text.startswith("---\nname: godseye-analyst")
    assert "Public assets and events only" in text and "no_pane" in text and "godseye_brief_and_show" in text


def _brief_result():
    data = {"view": {"camera": {"lat": 35.68, "lon": 139.69, "altitude_m": 181818}, "layers": ["flights", "earthquakes"]},
            "sections": {"aircraft": {"data": {"total": 40, "rows": [
                {"callsign": "JAL1", "lat": 35.5, "lon": 139.8, "distance_km": 9},
                {"callsign": "ANA2", "lat": 35.6, "lon": 139.7, "distance_km": 3},
                {"id": "x3", "lat": 35.7, "lon": 139.6, "distance_km": 20}]}},
                "weather": {"data": {"weather": {"conditions": "clear", "temperature_c": 14.6}}},
                "fires": {"unavailable": True}}}
    return {"content": [{"type": "text", "text": "Situation around Tokyo: 40 aircraft."}], "structuredContent": data}


class FakeLive:
    def __init__(self, open_=True, state=None):
        self.calls, self.open, self.state = [], open_, state or {}

    def __call__(self, name, args=None, session_id=None, **_kw):
        self.calls.append((name, args, session_id))
        if not self.open:
            return {"ok": False, "error": "no_pane", "message": "no pane"}
        if name == "get_current_view_state":
            return {"ok": True, "pane": "P", "result": self.state}
        return {"ok": True, "pane": "P", "result": {}}


def test_brief_and_show_flies_layers_annotates_caption():
    from gev import composer
    live, seen = FakeLive(), []
    out = composer.brief_and_show({"place": "Tokyo", "top_n": 2}, session_id="s1", live=live,
                                  mcp=lambda n, a: seen.append((n, a)) or _brief_result())
    assert seen[0] == ("situation_brief", {"area": {"place": "Tokyo"}})
    names = [c[0] for c in live.calls]
    assert names == ["fly_to_location", "set_layer_visibility", "set_layer_visibility", "annotate_map", "hermes_caption"]
    assert all(c[2] == "s1" for c in live.calls)
    marks = live.calls[3][1]["annotations"]
    assert [m["label"] for m in marks] == ["ANA2", "JAL1"]  # nearest first, top 2
    assert out["brief"].startswith("Situation") and out["shown"]["globe_open"] and out["shown"]["annotated"] == 2
    assert out["shown"]["caption"].startswith("Tokyo: 40 aircraft") and "queried extent" in out["shown"]["caption"]


def test_brief_and_show_without_pane_and_military_pick():
    from gev import composer
    seen = []
    out = composer.brief_and_show({"place": "San Diego", "question": "any military aircraft?"}, live=FakeLive(open_=False),
                                  mcp=lambda n, a: seen.append(n) or _brief_result())
    assert seen == ["military_awareness"]
    assert out["brief"] and out["shown"]["globe_open"] is False and "isn't open" in out["shown"]["message"]
    assert "Refused" in composer.brief_and_show({"place": "track someone in Paris"}, live=FakeLive(), mcp=None)["error"]


def test_brief_and_show_retries_with_coordinates_when_geocoder_down():
    from gev import composer
    seen = []

    def mcp(n, a):
        seen.append(a)
        return {"isError": True, "content": [{"type": "text", "text": "fail"}]} if "place" in a["area"] else _brief_result()
    out = composer.brief_and_show({"place": "tokyo"}, live=FakeLive(), mcp=mcp)
    assert seen[1]["area"]["lat"] == composer.PLACES["tokyo"][0] and out["notes"]


def test_home_state_and_file(tmp_path, monkeypatch):
    from gev import composer, home
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = {}
    home.bind(SimpleNamespace(get=lambda k, d=None: store.get(k, d), set=lambda k, v: store.__setitem__(k, v)))
    try:
        assert home.get() is None and home.get_or_default()["altitude_m"] == 15_000_000
        live = FakeLive(state={"camera": {"latitude": 45.5, "longitude": -73.56, "heightM": 30000}, "style": "noir",
                               "layers": [{"id": "flights", "enabled": True}, {"id": "ais-live-vessels"}]})
        out = composer.set_home({}, live=live)
        assert out["ok"] and store["home_view"]["style"] == "noir" and store["home_view"]["layers"] == ["flights", "ais-live-vessels"]
        home.bind(None)  # dashboard process: reads the mirrored file
        assert home.get()["lat"] == 45.5
        assert home.initial_view()["camera"] == {"lat": 45.5, "lon": -73.56, "altitude_m": 30000}
        live2 = FakeLive()
        assert composer.go_home(live=live2)["ok"]
        assert [c[0] for c in live2.calls] == ["fly_to_location", "set_visual_style", "set_layer_visibility", "set_layer_visibility"]
        with pytest.raises(ValueError):
            home.normalize({"lat": 200, "lon": 0})
    finally:
        home.bind(None)


def test_share_link_codec_and_fallback(monkeypatch):
    from gev import composer, config
    monkeypatch.setenv("GODSEYE_PUBLIC_URL", "https://gev.example.test")
    live = FakeLive(state={"camera": {"latitude": 35.68, "longitude": 139.69, "heightM": 20000}, "style": "thermal",
                           "layers": [{"id": "flights", "enabled": True}]})
    out = composer.share_link(live=live, encoder=lambda v: {"url": v["base"] + "/share/ABC"})
    assert out["url"] == "https://gev.example.test/share/ABC" and out["method"] == "share-code"
    out = composer.share_link(live=live, encoder=lambda v: (_ for _ in ()).throw(RuntimeError("x")))
    assert out["method"] == "camera-hash" and out["url"].startswith("https://gev.example.test/#v=2&lat=35.6800&lon=139.6900")
    assert composer.share_link(live=FakeLive(open_=False))["error"] == "no_pane"


@pytest.mark.skipif(not __import__("gev.config", fromlist=["x"]).checkout(), reason="needs the upstream checkout")
def test_share_link_real_upstream_codec():
    from gev import composer
    out = composer.encode_share({"lat": 35.68, "lon": 139.69, "alt": 20000, "style": "surveillance",
                                 "layers": ["flights"], "base": "https://gev.example.test"})
    assert out["url"].startswith("https://gev.example.test/share/")


def test_doctor_reports_fixes(monkeypatch):
    from gev import composer, config
    monkeypatch.setattr(config, "node_bin", lambda: None)
    monkeypatch.setattr(config, "checkout", lambda: None)
    monkeypatch.setattr(config, "api_base", lambda: "http://127.0.0.1:9")
    monkeypatch.setattr(composer, "relay_status", lambda: {"paneOpen": False, "panes": []})
    d = composer.doctor()
    by = {c["check"]: c for c in d["checks"]}
    assert not d["ok"] and "Node 24" in by["node"]["fix"] and "npm run dev" in by["app reachable"]["fix"]
    assert by["live relay"]["ok"] and not by["pane connected"]["ok"] and "Open the Eye Of Hermes (EOH) pane" in by["pane connected"]["fix"]
    assert "fix:" in composer.format_doctor(d)


def test_gev_command(monkeypatch):
    from gev import composer
    live = FakeLive(state={"camera": {"latitude": 1, "longitude": 2, "heightM": 5000}, "layers": [{"id": "flights", "enabled": True}]})
    assert "Flying to Tokyo" in composer.gev_command("fly Tokyo", live=live)
    assert live.calls[-1] == ("fly_to_location", {"query": "Tokyo"}, None)
    assert "Layers on: flights" == composer.gev_command("layers", live=live)
    assert "ships" in composer.gev_command("layers on ships", live=live)
    assert live.calls[-1][1] == {"layerId": "ships", "enabled": True}
    monkeypatch.setattr(composer, "relay_status", lambda: {"paneOpen": True, "panes": [{"id": "P", "open": True, "lastPollAgeS": 1.2}]})
    monkeypatch.setattr(composer, "doctor", lambda: {"ok": True, "checks": []})
    st = composer.gev_command("status", live=live)
    assert "Panes open: 1" in st and "last poll 1.2s" in st and "View: 1.000, 2.000" in st
    assert "/gev fly" in composer.gev_command("", live=live) and "Unknown" in composer.gev_command("nope", live=live)
    out = composer.gev_command("brief Tokyo", live=FakeLive(open_=False), mcp=lambda n, a: _brief_result())
    assert out.startswith("Situation") and "isn't open" in out


def test_panel_agent_handles_caption_and_pane_identity():
    js = (ROOT / "gev/panel_live_agent.js").read_text()
    assert "hermes_caption" in js and "hermes-gev-caption" in js
    plugin = (ROOT / "desktop/plugin.js").read_text()
    assert "<script" not in plugin and "pane: paneId" in plugin and "'/home'" in plugin


def test_pane_survives_layout_changes_and_releases_webgl():
    plugin = (ROOT / "desktop/plugin.js").read_text()
    assert "lifecycleKeepAlive: true" in plugin  # host never remounted/moved on resize
    assert "key: src.length" not in plugin and "key: `${fullUi ? 'f' : 'g'}-${nonce}`" in plugin
    assert "panelCache" in plugin and "disposeFrame(frame())" in plugin
    assert "Click to reload the globe" in plugin and "hermes-gev:gl-lost" in plugin
    agent = (ROOT / "gev/panel_live_agent.js").read_text()
    for needle in ("pagehide", "WEBGL_lose_context", "webglcontextlost", "e.preventDefault()",
                   "cesium-widget-errorPanel", "hermes-gev:dispose", "viewer.destroy()"):
        assert needle in agent


def test_panel_scripts_do_not_react_to_resize():
    from gev import panel_inject
    out = panel_inject.prepare("<head></head>")
    assert "addEventListener('resize'" not in out and 'addEventListener("resize"' not in out
