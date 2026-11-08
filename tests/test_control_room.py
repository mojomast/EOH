"""Control room: doctor aggregation, process ownership, and a fake systemd runner."""
from __future__ import annotations

import hashlib
import json
import os
import signal
from pathlib import Path

import pytest

from gev import composer, control_room, live

ROOT = Path(__file__).resolve().parents[1]
SECRET = "fixturevalueXYZ"
SECRET_SHA = hashlib.sha256(SECRET.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _reset_room():
    control_room.reset_runtime()
    yield
    control_room.reset_runtime()


def _doctor(**over):
    report = {
        "ok": True,
        "checks": [
            {"check": "app reachable", "ok": True, "detail": f"AISSTREAM_API_KEY={SECRET}"},
            {"check": "live relay", "ok": True, "detail": "127.0.0.1:18792"},
            {"check": "pane connected", "ok": True, "detail": "1 open"},
            {"check": "webgl recover", "ok": True, "detail": "on"},
            {"check": "key AISSTREAM_API_KEY", "ok": False, "detail": "missing"},
            {"check": "key FIRMS_MAP_KEY", "ok": True, "detail": "set"},
            {"check": "layer feeds", "ok": True, "detail": "nominal"},
        ],
        "panes": [
            {"id": "pane-a", "sessions": ["chat-1"], "open": True, "busy": False,
             "lastHeartbeatAgeS": 3.2, "contextLosses": 2},
            {"id": "pane-b", "sessions": ["chat-2"], "open": False, "lastPollAgeS": 40},
        ],
        "signals": {
            "rendering": {"webgl": "ok", "contextLosses": 4, "recover": True, "recreateViewer": True},
            "feedProvenance": {"layers": [
                {"id": "ships", "feedState": "nominal", "lastFetch": {"ok": True, "ageS": 12}},
                {"id": "fires", "feedState": "unavailable",
                 "lastFetch": {"ok": False, "ageS": 90, "error": f"FIRMS_MAP_KEY={SECRET}"}},
                {"id": "quakes", "feedState": "partial"},
            ]},
        },
    }
    report.update(over)
    return report


def _status(report=None, **kw):
    calls = []

    def runner(argv):
        calls.append(list(argv))
        return 0, "LoadState=not-found\n"

    body = control_room.status(
        doctor=lambda: report if report is not None else _doctor(),
        probe=lambda: {"http": 200, "version": "1.4.0", "commit": "abc1234def56",
                       "versionSource": "app", "commitSource": "app"},
        bridge=lambda: {"state": "up", "port": 18791},
        platform=kw.get("platform", "linux"),
        systemd_runner=kw.get("systemd_runner", runner),
        urls={"apiBase": "http://127.0.0.1:4173/", "publicUrl": "http://127.0.0.1:4173/"},
    )
    body["_runner"] = calls
    return body


def _no_secret(payload) -> None:
    blob = json.dumps(payload)
    assert hashlib.sha256(SECRET.encode()).hexdigest() == SECRET_SHA
    assert SECRET not in blob
    assert "env" not in payload


def test_status_aggregates_sections_and_names_missing_keys_only():
    body = _status()
    assert body["ok"] is True and body["busy"] is None and body["lastError"] is None
    assert body["app"]["state"] == "up" and body["app"]["http"] == 200
    assert body["app"]["version"] == "1.4.0" and body["app"]["commit"] == "abc1234def56"
    assert body["app"]["url"] == "http://127.0.0.1:4173/"
    assert body["relay"]["state"] == "up" and body["relay"]["hudBridge"] == "up"
    assert body["pane"]["connected"] == 1
    by_id = {row["id"]: row for row in body["pane"]["panes"]}
    assert by_id["pane-a"]["heartbeat"] == "heartbeat" and by_id["pane-a"]["lastHeartbeatS"] == 3.2
    assert by_id["pane-a"]["sessions"] == ["chat-1"]
    assert by_id["pane-b"]["heartbeat"] == "poll" and by_id["pane-b"]["lastHeartbeatS"] == 40
    gl = body["rendering"]
    assert gl["webgl"] == "ok" and gl["contextLosses"] == 4
    assert gl["recover"] is True and gl["recreateViewer"] is True
    feeds = {item["id"]: item for item in body["feeds"]["items"]}
    assert feeds["ships"] == {"id": "ships", "state": "ok", "ageS": 12, "detail": "nominal"}
    assert feeds["fires"]["state"] == "error" and feeds["fires"]["ageS"] == 90
    assert feeds["quakes"]["state"] == "ok" and feeds["quakes"]["detail"] == "partial"
    assert body["feeds"]["missingKeys"] == ["AISSTREAM_API_KEY"]
    assert body["lifecycle"]["mode"] == "process" and body["lifecycle"]["supported"] is True
    assert body["_runner"] and all(call[2] == "show" for call in body["_runner"])
    _no_secret(body)
    assert "[redacted]" in body["app"]["detail"]
    assert "[redacted]" in feeds["fires"]["detail"]


def test_json_root_404_does_not_decide_app_state(tmp_path):
    """God's Eye View returns 404 for `/` when Accept is application/json.

    `/health` and a plain `/` both return 200. That is the up/down signal.
    The version lookup is best-effort.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            accept = self.headers.get("Accept") or ""
            if self.path == "/" and "application/json" in accept:
                self.send_response(404)
                self.end_headers()
                return
            if self.path in ("/", "/health"):
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            return None

    (tmp_path / "package.json").write_text('{"version":"9.9.9"}\n')
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        probed = control_room.probe_app(url=f"http://127.0.0.1:{port}", checkout=tmp_path)
        assert control_room.app_http_status(f"http://127.0.0.1:{port}") == 200
    finally:
        server.shutdown()
        thread.join(2)
    assert probed["http"] == 200
    assert probed["version"] == "9.9.9" and probed["versionSource"] == "checkout"
    assert probed["commitSource"] != "app"

    def health_only(url):
        if url.endswith("/health"):
            return 404, b"", ""
        if url.endswith("/"):
            return 200, b"ok", "text/html"
        return 404, b"", ""

    plain = control_room.probe_app(url="http://app.test", checkout=tmp_path, fetch=health_only)
    assert plain["http"] == 200 and plain["version"] == "9.9.9"

    report = _doctor()
    body = control_room.aggregate(
        report, {"http": 404, "version": None, "commit": None},
        {"state": "up", "port": 1},
        {"supported": True, "platform": "linux", "mode": "process"},
        {"apiBase": "http://127.0.0.1:4173/"},
    )
    assert body["app"]["state"] == "up" and body["app"]["http"] == 404


def test_status_app_down_and_feed_summary_when_provenance_is_absent():
    report = _doctor()
    report["checks"][0] = {"check": "app reachable", "ok": False, "detail": "connection refused"}
    report["signals"] = {"rendering": {"webgl": "lost", "contextLosses": 0, "recover": False}}
    report["checks"].append({"check": "key FIRMS_MAP_KEY", "ok": False, "detail": "missing"})
    # The FIRMS row above is a second entry; the first one is ok. _check returns the first.
    report["checks"] = [c for c in report["checks"] if c["check"] != "key FIRMS_MAP_KEY"]
    report["checks"].append({"check": "key FIRMS_MAP_KEY", "ok": False, "detail": "missing"})
    report["signals"]["feedProvenance"] = {"layers": []}
    body = _status(report, platform="darwin")
    assert body["app"]["state"] == "down"
    assert body["rendering"]["webgl"] == "lost" and body["rendering"]["recover"] is False
    assert body["rendering"]["recreateViewer"] is None
    assert body["feeds"]["items"][0]["id"] == "layer feeds" and body["feeds"]["items"][0]["state"] == "ok"
    assert body["feeds"]["missingKeys"] == ["AISSTREAM_API_KEY", "FIRMS_MAP_KEY"]
    assert body["lifecycle"]["mode"] == "unsupported" and body["lifecycle"]["supported"] is False
    assert body["_runner"] == []


def test_control_signals_keep_rendering_and_drop_secret_text():
    sig = composer.control_signals({
        "rendering": {"webgl": "lost", "contextLosses": 4, "recover": False, "recreateViewer": True},
        "feedProvenance": {"overall": "unavailable", "layers": [
            {"id": "ais", "feedState": "unavailable", "lastFetch": {"ok": False, "ageS": 8, "error": f"token={SECRET}"}},
        ]},
    })
    assert sig["rendering"] == {"webgl": "lost", "contextLosses": 4, "recover": False, "recreateViewer": True}
    assert sig["feedProvenance"]["layers"][0]["lastFetch"]["ok"] is False
    assert SECRET not in json.dumps(sig)
    assert "[redacted]" in sig["feedProvenance"]["layers"][0]["lastFetch"]["error"]


def test_owns_listener_requires_node_checkout_and_port(tmp_path):
    node = tmp_path / "node"
    node.write_text("#!/bin/sh\n")
    checkout = tmp_path / "gods-eye-view"
    checkout.mkdir()
    vite = str(checkout / "node_modules" / "vite" / "bin" / "vite.js")
    good = {"pid": 7, "exe": str(node), "cwd": str(checkout), "cmdline": [str(node), vite], "port": 4173}
    assert control_room.owns_listener(good, str(node), str(checkout), 4173) == (True, "")
    assert control_room.owns_listener({**good, "exe": "/usr/bin/python"}, str(node), str(checkout), 4173)[0] is False
    assert control_room.owns_listener({**good, "port": 4174}, str(node), str(checkout), 4173) == (False, "port")
    foreign_cmd = {"pid": 7, "exe": str(node), "cwd": "/tmp", "cmdline": [str(node), "vite.js"], "port": 4173}
    assert control_room.owns_listener(foreign_cmd, str(node), str(checkout), 4173) == (False, "checkout")
    via_cmd = {"pid": 7, "exe": str(node), "cwd": "/tmp", "cmdline": [str(node), vite], "port": 4173}
    assert control_room.owns_listener(via_cmd, str(node), str(checkout), 4173) == (True, "")


def test_plan_stop_refuses_when_any_listener_is_foreign(tmp_path):
    node = tmp_path / "node"
    node.write_text("node")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    owned = {"pid": 4, "exe": str(node), "cwd": str(checkout), "cmdline": [], "port": 4173}
    foreign = {"pid": 5, "exe": "/usr/bin/python", "cwd": "/tmp", "cmdline": ["python"], "port": 4173}
    refused = control_room.plan_stop([owned, foreign], str(node), str(checkout), 4173)
    assert refused == {"action": "refuse", "pids": [], "code": "port_in_use"}
    planned = control_room.plan_stop([owned], str(node), str(checkout), 4173)
    assert planned == {"action": "stop", "pids": [4]}
    assert control_room.plan_stop([], str(node), str(checkout), 4173) == {"action": "noop", "pids": []}


def _proc_tree(root: Path, pid: int, node: Path, checkout: Path, port: int, inode: str, start: str):
    (root / "net").mkdir(parents=True, exist_ok=True)
    line = f"  0: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 {inode}\n"
    (root / "net" / "tcp").write_text(line)
    base = root / str(pid)
    (base / "fd").mkdir(parents=True)
    os.symlink(f"socket:[{inode}]", base / "fd" / "3")
    os.symlink(str(node), base / "exe")
    os.symlink(str(checkout), base / "cwd")
    vite = checkout / "node_modules" / "vite" / "bin" / "vite.js"
    (base / "cmdline").write_bytes(b"\0".join([str(node).encode(), str(vite).encode(), b"--port", str(port).encode()]))
    (base / "stat").write_text(f"{pid} (node) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 {start}\n")


def test_listeners_from_proc_reads_a_fake_tree(tmp_path):
    node = tmp_path / "bin" / "node"
    node.parent.mkdir()
    node.write_text("node")
    checkout = tmp_path / "gods-eye-view"
    checkout.mkdir()
    proc = tmp_path / "proc"
    _proc_tree(proc, 42, node, checkout, 4173, "4242", "99999")
    # A non-listen connection and a different port must not be selected.
    (proc / "net" / "tcp").write_text(
        (proc / "net" / "tcp").read_text()
        + "  1: 0100007F:104D 00000000:0000 01 00000000:00000000 00:00000000 00000000 0 0 9999\n"
        + "  2: 0100007F:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 8888\n"
    )
    found = control_room.listeners_from_proc(4173, proc)
    assert len(found) == 1
    snap = found[0]
    assert snap["pid"] == 42 and snap["port"] == 4173 and snap["starttime"] == "99999"
    assert control_room.owns_listener(snap, str(node), str(checkout), 4173) == (True, "")
    assert control_room.listeners_from_proc(80, proc) == []


def test_parse_systemctl_show():
    parsed = control_room.parse_systemctl_show(
        "LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=321\nFragmentPath=/home/u/.config/systemd/user/gods-eye-view.service\n"
    )
    assert parsed["loaded"] is True and parsed["active"] == "active" and parsed["pid"] == 321
    missing = control_room.parse_systemctl_show("LoadState=not-found\n")
    assert missing["loaded"] is False and missing["pid"] == 0


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, _seconds):
        self.now += 100


def _unit(loaded: bool):
    calls = []

    def runner(argv):
        calls.append(list(argv))
        if argv[2] == "show":
            state = "loaded" if loaded else "not-found"
            active = "inactive" if loaded else ""
            return 0, f"LoadState={state}\nActiveState={active}\nMainPID=0\n"
        return 0, ""

    return runner, calls


def _paths(tmp_path):
    node = tmp_path / "node"
    node.write_text("node")
    checkout = tmp_path / "gods-eye-view"
    vite = checkout / "node_modules" / "vite" / "bin" / "vite.js"
    vite.parent.mkdir(parents=True, exist_ok=True)
    vite.write_text("// vite\n")
    return node, checkout


def _owned(node, checkout, pid=42, start="111"):
    return {
        "pid": pid, "exe": str(node), "cwd": str(checkout),
        "cmdline": [str(node), str(checkout / "node_modules" / "vite" / "bin" / "vite.js")],
        "port": 4173, "starttime": start,
    }


def _action(action, tmp_path, **kw):
    node, checkout = _paths(tmp_path)
    runner, calls = kw.pop("runner_pair", _unit(False))
    killed = []

    def killer(pid, sig):
        killed.append((pid, int(sig)))

    out = control_room.app_action(
        action,
        platform=kw.pop("platform", "linux"),
        systemd_runner=runner,
        node=str(node),
        checkout=str(checkout),
        port=4173,
        url="http://127.0.0.1:4173/",
        bind="127.0.0.1",
        killer=killer,
        **kw,
    )
    return out, calls, killed


def test_systemd_start_waits_for_http_200_not_an_open_socket(tmp_path):
    seen = {"n": 0}

    def opener(_url):
        seen["n"] += 1
        return None if seen["n"] == 1 else 200

    runner, calls = _unit(True)
    out, recorded, killed = _action(
        "start", tmp_path, runner_pair=(runner, calls), opener=opener,
        listeners=lambda _port: [], port_open=lambda _port: False,
        sleep=lambda _s: None, monotonic=lambda: 0, timeout=60,
    )
    assert out["ok"] is True and out["mode"] == "systemd" and out["http"] == 200
    assert ["systemctl", "--user", "start", "gods-eye-view.service"] in recorded
    assert killed == []
    assert seen["n"] >= 2


def test_systemd_start_times_out_when_http_never_returns_200(tmp_path):
    clock = _Clock()
    runner, calls = _unit(True)
    out, recorded, _killed = _action(
        "start", tmp_path, runner_pair=(runner, calls), opener=lambda _url: None,
        listeners=lambda _port: [], port_open=lambda _port: False,
        sleep=clock.sleep, monotonic=clock.monotonic, timeout=60,
    )
    assert out["ok"] is False and out["code"] == "startup_timeout"
    assert out.get("http") is None
    assert ["systemctl", "--user", "start", "gods-eye-view.service"] in recorded


def test_open_socket_without_a_matched_listener_refuses_start(tmp_path):
    spawned = []

    def spawn(*_args):
        spawned.append(True)
        raise AssertionError("spawn")

    out, recorded, killed = _action(
        "start", tmp_path, opener=lambda _url: None, listeners=lambda _port: [],
        port_open=lambda _port: True, spawn=spawn,
    )
    assert out["code"] == "port_in_use" and spawned == [] and killed == []
    assert all(call[2] == "show" for call in recorded)


def test_process_stop_signals_only_the_owned_pid(tmp_path):
    node, checkout = _paths(tmp_path)
    proc = _owned(node, checkout)
    out, _calls, killed = _action(
        "stop", tmp_path, opener=lambda _url: None,
        listeners=lambda _port: [proc], inspect=lambda _pid: proc,
        sleep=lambda _s: None, monotonic=lambda: 0,
    )
    assert out["ok"] is True and out["stopped"] == [42] and out["mode"] == "process"
    assert killed == [(42, signal.SIGTERM)]


def test_process_stop_refuses_a_foreign_listener(tmp_path):
    foreign = {"pid": 9, "exe": "/usr/bin/python", "cwd": "/tmp", "cmdline": ["python"], "port": 4173, "starttime": "1"}
    out, recorded, killed = _action(
        "stop", tmp_path, opener=lambda _url: 200, listeners=lambda _port: [foreign],
        port_open=lambda _port: True,
    )
    assert out["code"] == "port_in_use" and killed == []
    assert all(call[2] == "show" for call in recorded)


def test_stop_refuses_when_http_is_up_and_no_listener_was_identified(tmp_path):
    out, _calls, killed = _action(
        "stop", tmp_path, opener=lambda _url: 200, listeners=lambda _port: [],
        port_open=lambda _port: False,
    )
    assert out["code"] == "port_in_use" and killed == []


def test_starttime_mismatch_does_not_signal(tmp_path):
    node, checkout = _paths(tmp_path)
    proc = _owned(node, checkout, start="111")
    moved = {**proc, "starttime": "222"}
    out, _calls, killed = _action(
        "stop", tmp_path, opener=lambda _url: None, listeners=lambda _port: [proc],
        inspect=lambda _pid: moved, sleep=lambda _s: None, monotonic=lambda: 0,
    )
    assert out["code"] == "stop_failed" and killed == []


def test_process_start_waits_for_http_200_and_hides_child_env(tmp_path, monkeypatch):
    envf = tmp_path / "gods-eye-view.env"
    envf.write_text(f"AISSTREAM_API_KEY={SECRET}\n")
    monkeypatch.setenv("AISSTREAM_API_KEY", SECRET)
    seen = {"n": 0, "child_has_value": False}

    def opener(_url):
        seen["n"] += 1
        return None if seen["n"] == 1 else 200

    def spawn(argv, cwd, env):
        seen["child_has_value"] = env.get("AISSTREAM_API_KEY") == SECRET
        seen["argv"] = argv

        class Proc:
            pid = 77

            def poll(self):
                return None

        return Proc()

    out, _calls, killed = _action(
        "start", tmp_path, opener=opener, listeners=lambda _port: [],
        port_open=lambda _port: False, spawn=spawn, sleep=lambda _s: None, monotonic=lambda: 0,
    )
    assert out["ok"] is True and out["pid"] == 77 and out["http"] == 200 and killed == []
    assert seen["child_has_value"] is True
    assert "vite.js" in " ".join(seen["argv"]) and "--strictPort" in seen["argv"]
    assert SECRET not in json.dumps(out)


def test_process_restart_stops_owned_listener_then_spawns(tmp_path):
    node, checkout = _paths(tmp_path)
    proc = _owned(node, checkout)
    seen = {"n": 0, "spawned": 0}

    def opener(_url):
        seen["n"] += 1
        # Pre-check is up, the stop wait sees it down, the start wait sees HTTP 200.
        return {1: 200, 2: None}.get(seen["n"], 200)

    def spawn(argv, cwd, env):
        seen["spawned"] += 1

        class Proc:
            pid = 88

            def poll(self):
                return None

        return Proc()

    out, _calls, killed = _action(
        "restart", tmp_path, opener=opener, listeners=lambda _port: [proc],
        inspect=lambda _pid: proc, spawn=spawn, port_open=lambda _port: False,
        sleep=lambda _s: None, monotonic=lambda: 0,
    )
    assert out["ok"] is True and out["restarted"] is True and out["pid"] == 88
    assert killed == [(42, signal.SIGTERM)] and seen["spawned"] == 1


def test_systemd_stop_does_not_signal_a_listener(tmp_path):
    node, checkout = _paths(tmp_path)
    proc = _owned(node, checkout)
    seen = {"n": 0}

    def opener(_url):
        seen["n"] += 1
        return 200 if seen["n"] == 1 else None

    runner, calls = _unit(True)
    out, recorded, killed = _action(
        "stop", tmp_path, runner_pair=(runner, calls), opener=opener,
        listeners=lambda _port: [proc], inspect=lambda _pid: proc,
        sleep=lambda _s: None, monotonic=lambda: 0,
    )
    assert out["ok"] is True and out["mode"] == "systemd"
    assert ["systemctl", "--user", "stop", "gods-eye-view.service"] in recorded
    assert killed == []


def test_unsupported_platform_does_not_call_systemd_or_kill(tmp_path):
    def boom(_argv):
        raise AssertionError("systemctl")

    out, _calls, killed = _action("restart", tmp_path, platform="darwin", runner_pair=(boom, []))
    assert out["code"] == "unsupported" and out["mode"] == "unsupported" and killed == []
    win, _c, killed_win = _action("start", tmp_path, platform="win32", runner_pair=(boom, []))
    assert win["code"] == "unsupported" and killed_win == []


def test_busy_lock_and_status_does_not_start():
    assert control_room._begin("start") is True
    busy = control_room.app_action("stop", platform="linux")
    assert busy["code"] == "busy" and busy["busy"] == "start"
    body = _status()
    assert body["busy"] == "start" and body["ok"] is True
    assert all(call[2] == "show" for call in body["_runner"])
    control_room.reset_runtime()
    assert control_room.app_action("nope")["code"] == "bad_action"


def test_relay_records_heartbeat_and_context_loss():
    relay = live.Relay()
    relay.next(wait=0, pane_id="p", sessions=["chat"])
    relay.note_state("p", {"lat": 1, "lon": 2, "rangeM": 1000})
    info = relay.panes["p"].info(__import__("time").monotonic())
    assert info["lastHeartbeatAgeS"] >= 0
    relay.gl_event({"event": "lost", "pane": "p", "kind": "recovering", "sessions": ["chat"]})
    assert relay.panes["p"].context_losses == 1
    again = relay.panes["p"].info(__import__("time").monotonic())
    assert again["contextLosses"] == 1


def test_desktop_and_dashboard_expose_the_control_room():
    plugin = (ROOT / "desktop" / "plugin.js").read_text()
    api = (ROOT / "dashboard" / "plugin_api.py").read_text()
    assert "Control room" in plugin and "ctx.rest('/control')" in plugin
    assert "cygnostik/Hermes-Plugin-Gods-Eye-View" in plugin
    assert '@router.get("/control")' in api and '@router.post("/control/app")' in api
    assert SECRET not in plugin and SECRET not in api
