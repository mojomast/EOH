"""Pane capture waits for scene.postRender before toDataURL."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def test_panel_live_agent_uses_post_render_capture():
    src = (ROOT / "gev" / "panel_live_agent.js").read_text()
    assert "afterPostRender" in src
    assert "capturedAt: 'postRender'" in src
    assert "await Promise.resolve(capture())" in src
    assert "await Promise.resolve(paneShot())" in src


@pytest.mark.skipif(not shutil.which("node"), reason="node required")
def test_capture_post_render_node_suite():
    r = subprocess.run(
        ["node", "--test", str(ROOT / "gev" / "capture_post_render.test.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
