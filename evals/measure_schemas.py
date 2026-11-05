#!/usr/bin/env python3
"""Print Eye Of Hermes tool-schema size before and after phase A.

"after" is every schema registered now (legacy aliases plus godseye_view and
godseye_query). "baseline" rebuilds the pre-change catalog by dropping those
two tools and stripping the ``deprecated: use godseye_view op=...`` prefix.
Token counts use tiktoken when it is installed (o200k_base and cl100k_base).
No network. No secrets.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NOTE = re.compile(r"^deprecated: use godseye_(?:view|query) op=[a-z0-9_]+\. ")
NEW = {"godseye_view", "godseye_query"}


def _compact(schemas: list[dict]) -> str:
    return json.dumps(schemas, separators=(",", ":"), ensure_ascii=False)


def _tokens(text: str) -> dict:
    try:
        import tiktoken
    except ImportError:
        return {}
    out = {}
    for name in ("o200k_base", "cl100k_base"):
        out[name] = len(tiktoken.get_encoding(name).encode(text))
    return out


def _pack(schemas: list[dict]) -> dict:
    text = _compact(schemas)
    return {"tools": len(schemas), "bytes": len(text.encode()), "tokens": _tokens(text)}


def collect():
    from gev import hermes_tools, router

    class Ctx:
        def __init__(self):
            self.tools = {}

        def register_tool(self, **kw):
            self.tools[kw["name"]] = kw

        def register_system_prompt_section(self, *args, **kwargs):
            pass

        def register_hook(self, *args, **kwargs):
            pass

    ctx = Ctx()
    hermes_tools.register_tools(ctx)
    ordered = [ctx.tools[name]["schema"] for name in ctx.tools]
    phase_a = {t.legacy for t in router.CATALOG if t.phase == "A"}
    return ordered, phase_a


def measure() -> dict:
    ordered, phase_a = collect()
    after = list(ordered)
    baseline = []
    phase_a_legacy = []
    phase_a_stripped = []
    for schema in ordered:
        if schema["name"] in NEW:
            continue
        baseline.append(schema)
        if schema["name"] in phase_a:
            phase_a_legacy.append(schema)
            stripped = dict(schema)
            stripped["description"] = NOTE.sub("", schema["description"], count=1)
            phase_a_stripped.append(stripped)
            baseline[-1] = stripped
    composable = [schema for schema in ordered if schema["name"] in NEW]
    return {
        "encoding": "json.dumps separators=(',', ':'), ensure_ascii=False, registration order",
        "tokenizer": "tiktoken o200k_base and cl100k_base, or omitted when tiktoken is not installed",
        "after": _pack(after),
        "baseline": _pack(baseline),
        "composable_only": _pack(composable),
        "phase_a_legacy_with_notes": _pack(phase_a_legacy),
        "phase_a_legacy_without_notes": _pack(phase_a_stripped),
    }


def main() -> int:
    json.dump(measure(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
