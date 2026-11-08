"""plugin.yaml must parse under a strict YAML loader (no duplicate keys)."""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugin.yaml"


def _strict_load(text: str):
    from ruamel.yaml import YAML
    from ruamel.yaml.constructor import DuplicateKeyError

    yaml = YAML(typ="safe")
    yaml.allow_duplicate_keys = False
    try:
        return yaml.load(text), None
    except DuplicateKeyError as exc:
        return None, exc


def test_plugin_yaml_strict_no_duplicate_keys():
    data, err = _strict_load(PLUGIN.read_text(encoding="utf-8"))
    assert err is None, f"duplicate key in plugin.yaml: {err}"
    assert isinstance(data, dict)
    assert data.get("name") == "godseye"
    assert data.get("version") == "0.2.7"
    tools = data.get("provides_tools") or []
    assert len(tools) == 11
    assert len(tools) == len(set(tools))


def test_plugin_yaml_has_single_name_and_version_line():
    text = PLUGIN.read_text(encoding="utf-8")
    assert text.count("\nname:") + (1 if text.startswith("name:") else 0) == 1
    assert sum(1 for line in text.splitlines() if line.startswith("version:")) == 1
    assert not text.lstrip().startswith("name: godseye\nname:")


def test_strict_loader_rejects_duplicate_name():
    bad = textwrap.dedent(
        """\
        name: godseye
        name: godseye
        version: "0.0.0"
        """
    )
    data, err = _strict_load(bad)
    assert data is None and err is not None


def test_bump_script_only_changes_version(tmp_path, monkeypatch):
    script = ROOT / "scripts" / "bump_plugin_version.py"
    # Copy a clean manifest into tmp and bump there by monkeypatching PLUGIN path via cwd copy
    # Run against repo file only after restoring — use a subprocess on a temp copy by
    # invoking the load/bump helpers through a small inline check.
    from importlib.util import module_from_spec, spec_from_loader
    import importlib.machinery

    spec = importlib.util.spec_from_file_location("bump_plugin_version", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Point PLUGIN at a temp copy
    sample = textwrap.dedent(
        """\
        name: godseye
        version: "0.2.4"
        description: test
        author: mojomast
        license: MIT
        homepage: https://example.invalid
        tags: [test]
        manifest_version: 2
        api_version: 1
        provides_hooks:
          - pre_llm_call
        provides_tools:
          - godseye_view
        """
    )
    target = tmp_path / "plugin.yaml"
    target.write_text(sample, encoding="utf-8")
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "PLUGIN", target)
    mod.bump("0.2.5")
    text = target.read_text(encoding="utf-8")
    assert 'version: "0.2.5"' in text
    assert text.count("name: godseye") == 1
    data, err = _strict_load(text)
    assert err is None and data["version"] == "0.2.5"


def test_bump_script_refuses_duplicate_manifest(tmp_path, monkeypatch):
    import importlib.util

    script = ROOT / "scripts" / "bump_plugin_version.py"
    spec = importlib.util.spec_from_file_location("bump_plugin_version", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    target = tmp_path / "plugin.yaml"
    target.write_text("name: godseye\nname: godseye\nversion: \"0.2.4\"\n", encoding="utf-8")
    monkeypatch.setattr(mod, "PLUGIN", target)
    with pytest.raises(SystemExit):
        mod.bump("0.2.5")
