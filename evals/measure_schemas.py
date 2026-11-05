#!/usr/bin/env python3
"""Print Eye Of Hermes tool-schema size before and after the eleven-tool catalog.

"after" is every schema registered for the model (eleven composable tools).

"baseline" rebuilds the pre-composable catalog: phase A names stay
unregistered, phase B names are registered again, and only godseye_view
and godseye_query are added. The slider's absorbed name is one of those
phase B tools. view and query also include
show, state, terrain, and features. Control-room names and street_walk are
phase B, so this reconstruction registers them too. The registered catalog
stays at eleven.
The pull request records the feat/v0.2 measurement separately.

Token counts use tiktoken (o200k_base and cl100k_base). No network. No secrets.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


def _schemas(phase_b_registered: bool) -> list[dict]:
    from gev import hermes_tools, router
    real_absorbed = router.absorbed
    real_comp = router.register_composable

    def absorbed_phase_a(name: str) -> bool:
        target = router.BY_LEGACY.get(name)
        return target is not None and target.phase == "A"

    def view_and_query(ctx) -> None:
        before = set(ctx.tools)
        real_comp(ctx)
        for name in list(ctx.tools):
            if name not in before and name not in {"godseye_view", "godseye_query"}:
                del ctx.tools[name]

    if phase_b_registered:
        router.absorbed = absorbed_phase_a
        router.register_composable = view_and_query
    try:
        ctx = _Ctx()
        hermes_tools.register_tools(ctx)
        return [ctx.tools[name]["schema"] for name in ctx.tools]
    finally:
        router.absorbed = real_absorbed
        router.register_composable = real_comp


def measure() -> dict:
    after = _schemas(phase_b_registered=False)
    baseline = _schemas(phase_b_registered=True)
    return {
        "encoding": "json.dumps separators=(',', ':'), ensure_ascii=False, registration order",
        "tokenizer": "tiktoken o200k_base and cl100k_base",
        "after": _pack(after),
        "baseline": _pack(baseline),
    }


def main() -> int:
    json.dump(measure(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
