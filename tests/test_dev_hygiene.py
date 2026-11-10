"""Dev metadata stays consistent: versions, homepage, CI workflow."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin.yaml"
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_pyproject_version_matches_plugin_yaml():
    manifest = PLUGIN.read_text(encoding="utf-8")
    project = PYPROJECT.read_text(encoding="utf-8")
    plugin_version = re.search(r'(?m)^version:\s*"([^"]+)"\s*$', manifest)
    py_version = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', project)
    assert plugin_version and py_version
    assert plugin_version.group(1) == py_version.group(1) == "0.2.9"


def test_ci_workflow_covers_required_gates():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pytest" in text
    assert "node --test" in text
    assert "plugins validate" in text
    assert "secret_scan" in text
    assert "test_plugin_yaml_strict" in text or "tests/" in text


def test_dev_extras_include_test_imports():
    text = PYPROJECT.read_text(encoding="utf-8")
    # CI installs only .[dev]. Collection imports these third-party modules.
    for dep in ("pytest", "ruamel.yaml", "tiktoken", "PyYAML"):
        assert dep in text, f"{dep} must be in the dev extra so CI can collect tests"
