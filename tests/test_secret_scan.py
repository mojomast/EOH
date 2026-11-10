"""scripts/secret_scan.py flags credential shapes and never prints the secret."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "scripts" / "secret_scan.py"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCAN), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_secret_scan_clean_on_this_tree():
    proc = _run(ROOT, str(ROOT))
    assert proc.returncode == 0, proc.stderr
    assert "clean" in proc.stdout
    assert proc.stderr == ""


def test_secret_scan_reports_rule_without_value(tmp_path):
    # Synthetic value used only for this test; must not appear in output.
    planted = "AKIA" + "0123456789ABCDEF"
    sample = tmp_path / "leak.txt"
    sample.write_text(f"aws_key = {planted}\n", encoding="utf-8")
    proc = _run(tmp_path, str(tmp_path))
    assert proc.returncode == 1
    combined = proc.stdout + proc.stderr
    assert "aws_access_key_id" in proc.stderr
    assert "leak.txt:1:" in proc.stderr
    assert planted not in combined
    assert "AKIA" not in combined


def test_assignment_token_in_non_test_file(tmp_path):
    planted = "orbit-" + "deadbeef" * 2
    (tmp_path / "config.env").write_text(f"ORBIT_TOKEN={planted}\n", encoding="utf-8")
    proc = _run(tmp_path, str(tmp_path))
    assert proc.returncode == 1
    combined = proc.stdout + proc.stderr
    assert "assignment_token" in proc.stderr
    assert "config.env:1:" in proc.stderr
    assert planted not in combined
