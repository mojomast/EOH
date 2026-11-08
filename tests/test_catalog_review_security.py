"""Regression coverage for teknium1's catalog review, without live networking."""
import json
import socket
from pathlib import Path

import pytest

from gev import bridge, composer, ext_director, gl_recover, overlays, poi, preview, providers, public_http, records, research, router, runtime_config, security, watch


def _dns(monkeypatch, addresses):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [
        (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
        for ip in addresses])


@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "192.168.1.1", "172.16.1.1", "100.64.0.1", "224.0.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "ff02::1", "::ffff:127.0.0.1"])
def test_reject_non_public_resolved_addresses(monkeypatch, ip):
    _dns(monkeypatch, [ip])
    with pytest.raises(public_http.UnsafeURL):
        public_http.resolve_public("https://venue.example/")


def test_mixed_dns_is_rejected(monkeypatch):
    _dns(monkeypatch, ["1.1.1.1", "127.0.0.1"])
    with pytest.raises(public_http.UnsafeURL):
        public_http.resolve_public("https://venue.example/")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.org/", "http://user:pass@example.org/", "http://[fe80::1%25eth0]/"])
def test_reject_bad_url_forms(url):
    with pytest.raises(public_http.UnsafeURL):
        public_http.resolve_public(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/"])
def test_pin_links_never_reach_connection(monkeypatch, url):
    _dns(monkeypatch, [url.split('/')[2]])
    research._mem.clear()
    monkeypatch.setattr(research, "cache_get", lambda k: None)
    monkeypatch.setattr(research, "cache_set", lambda *a: None)
    monkeypatch.setattr(public_http, "_connection", lambda *a: pytest.fail("unsafe pin was fetched"))
    assert research.website_meta(url)["ok"] is False


def test_venue_link_never_reaches_connection(monkeypatch):
    _dns(monkeypatch, ["169.254.169.254"])
    monkeypatch.setattr(poi, "nominatim", lambda *a: [{"lat": "1", "lon": "2", "extratags": {"website": "http://169.254.169.254/"}}])
    monkeypatch.setattr(public_http, "_connection", lambda *a: pytest.fail("unsafe venue was fetched"))
    out = poi.poi_details({"name": "Venue"})
    assert out["prices"] == [] and out["notes"]


class _Response:
    def __init__(self, status, location=None):
        self.status, self.location = status, location
    def getheader(self, key):
        return self.location
    def read(self, limit):
        return b"public page"


class _Conn:
    def __init__(self, response):
        self.response, self.closed = response, False
    def request(self, *a, **kw):
        pass
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True


def test_redirect_to_private_is_not_connected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1" if host == "private.example" else "1.1.1.1", 80))])
    seen = []
    def connect(parts, addresses, timeout):
        seen.append(parts.hostname)
        return _Conn(_Response(302, "http://private.example/"))
    monkeypatch.setattr(public_http, "_connection", connect)
    with pytest.raises(public_http.UnsafeURL):
        public_http.get("https://public.example/")
    assert seen == ["public.example"]


def test_relative_public_redirect_and_loop_bound(monkeypatch):
    _dns(monkeypatch, ["1.1.1.1"])
    conns = [_Conn(_Response(302, "/menu")), _Conn(_Response(200))]
    made = []
    def connect(*a):
        conn = conns[len(made)]
        made.append(conn)
        return conn
    monkeypatch.setattr(public_http, "_connection", connect)
    assert public_http.get("https://public.example/") == (200, "public page")
    assert all(c.closed for c in made)
    monkeypatch.setattr(public_http, "_connection", lambda *a: _Conn(_Response(302, "/loop")))
    with pytest.raises(public_http.UnsafeURL):
        public_http.get("https://public.example/", max_redirects=1)


def test_connection_pins_resolved_address_without_second_lookup(monkeypatch):
    _dns(monkeypatch, ["1.1.1.1"])
    parts, addresses = public_http.resolve_public("https://public.example/")
    conn = public_http._connection(parts, addresses, 3)
    seen = []
    class Sock:
        def settimeout(self, value):
            pass
        def connect(self, address):
            seen.append(address)
    monkeypatch.setattr(socket, "socket", lambda *a: Sock())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: pytest.fail("DNS rebind lookup"))
    conn._create_connection(("public.example", 443))
    assert seen == [("1.1.1.1", 443)]
    assert conn.host == "public.example"  # preserved for TLS SNI and certificate verification


def test_all_credentials_use_host_scope_not_dotenv_or_process(tmp_path, monkeypatch):
    from agent import secret_scope
    for name in security.SECRET_NAMES:
        monkeypatch.setenv(name, "other-profile-value")
    monkeypatch.setattr(secret_scope, "get_secret", lambda name, default=None: "active-profile-value" if name in security.SECRET_NAMES else default)
    fake = tmp_path / ".env"
    fake.write_text("FIRMS_MAP_KEY=forbidden-file-value\n")
    assert watch.key_value("FIRMS_MAP_KEY") == "active-profile-value"
    assert providers._key("TOMTOM_API_KEY", {"TOMTOM_API_KEY": "forbidden-file-value"}) == "active-profile-value"
    assert set(runtime_config.merged_env(tmp_path, fake).values()) == {"active-profile-value"}
    assert composer.read_env(fake) == {}
    assert composer._env_has({}, "FIRMS_MAP_KEY") == (True, "active Hermes profile")


def test_secret_scope_errors_are_not_bypassed(monkeypatch):
    from agent import secret_scope
    def unscoped(*a):
        raise RuntimeError("missing scope")
    monkeypatch.setattr(secret_scope, "get_secret", unscoped)
    monkeypatch.setenv("FIRMS_MAP_KEY", "must-not-fallback")
    with pytest.raises(RuntimeError, match="missing scope"):
        security.secret("FIRMS_MAP_KEY")


def test_all_used_keys_declared():
    import yaml
    manifest = yaml.safe_load((Path(__file__).parents[1] / "plugin.yaml").read_text())
    assert set(security.SECRET_NAMES) == {row["name"] for row in manifest["requires_env"]}


def test_minimal_children_exclude_unrelated_secrets_and_use_active_scope(monkeypatch):
    from agent import secret_scope
    monkeypatch.setattr(secret_scope, "get_secret", lambda name, default=None: "scoped-app-value" if name == "FIRMS_MAP_KEY" else default)
    monkeypatch.setenv("OPENROUTER_API_KEY", "host-only-value")
    monkeypatch.setenv("FIRMS_MAP_KEY", "wrong-profile-value")
    monkeypatch.setenv("NODE_OPTIONS", "--require=/untrusted/startup.js")
    monkeypatch.setenv("NODE_AUTH_TOKEN", "host-only-value")
    monkeypatch.setenv("GEV_OTHER_TOKEN", "host-only-value")
    env = security.child_env("/node/bin/node", app_keys=("FIRMS_MAP_KEY",))
    assert env["FIRMS_MAP_KEY"] == "scoped-app-value"
    assert env["PATH"].startswith("/node/bin:")
    assert not {"OPENROUTER_API_KEY", "NODE_OPTIONS", "NODE_AUTH_TOKEN", "GEV_OTHER_TOKEN"} & env.keys()
    assert "FIRMS_MAP_KEY" not in security.child_env()


def test_profile_home_comes_from_hermes_context(tmp_path, monkeypatch):
    import hermes_constants
    profile = tmp_path / "routed-profile"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "wrong-home"))
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: profile)
    for fn in (bridge.data_dir, research.data_dir, security.plugin_data):
        assert fn() == profile / "plugin-data" / "godseye"
    assert watch.default_path() == profile / "plugin-data/godseye/watches.json"
    assert ext_director._store_path() == profile / "plugin-data/godseye/director.json"
    assert gl_recover.config_path() == profile / "plugin-data/godseye/config.json"


def test_preview_never_imports_private_host_modules():
    assert "from tools import" not in Path(preview.__file__).read_text()
    preview.clear()
    assert preview.swap("https://example.org")["desktop_preview_steps"][0]["action"] == "open"
    assert "keys" not in router.generic_schema("godseye_admin")["parameters"]["properties"]["op"]["enum"]


def test_imports_require_plugin_data_or_host_approved_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("GODSEYE_IMPORT_ROOT", raising=False)
    root = security.plugin_data()
    root.mkdir(parents=True)
    allowed = root / "pins.json"
    allowed.write_text('[{"lat":1,"lon":2}]')
    assert records.load_path("pins.json")[0]
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    with pytest.raises(ValueError, match="must stay under"):
        records.load_path(str(outside))
    with pytest.raises(ValueError):
        overlays.import_path(str(outside))
    (root / "escape.json").symlink_to(outside)
    with pytest.raises(ValueError):
        records.load_path("escape.json")
    with pytest.raises(ValueError):
        security.import_file("../../../outside.json")
    approved = tmp_path / "user-folder"
    approved.mkdir()
    overlay = approved / "overlay.json"
    overlay.write_text("{}")
    monkeypatch.setenv("GODSEYE_IMPORT_ROOT", str(approved))
    assert overlays.import_path(str(overlay)) == overlay
    (approved / "escape.json").symlink_to(outside)
    with pytest.raises(ValueError):
        overlays.import_path(str(approved / "escape.json"))


def test_real_hermes_dotenv_scope_api_compatibility(tmp_path, monkeypatch):
    """Synthetic .env through Hermes's own loader; never touch a user's .env."""
    from agent import secret_scope
    if not hasattr(secret_scope, "load_env_file"):
        pytest.skip("Hermes source required for integration contract")
    envf = tmp_path / ".env"
    envf.write_text("FIRMS_MAP_KEY=synthetic-profile-value\n")
    monkeypatch.setenv("FIRMS_MAP_KEY", "synthetic-wrong-process-value")
    token = secret_scope.set_secret_scope(secret_scope.load_env_file(envf), profile_home=str(tmp_path))
    try:
        assert watch.key_value("FIRMS_MAP_KEY") == "synthetic-profile-value"
        assert security.child_env(app_keys=("FIRMS_MAP_KEY",))["FIRMS_MAP_KEY"] == "synthetic-profile-value"
    finally:
        secret_scope.reset_secret_scope(token)
