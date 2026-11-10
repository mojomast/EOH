#!/usr/bin/env python3
"""Scan the plugin tree for committed credentials. Prints path:line:rule only.

Never prints the matching text. Exit 1 if any rule hits. Used by CI and before
every push. This is a heuristic preflight, not a proof of absence.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# High-signal credential shapes only. Keep the match off stdout/stderr.
RULES: list[tuple[str, re.Pattern[str]]] = [
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("openrouter_key", re.compile(r"\bsk-or-[A-Za-z0-9\-_]{16,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{16,}\b")),
    ("openai_like_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("assignment_token", re.compile(
        r"\b(?:ORBIT_TOKEN|OPENROUTER_API_KEY|GOOGLE_MAPS_API_KEY|GOOGLE_API_KEY|"
        r"CESIUM_ION_TOKEN|FIRMS_MAP_KEY|AISSTREAM_API_KEY|TOMTOM_API_KEY|"
        r"MAPILLARY_CLIENT_TOKEN|GITHUB_TOKEN|OPENAI_API_KEY)\s*[:=]\s*"
        r"""['"]?(?=[^\s'"]{8,})(?!your[_-]|changeme|xxx|placeholder|example|"""
        r"""redacted|dummy|fake|test|not-real|host-only|scoped-app|synthetic)"""
        r"""[^\s'"]+"""
    , re.I)),
]

SKIP_DIR_NAMES = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ci", "dist", "build", ".eggs", "*.egg-info",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".woff", ".woff2",
    ".ttf", ".eot", ".mp4", ".webm", ".zip", ".gz", ".pyc", ".lock",
}

# Obvious fixtures / documentation examples that must not trip the scanner.
# Keys are repo-relative POSIX paths; values are rule names allowed on those lines.
ALLOW = {
    # "sk-test-not-real" is shorter than the openai_like_key rule; no allow needed.
}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if path.name in {"package-lock.json"}:
            continue
        yield path


def scan(root: Path) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(text.splitlines(), 1):
            for name, pattern in RULES:
                if pattern.search(line):
                    allowed = ALLOW.get(rel, ())
                    if name in allowed or "*" in allowed:
                        continue
                    # Tests plant dotenv-style KEY=dummy lines; shape rules still apply.
                    if name == "assignment_token" and (rel.startswith("tests/") or "/test_" in rel):
                        continue
                    hits.append((rel, i, name))
                    break
    return hits


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=str(ROOT), help="directory to scan")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    hits = scan(root)
    for rel, line, rule in hits:
        # Path, line, and rule only — never the matching text.
        print(f"{rel}:{line}: {rule}", file=sys.stderr)
    if hits:
        print(f"secret_scan: {len(hits)} finding(s). Values are not printed.", file=sys.stderr)
        return 1
    print("secret_scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
