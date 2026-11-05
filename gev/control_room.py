"""EOH Control room: live status and user-triggered God's Eye View lifecycle.

The collapsible room in the desktop pane follows the control-room idea in
cygnostik/Hermes-Plugin-Gods-Eye-View (desktop/plugin.js and
dashboard/plugin_api.py, MIT): explicit start/stop, a busy lock, and a refusal
when the listen port belongs to another process. This module does not copy
that plugin's engine, key bridge, or update flow.

Status reuses ``composer.doctor`` (and the signals doctor already collected).
Start, stop, and restart run only when a caller asks. Linux can use the
``gods-eye-view.service`` user unit from ``scripts/install-service.sh``, or a
plugin-managed Node process. macOS and Windows report lifecycle as unsupported.

A plugin-managed stop signals a process only when the Node executable, the
checkout path, and the listen port all match. Readiness is HTTP 200 on
``GODSEYE_API_BASE``. An open socket is not treated as up. Provider key values
are never returned.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import composer, config

UNIT = "gods-eye-view.service"
OPTIONAL_KEYS = ("AISSTREAM_API_KEY", "FIRMS_MAP_KEY")
_REV = __import__("re").compile(r"^[A-Za-z0-9._+\-]{1,40}$")
_HEX = __import__("re").compile(r"^[0-9a-f]{7,40}$")

_gate = threading.Lock()
_busy: str | None = None
_last_error: dict | None = None


def reset_runtime() -> None:
    """Drop busy state. Tests call this so a failed case cannot hold the lock."""
    global _busy, _last_error
    _busy = None
    _last_error = None
    if _gate.locked():
        try:
            _gate.release()
        except RuntimeError:
            pass


def _begin(action: str) -> bool:
    global _busy
    if not _gate.acquire(blocking=False):
        return False
    _busy = action
    return True


def _finish(error: dict | None = None) -> None:
    global _busy, _last_error
    _busy = None
    if error:
        _last_error = {"code": error.get("code"), "message": error.get("message")}
    else:
        _last_error = None
    try:
        _gate.release()
    except RuntimeError:
        pass


def _fail(code: str, message: str, fix: str, **extra) -> dict:
    return {"ok": False, "code": code, "error": code, "message": message, "fix": fix, **extra}


def _platform(name: str | None = None) -> str:
    raw = name or sys.platform
    if str(raw).startswith("linux"):
        return "linux"
    if raw == "darwin":
        return "darwin"
    if raw in ("win32", "windows", "cygwin"):
        return "windows"
    return str(raw)


def lifecycle_supported(platform: str | None = None) -> bool:
    return _platform(platform) == "linux"


def scrub_obj(value):
    """Walk a status payload and strip credential-shaped text."""
    if isinstance(value, str):
        return composer.scrub_text(value)
    if isinstance(value, list):
        return [scrub_obj(item) for item in value]
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in {"env", "environ", "authorization", "headers"}:
                continue
            out[str(key)] = scrub_obj(item)
        return out
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return composer.scrub_text(str(value))[:160]


def _check(checks: list, name: str) -> dict:
    for item in checks or []:
        if isinstance(item, dict) and item.get("check") == name:
            return item
    return {}


def _fetch_state(layer: dict) -> tuple[str, int | None, str | None]:
    fetch = layer.get("lastFetch") if isinstance(layer.get("lastFetch"), dict) else {}
    age = fetch.get("ageS") if isinstance(fetch.get("ageS"), int) else None
    detail = None
    if fetch.get("ok") is True:
        return "ok", age, layer.get("feedState")
    if fetch.get("ok") is False:
        err = fetch.get("error")
        return "error", age, err if isinstance(err, str) and err else (layer.get("feedState") or "error")
    state = str(layer.get("feedState") or "").lower()
    if state in ("nominal", "partial"):
        return "ok", age, state
    if state in ("unavailable", "error", "failed", "fallback", "degraded", "stale"):
        return "error", age, state
    if state:
        detail = state
    return "unknown", age, detail


def aggregate(report: dict, probe: dict, bridge: dict, life: dict, urls: dict) -> dict:
    """Five live sections from a doctor report. Pure: no processes, no network."""
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    panes = report.get("panes") if isinstance(report.get("panes"), list) else []
    signals = report.get("signals") if isinstance(report.get("signals"), dict) else {}
    app_check = _check(checks, "app reachable")
    http = probe.get("http") if isinstance(probe.get("http"), int) else None
    # The doctor row, or /health, decides up or down. A version/commit probe does not.
    if app_check.get("ok"):
        app_state = "up"
    elif app_check and not app_check.get("ok"):
        app_state = "down"
    elif http == 200:
        app_state = "up"
    elif not app_check and http is None:
        app_state = "unknown"
    else:
        app_state = "down"
    relay_check = _check(checks, "live relay")
    if relay_check.get("ok"):
        relay_state = "up"
    elif relay_check:
        relay_state = "down"
    else:
        relay_state = "unknown"
    pane_check = _check(checks, "pane connected")
    rows = []
    for pane in panes:
        if not isinstance(pane, dict):
            continue
        age = pane.get("lastHeartbeatAgeS")
        kind = "heartbeat" if isinstance(age, (int, float)) and not isinstance(age, bool) else "poll"
        if not isinstance(age, (int, float)) or isinstance(age, bool):
            age = pane.get("lastPollAgeS")
        rows.append({
            "id": str(pane.get("id") or "")[:80],
            "sessions": [str(item)[:80] for item in (pane.get("sessions") or []) if item][:12],
            "open": bool(pane.get("open")),
            "busy": bool(pane.get("busy")),
            "lastHeartbeatS": age if isinstance(age, (int, float)) and not isinstance(age, bool) else None,
            "heartbeat": kind,
        })
    rendering = signals.get("rendering") if isinstance(signals.get("rendering"), dict) else {}
    losses = rendering.get("contextLosses")
    relay_losses = 0
    for pane in panes:
        if isinstance(pane, dict) and isinstance(pane.get("contextLosses"), int):
            relay_losses += pane["contextLosses"]
    if isinstance(losses, int):
        loss_count = max(losses, relay_losses)
    else:
        loss_count = relay_losses
    recover = rendering.get("recover")
    if not isinstance(recover, bool):
        from .gl_recover import enabled as gl_enabled
        recover = gl_enabled()
    webgl = rendering.get("webgl") if rendering.get("webgl") in ("ok", "lost", "unknown") else "unknown"
    hook = rendering.get("recreateViewer") if isinstance(rendering.get("recreateViewer"), bool) else None
    gl_check = _check(checks, "webgl recover")
    missing = []
    for name in OPTIONAL_KEYS:
        item = _check(checks, f"key {name}")
        if item and not item.get("ok"):
            missing.append(name)
    feeds = []
    provenance = signals.get("feedProvenance") if isinstance(signals.get("feedProvenance"), dict) else {}
    for layer in provenance.get("layers") or []:
        if not isinstance(layer, dict) or not layer.get("id"):
            continue
        state, age, detail = _fetch_state(layer)
        feeds.append({"id": str(layer["id"])[:40], "state": state, "ageS": age,
                      "detail": detail if isinstance(detail, str) else None})
    if not feeds:
        summary = _check(checks, "layer feeds")
        if summary:
            feeds.append({
                "id": "layer feeds",
                "state": "ok" if summary.get("ok") else "error",
                "ageS": None,
                "detail": summary.get("detail"),
            })
    return {
        "ok": True,
        "doctorOk": bool(report.get("ok")),
        "lifecycle": life,
        "app": {
            "state": app_state,
            "url": urls.get("apiBase"),
            "publicUrl": urls.get("publicUrl"),
            "http": http,
            "version": probe.get("version"),
            "commit": probe.get("commit"),
            "versionSource": probe.get("versionSource"),
            "commitSource": probe.get("commitSource"),
            "detail": app_check.get("detail"),
        },
        "relay": {
            "state": relay_state,
            "port": probe.get("relayPort"),
            "detail": relay_check.get("detail"),
            "hudBridge": bridge.get("state") or "unknown",
            "hudPort": bridge.get("port"),
        },
        "pane": {
            "state": "up" if pane_check.get("ok") else ("down" if pane_check else "unknown"),
            "connected": sum(1 for row in rows if row["open"]),
            "detail": pane_check.get("detail"),
            "panes": rows,
        },
        "rendering": {
            "webgl": webgl,
            "contextLosses": loss_count,
            "recover": bool(recover),
            "recreateViewer": hook,
            "detail": gl_check.get("detail"),
        },
        "feeds": {"items": feeds, "missingKeys": missing},
    }


def owns_listener(proc: dict, node: str, checkout: str, port: int) -> tuple[bool, str]:
    """True only when the Node executable, the checkout path, and the port all match."""
    try:
        listened = int(proc.get("port"))
    except (TypeError, ValueError):
        return False, "port"
    if listened != int(port):
        return False, "port"
    if not node or _resolved(proc.get("exe") or "") != _resolved(node):
        return False, "exe"
    root = _resolved(checkout) if checkout else ""
    if not root:
        return False, "checkout"
    cwd = _resolved(proc.get("cwd") or "")
    if cwd == root or _cmdline_has_checkout(proc.get("cmdline") or [], root):
        return True, ""
    return False, "checkout"


def plan_stop(listeners: list[dict], node: str, checkout: str, port: int) -> dict:
    """Refuse the whole port when any listener fails the ownership check."""
    if not listeners:
        return {"action": "noop", "pids": []}
    owned = []
    for proc in listeners:
        ok, _why = owns_listener(proc, node, checkout, port)
        if not ok:
            return {"action": "refuse", "pids": [], "code": "port_in_use"}
        owned.append(int(proc["pid"]))
    return {"action": "stop", "pids": owned}


def parse_systemctl_show(text: str) -> dict:
    fields = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip()
    pid_text = fields.get("MainPID") or "0"
    try:
        pid = int(pid_text)
    except ValueError:
        pid = 0
    return {
        "load": fields.get("LoadState") or "",
        "active": fields.get("ActiveState") or "",
        "sub": fields.get("SubState") or "",
        "pid": pid,
        "fragment": fields.get("FragmentPath") or "",
        "loaded": fields.get("LoadState") == "loaded",
    }


def _resolved(path: str | Path) -> str:
    text = str(path or "")
    if not text:
        return ""
    try:
        return str(Path(text).resolve())
    except OSError:
        return os.path.normpath(text)


def _cmdline_has_checkout(cmdline: list, root: str) -> bool:
    prefix = root.rstrip("/") + os.sep
    for part in cmdline:
        text = str(part)
        if not text.startswith("/"):
            continue
        if _resolved(text) == root or text.startswith(prefix) or _resolved(text).startswith(prefix):
            return True
    return False


def _tcp_inodes(text: str, port: int) -> set[str]:
    found = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 10 or ":" not in parts[1]:
            continue
        try:
            listened = int(parts[1].rsplit(":", 1)[-1], 16)
        except ValueError:
            continue
        if listened == int(port) and parts[3] == "0A":
            found.add(parts[9])
    return found


def listeners_from_proc(port: int, proc_root: Path | None = None) -> list[dict]:
    """Listening processes for ``port``, from a /proc tree. Empty when unreadable."""
    root = proc_root or Path("/proc")
    inodes: set[str] = set()
    for name in ("tcp", "tcp6"):
        try:
            inodes |= _tcp_inodes((root / "net" / name).read_text(errors="replace"), port)
        except OSError:
            continue
    if not inodes or not root.is_dir():
        return []
    pids: dict[str, int] = {}
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            fds = list((entry / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:[") and target.endswith("]"):
                inode = target[len("socket:["):-1]
                if inode in inodes:
                    pids.setdefault(inode, int(entry.name))
    out = []
    seen = set()
    for pid in pids.values():
        if pid in seen:
            continue
        seen.add(pid)
        snap = _proc_snapshot(root, pid, port)
        if snap:
            out.append(snap)
    return out


def _proc_snapshot(proc_root: Path, pid: int, port: int) -> dict | None:
    base = proc_root / str(pid)
    if not base.exists():
        return None

    def link(name: str) -> str:
        try:
            return os.readlink(base / name)
        except OSError:
            return ""

    cmdline: list[str] = []
    try:
        raw = (base / "cmdline").read_bytes()
        cmdline = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    except OSError:
        pass
    start = ""
    try:
        stat = (base / "stat").read_text(errors="replace")
        end = stat.rfind(")")
        fields = stat[end + 2:].split() if end >= 0 else []
        if len(fields) > 19:
            start = fields[19]
    except OSError:
        pass
    return {"pid": pid, "exe": link("exe"), "cwd": link("cwd"), "cmdline": cmdline,
            "port": int(port), "starttime": start}


def _safe_rev(value) -> str | None:
    if not isinstance(value, str) or not _REV.fullmatch(value):
        return None
    if __import__("re").search(r"key|secret|token", value, __import__("re").I):
        return None
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: A003
        return None


def fetch_url(url: str, timeout: float = 3, accept: str | None = "application/json") -> tuple[int | None, bytes, str]:
    headers = {"Accept": accept} if accept else {}
    request = urllib.request.Request(url, method="GET", headers=headers)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as resp:
            return getattr(resp, "status", 200), resp.read(8192), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, b"", ""
    except Exception:
        return None, b"", ""


def _json_meta(raw: bytes, content_type: str) -> dict:
    if not raw or len(raw) > 8192:
        return {}
    stripped = raw.lstrip()
    if "json" not in content_type.lower() and not stripped.startswith(b"{"):
        return {}
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _version_fields(data: dict) -> tuple[str | None, str | None]:
    version = _safe_rev(data.get("version"))
    commit = None
    for key in ("commit", "sha", "rev", "gitCommit"):
        commit = _safe_rev(data.get(key))
        if commit and _HEX.fullmatch(commit):
            break
        commit = None
    git = data.get("git") if isinstance(data.get("git"), dict) else {}
    if commit is None:
        commit = _safe_rev(git.get("commit"))
        if commit and not _HEX.fullmatch(commit):
            commit = None
    return version, commit


def checkout_rev(root: Path | None) -> tuple[str | None, str | None]:
    if not root or not Path(root).is_dir():
        return None, None
    version = None
    try:
        data = json.loads((Path(root) / "package.json").read_text(encoding="utf-8"))
        version = _safe_rev(data.get("version")) if isinstance(data, dict) else None
    except (OSError, ValueError):
        version = None
    commit = None
    if (Path(root) / ".git").exists():
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
                capture_output=True, text=True, timeout=3,
            )
            text = (proc.stdout or "").strip()
            if proc.returncode == 0 and _HEX.fullmatch(text):
                commit = text
        except (OSError, subprocess.TimeoutExpired):
            commit = None
    return version, commit


def _split_fetch(got) -> tuple[int | None, bytes, str]:
    if isinstance(got, tuple):
        code = got[0] if got and isinstance(got[0], int) else None
        raw = got[1] if len(got) > 1 and isinstance(got[1], (bytes, bytearray)) else b""
        ctype = got[2] if len(got) > 2 and isinstance(got[2], str) else ""
        return code, raw, ctype
    return (got if isinstance(got, int) else None), b"", ""


def app_http_status(url: str, timeout: float = 2, fetch=None) -> int | None:
    """200 when `/health` or a plain `/` answers. No JSON Accept header.

    God's Eye View returns 404 for `/` when the request asks for JSON. That
    response is not a reason to call the app down. The doctor uses the same
    plain `/` check.
    """
    root = str(url or "").rstrip("/")
    getter = fetch or (lambda target: fetch_url(target, timeout=timeout, accept=None))
    last = None
    for path in ("/health", "/"):
        try:
            code, _raw, _ctype = _split_fetch(getter(root + path))
        except Exception:
            continue
        if code == 200:
            return 200
        if isinstance(code, int):
            last = code
    return last


def probe_app(url: str | None = None, checkout: Path | None = None, fetch=None) -> dict:
    """Reachability from `/health` or plain `/`, plus a best-effort version.

    The version request may send `Accept: application/json`. Its status never
    becomes the up/down signal.
    """
    root = (url or config.api_base()).rstrip("/")

    def getter(target: str, accept: str | None = None):
        if fetch is not None:
            return fetch(target)
        return fetch_url(target, accept=accept)

    health = _split_fetch(getter(root + "/health", accept=None))
    plain = (None, b"", "")
    if health[0] != 200:
        plain = _split_fetch(getter(root + "/", accept=None))
    http = 200 if health[0] == 200 or plain[0] == 200 else (health[0] if health[0] is not None else plain[0])
    version, commit = None, None
    for raw, ctype in ((health[1], health[2]), (plain[1], plain[2])):
        found_version, found_commit = _version_fields(_json_meta(raw, ctype))
        version = version or found_version
        commit = commit or found_commit
    if not version or not commit:
        try:
            _ignored, raw, ctype = _split_fetch(getter(root + "/", accept="application/json"))
        except Exception:
            raw, ctype = b"", ""
        found_version, found_commit = _version_fields(_json_meta(raw, ctype))
        version = version or found_version
        commit = commit or found_commit
    local_version, local_commit = checkout_rev(checkout if checkout is not None else config.checkout())
    return {
        "http": http if isinstance(http, int) else None,
        "version": version or local_version,
        "commit": commit or local_commit,
        "versionSource": "app" if version else ("checkout" if local_version else None),
        "commitSource": "app" if commit else ("git" if local_commit else None),
    }


def hud_bridge_state(fetch=None) -> dict:
    port = int(os.environ.get("GODSEYE_BRIDGE_PORT", "18791"))
    if os.environ.get("GODSEYE_HUD_BRIDGE", "1") == "0":
        return {"state": "off", "port": port}
    from . import bridge
    code = None
    if fetch is not None:
        got = fetch(f"http://127.0.0.1:{port}/health")
        code = got[0] if isinstance(got, tuple) else got
    elif bridge._server is None:
        code, _raw, _ctype = fetch_url(f"http://127.0.0.1:{port}/health", timeout=1.5)
    up = bridge._server is not None or code == 200
    return {"state": "up" if up else "down", "port": port}


def port_of(url: str) -> int:
    parsed = urllib.parse.urlparse(url if "://" in str(url) else "http://" + str(url))
    if parsed.port:
        return int(parsed.port)
    return 443 if parsed.scheme == "https" else 80


def socket_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def default_systemctl(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, type(exc).__name__
    text = composer.scrub_text(((proc.stdout or "") + (proc.stderr or ""))[:500])
    return proc.returncode, text


def _show_unit(runner) -> dict:
    code, text = runner(["systemctl", "--user", "show", UNIT, "-p", "LoadState",
                         "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "FragmentPath"])
    if code not in (0, 1) and "LoadState=" not in text:
        return parse_systemctl_show("")
    return parse_systemctl_show(text)


def lifecycle_view(platform: str | None = None, systemd_runner=None) -> dict:
    plat = _platform(platform)
    if plat != "linux":
        return {
            "supported": False, "platform": plat, "mode": "unsupported", "unit": UNIT,
            "detail": "Start, stop, and restart are supported on Linux. This host shows status only.",
        }
    show = _show_unit(systemd_runner or default_systemctl)
    if show["loaded"]:
        active = show["active"] or "unknown"
        return {"supported": True, "platform": "linux", "mode": "systemd", "unit": UNIT,
                "active": active, "detail": f"{UNIT} is {active}."}
    return {"supported": True, "platform": "linux", "mode": "process", "unit": UNIT, "active": None,
            "detail": "No user systemd unit. A start launches a plugin-managed Node process."}


def status(*, doctor=None, probe=None, bridge=None, platform=None, systemd_runner=None, urls=None) -> dict:
    """Read-only control room. Does not start, stop, or signal a process."""
    try:
        report = doctor() if doctor else composer.doctor()
    except Exception as exc:
        report = {"ok": False, "checks": [], "panes": [], "signals": {},
                  "error": type(exc).__name__}
    if not isinstance(report, dict):
        report = {"ok": False, "checks": [], "panes": [], "signals": {}}
    try:
        probed = probe() if probe else probe_app()
    except Exception:
        probed = {}
    if not isinstance(probed, dict):
        probed = {}
    try:
        from . import live
        probed = {**probed, "relayPort": live.port()}
    except Exception:
        probed = {**probed, "relayPort": None}
    try:
        hud = bridge() if bridge else hud_bridge_state()
    except Exception:
        hud = {"state": "unknown", "port": None}
    if not isinstance(hud, dict):
        hud = {"state": "unknown", "port": None}
    life = lifecycle_view(platform=platform, systemd_runner=systemd_runner)
    if urls is None:
        urls = {"apiBase": config.api_base(), "publicUrl": config.public_url()}
    body = aggregate(report, probed, hud, life, urls)
    body["busy"] = _busy
    body["lastError"] = dict(_last_error) if _last_error else None
    return scrub_obj(body)


def _signal_owned(proc: dict, node: str, checkout: str, port: int, inspect, killer, sig: int) -> bool:
    current = inspect(proc["pid"]) if inspect else proc
    if not isinstance(current, dict):
        return False
    if proc.get("starttime") and current.get("starttime") and current["starttime"] != proc["starttime"]:
        return False
    ok, _why = owns_listener(current, node, checkout, port)
    if not ok:
        return False
    killer(int(current["pid"]), sig)
    return True


def _default_killer(pid: int, sig: int) -> None:
    os.kill(int(pid), sig)


def wait_http(url: str, *, want_up: bool, timeout: float, opener, sleep, monotonic, alive=None) -> dict:
    """Wait until HTTP status is 200, or until it is not. A connect is not success."""
    clock = monotonic or time.monotonic
    deadline = clock() + timeout
    last = None
    while True:
        if alive is not None and not alive():
            return _fail("start_failed", "The Node process exited before HTTP 200.",
                         "Check the God's Eye View checkout (npm ci, Node 24). Upstream output is not logged.")
        try:
            last = opener(url)
        except Exception:
            last = None
        up = last == 200
        if want_up and up:
            return {"ok": True, "http": 200}
        if not want_up and not up:
            return {"ok": True, "http": last}
        if clock() >= deadline:
            break
        sleep(0.4)
    if want_up:
        return _fail("startup_timeout", "The app did not return HTTP 200 before the deadline.",
                     "An open port is not treated as ready. Call godseye_admin op=status.",
                     http=last)
    return _fail("stop_failed", "The app still returned HTTP 200 after the stop attempt.",
                 "Call godseye_admin op=status. No success was assumed.", http=last)


def _dev_argv(node: str, checkout: str, bind: str, port: int) -> list[str] | dict:
    vite = Path(checkout) / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite.is_file():
        return _fail("no_vite", "node_modules/vite/bin/vite.js is not in the checkout.",
                     "Run npm ci in the God's Eye View checkout. Nothing else was started.")
    return [node, str(vite), "--host", bind, "--port", str(port), "--strictPort"]


def _child_env(node: str) -> dict:
    env = dict(os.environ)
    try:
        env.update({k: v for k, v in composer.read_env(composer.env_file()).items() if k and v})
    except Exception:
        pass
    env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    return env


def _spawn_default(argv: list[str], cwd: str, env: dict):
    return subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def _listeners(port: int, kw) -> list[dict]:
    fn = kw.get("listeners")
    if fn is None:
        return listeners_from_proc(port, Path("/proc"))
    return [item for item in (fn(port) or []) if isinstance(item, dict)]


def _port_busy(port: int, bind: str, kw) -> bool:
    fn = kw.get("port_open")
    if fn is not None:
        return bool(fn(port))
    return socket_open(bind, port) or socket_open("127.0.0.1", port)


def _refuse_foreign(port: int) -> dict:
    return _fail(
        "port_in_use",
        f"Port {port} is in use by a process that is not this checkout's Node. Nothing was killed.",
        "Free that port or point GODSEYE_API_BASE at the God's Eye View app. "
        "godseye_admin will not stop an unrelated process.",
    )


def _stop_owned(listeners: list[dict], node: str, checkout: str, port: int, url: str, kw) -> dict:
    plan = plan_stop(listeners, node, checkout, port)
    if plan["action"] == "refuse":
        return _refuse_foreign(port)
    if plan["action"] == "noop":
        if _port_busy(port, kw.get("bind") or "127.0.0.1", kw):
            return _fail(
                "port_in_use",
                f"Port {port} accepts connections, but the listener could not be matched to Node and the checkout. Nothing was killed.",
                "godseye_admin will not stop an unverified process.",
            )
        return {"ok": True, "alreadyStopped": True, "stopped": []}
    inspect = kw.get("inspect") or (lambda pid, _port=port: _proc_snapshot(Path("/proc"), int(pid), _port))
    killer = kw.get("killer") or _default_killer
    stopped = []
    for proc in listeners:
        if _signal_owned(proc, node, checkout, port, inspect, killer, signal.SIGTERM):
            stopped.append(int(proc["pid"]))
    if not stopped:
        return _fail("stop_failed", "Ownership changed before the signal. Nothing was killed.",
                     "Call godseye_admin op=status and retry op=app_stop.")
    sleep = kw.get("sleep") or time.sleep
    clock = kw.get("monotonic") or time.monotonic
    opener = kw.get("opener") or (lambda target: app_http_status(target, timeout=2))
    waited = wait_http(url, want_up=False, timeout=float(kw.get("stop_timeout") or 8),
                       opener=opener, sleep=sleep, monotonic=clock)
    if waited.get("ok"):
        return {"ok": True, "stopped": stopped, "http": waited.get("http")}
    for proc in listeners:
        if int(proc["pid"]) in stopped:
            _signal_owned(proc, node, checkout, port, inspect, killer, signal.SIGKILL)
    waited = wait_http(url, want_up=False, timeout=float(kw.get("stop_timeout") or 4),
                       opener=opener, sleep=sleep, monotonic=clock)
    if waited.get("ok"):
        return {"ok": True, "stopped": stopped, "http": waited.get("http")}
    waited["stopped"] = stopped
    return waited


def _start_process(node: str, checkout: str, port: int, url: str, bind: str, kw) -> dict:
    if not checkout:
        return _fail("no_checkout", "The God's Eye View checkout was not found.",
                     "Clone it to ~/gods-eye-view or set GODSEYE_HOME. Nothing was started.")
    if not node:
        return _fail("no_node", "Node was not found.",
                     "Install Node 24 or set GODSEYE_NODE. Nothing was started.")
    argv = _dev_argv(node, checkout, bind, port)
    if isinstance(argv, dict):
        return argv
    spawn = kw.get("spawn") or _spawn_default
    try:
        proc = spawn(argv, checkout, _child_env(node))
    except OSError:
        return _fail("start_failed", "The Node process could not be launched.",
                     "Check GODSEYE_NODE and the checkout. Nothing else was started.")
    pid = getattr(proc, "pid", None)
    sleep = kw.get("sleep") or time.sleep
    clock = kw.get("monotonic") or time.monotonic
    opener = kw.get("opener") or (lambda target: app_http_status(target, timeout=2))

    def alive() -> bool:
        poll = getattr(proc, "poll", None)
        if not callable(poll):
            return True
        try:
            return poll() is None
        except Exception:
            return True

    waited = wait_http(url, want_up=True, timeout=float(kw.get("timeout") or 60),
                       opener=opener, sleep=sleep, monotonic=clock, alive=alive)
    if not waited.get("ok"):
        waited["pid"] = pid
        return waited
    return {"ok": True, "mode": "process", "pid": pid, "http": 200}


def _systemd(action: str, runner) -> dict:
    argv = ["systemctl", "--user", action, UNIT]
    code, text = runner(argv)
    if code != 0:
        detail = composer.scrub_text(text or "").strip().splitlines()
        tail = detail[-1][:180] if detail else f"exit {code}"
        return _fail("start_failed" if action != "stop" else "stop_failed",
                     f"systemctl --user {action} {UNIT} failed ({tail}).",
                     "Call godseye_admin op=status. The plugin did not signal another process.")
    return {"ok": True, "mode": "systemd", "unit": UNIT}


def _dispatch(action: str, **kw) -> dict:
    platform = kw.get("platform")
    if not lifecycle_supported(platform):
        plat = _platform(platform)
        return _fail(
            "unsupported",
            f"Start, stop, and restart are not supported on {plat}. Status is still available.",
            "On macOS or Windows, start God's Eye View yourself (npm run dev) or use Linux with the user systemd unit.",
            platform=plat, mode="unsupported",
        )
    node, checkout, port, url, bind = _runtime_paths(kw)
    runner = kw.get("systemd_runner") or default_systemctl
    show = _show_unit(runner)
    listeners = _listeners(port, kw)
    opener = kw.get("opener") or (lambda target: app_http_status(target, timeout=2))
    try:
        http = opener(url)
    except Exception:
        http = None
    foreign = plan_stop(listeners, node, checkout, port)
    if not listeners and _port_busy(port, bind, kw):
        foreign = {"action": "refuse"}
    if foreign["action"] == "refuse":
        return _refuse_foreign(port)
    if show["loaded"]:
        return _dispatch_systemd(action, http, runner, url, kw, opener)
    return _dispatch_process(action, http, listeners, node, checkout, port, url, bind, kw)


def _runtime_paths(kw) -> tuple[str, str, int, str, str]:
    node = kw["node"] if "node" in kw else config.node_bin()
    checkout = kw["checkout"] if "checkout" in kw else config.checkout()
    url = kw.get("url") or (config.api_base().rstrip("/") + "/")
    port = int(kw["port"]) if kw.get("port") is not None else port_of(url)
    bind = kw.get("bind") or os.environ.get("GODSEYE_BIND") or "127.0.0.1"
    return str(node or ""), str(checkout or ""), port, url, bind


def _dispatch_systemd(action: str, http, runner, url: str, kw, opener) -> dict:
    sleep = kw.get("sleep") or time.sleep
    clock = kw.get("monotonic") or time.monotonic
    timeout = float(kw.get("timeout") or 60)
    if action == "start" and http == 200:
        return {"ok": True, "alreadyRunning": True, "mode": "systemd", "unit": UNIT, "http": 200}
    if action == "stop" and http != 200:
        stopped = _systemd("stop", runner)
        if not stopped.get("ok"):
            return stopped
        return {"ok": True, "alreadyStopped": True, "mode": "systemd", "unit": UNIT, "http": http}
    verb = {"start": "start", "stop": "stop", "restart": "restart"}[action]
    ran = _systemd(verb, runner)
    if not ran.get("ok"):
        return ran
    if action == "stop":
        waited = wait_http(url, want_up=False, timeout=float(kw.get("stop_timeout") or 15),
                           opener=opener, sleep=sleep, monotonic=clock)
    else:
        waited = wait_http(url, want_up=True, timeout=timeout, opener=opener, sleep=sleep, monotonic=clock)
    if not waited.get("ok"):
        waited["mode"] = "systemd"
        waited["unit"] = UNIT
        return waited
    return {"ok": True, "mode": "systemd", "unit": UNIT, "action": action, "http": waited.get("http")}


def _dispatch_process(action, http, listeners, node, checkout, port, url, bind, kw) -> dict:
    if action in ("stop", "restart") and http == 200 and not listeners:
        return _refuse_foreign(port)
    if action == "start":
        if http == 200:
            return {"ok": True, "alreadyRunning": True, "mode": "process", "http": 200}
        if listeners:
            return _fail("not_ready",
                         "The owned app is on the port but did not return HTTP 200. Nothing new was started.",
                         "Call godseye_admin op=app_restart, or op=app_stop and then op=app_start.")
        return _start_process(node, checkout, port, url, bind, kw)
    if action == "stop":
        return {**_stop_owned(listeners, node, checkout, port, url, kw), "mode": "process"}
    stopped = _stop_owned(listeners, node, checkout, port, url, kw) if listeners or http == 200 else {"ok": True, "stopped": []}
    if not stopped.get("ok"):
        stopped["mode"] = "process"
        return stopped
    started = _start_process(node, checkout, port, url, bind, kw)
    if started.get("ok"):
        started["restarted"] = True
    return started


def app_action(action: str, **kw) -> dict:
    """Start, stop, or restart. No-ops for status. Refuses while another action holds the lock."""
    if action not in ("start", "stop", "restart"):
        return _fail("bad_action", "action must be start, stop, or restart.",
                     "Call godseye_admin op=app_start, op=app_stop, or op=app_restart.")
    if not _begin(action):
        return _fail("busy", f"Control room is busy ({_busy or 'working'}).",
                     "Wait, then call godseye_admin op=status. Do not start a second copy.",
                     busy=_busy)
    result: dict | None = None
    try:
        result = _dispatch(action, **kw)
        return scrub_obj(result)
    except Exception:
        result = _fail("tool_failed", f"Control room {action} failed.",
                       "Call godseye_admin op=status. Nothing was assumed stopped or started.")
        return result
    finally:
        err = result if isinstance(result, dict) and result.get("ok") is False else None
        _finish(err)
