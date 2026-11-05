#!/usr/bin/env python3
"""Print Eye Of Hermes tool-schema size before and after dropping phase A names.

"after" is every schema registered for the model (the 18 absorbed names are
absent; godseye_view and godseye_query are present).

"baseline" rebuilds the catalog those two tools replace: the absorbed names
are registered again, and godseye_view / godseye_query are not. That is the
same-tree stand-in for the pre-shrink catalog. Token counts use tiktoken
(o200k_base and cl100k_base). No network. No secrets.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NEW = {"godseye_view", "godseye_query"}


def _compact(schemas: list[dict]) -> str:
    return json.dumps(schemas, separators=(",", ":"), ensure_ascii=False)


def _tokens(text: str) -> dict:
    import tiktoken
    out = {}
    for name in ("o200k_base", "cl100k_base"):
        out[name] = len(tiktoken.get_encoding(name).encode(text))
    return out


def _pack(schemas: list[dict]) -> dict:
    text = _compact(schemas)
    return {"tools": len(schemas), "bytes": len(text.encode()), "tokens": _tokens(text)}


class _Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, **kw):
        self.tools[kw["name"]] = kw

    def register_system_prompt_section(self, *args, **kwargs):
        pass

    def register_hook(self, *args, **kwargs):
        pass


def _schemas(include_absorbed: bool, composable: bool) -> list[dict]:
    from gev import hermes_tools, router
    real_absorbed = router.absorbed
    real_comp = router.register_composable
    if include_absorbed:
        router.absorbed = lambda name: False
    if not composable:
        router.register_composable = lambda ctx: None
    try:
        ctx = _Ctx()
        hermes_tools.register_tools(ctx)
        return [ctx.tools[name]["schema"] for name in ctx.tools]
    finally:
        router.absorbed = real_absorbed
        router.register_composable = real_comp


def measure() -> dict:
    from gev import router
    after = _schemas(include_absorbed=False, composable=True)
    baseline = _schemas(include_absorbed=True, composable=False)
    phase_a = {t.legacy for t in router.CATALOG if t.phase == "A"}
    phase_a_legacy = [schema for schema in baseline if schema["name"] in phase_a]
    composable = [schema for schema in after if schema["name"] in NEW]
    return {
        "encoding": "json.dumps separators=(',', ':'), ensure_ascii=False, registration order",
        "tokenizer": "tiktoken o200k_base and cl100k_base",
        "after": _pack(after),
        "baseline": _pack(baseline),
        "composable_only": _pack(composable),
        "phase_a_legacy": _pack(phase_a_legacy),
    }


def main() -> int:
    json.dump(measure(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
