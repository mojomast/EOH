"""Compare/timeline surface host: helper + inject order."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def test_compare_surface_injected_before_context_and_timeline():
    from gev import panel_inject
    import re

    text = Path(panel_inject.__file__).read_text()
    # Only the head += _script(...) load order matters (filename may appear earlier in docs).
    loads = re.findall(r'_HERE\s*/\s*"([^"]+\.js)"', text)
    assert "compare_surface.js" in loads
    assert loads.index("compare_surface.js") < loads.index("panel_context.js") < loads.index("panel_timeline.js")


def test_panel_files_call_compare_surface_api():
    ctx = (ROOT / "gev" / "panel_context.js").read_text()
    tl = (ROOT / "gev" / "panel_timeline.js").read_text()
    assert "__hermesCompareSurface" in ctx and "acquire" in ctx
    assert "esri-if-photoreal" in (ROOT / "gev" / "compare_surface.js").read_text()
    assert "__hermesCompareSurface" in tl
    # Must not only hide tileset without preparing a globe host.
    assert "hidPhotorealTiles" in ctx  # still reported when lease/fallback hides tiles
    assert "acquireImageryComparison" in (ROOT / "gev" / "compare_surface.js").read_text()
    assert "no_visible_surface" in ctx or "no_visible_surface" in (
        ROOT / "gev" / "compare_surface.js"
    ).read_text()


@pytest.mark.skipif(not shutil.which("node"), reason="node required")
def test_compare_surface_node_suite():
    r = subprocess.run(
        ["node", "--test", str(ROOT / "gev" / "compare_surface.test.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
