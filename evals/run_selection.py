#!/usr/bin/env python3
"""Score evals/selection_golden.json against a live chat model.

Presents only godseye_view and godseye_query (the phase A surface). Exits 0
without calling a model when CI or GITHUB_ACTIONS is set, or when neither
OPENAI_API_KEY nor ANTHROPIC_API_KEY is set. The key is read from the
environment and is never printed.

GODSEYE_EVAL_MODEL overrides the default model
(gpt-4o-mini for OpenAI, claude-3-5-haiku-latest for Anthropic).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gev.selection_eval import load_golden, score  # noqa: E402


def _skip(reason: str) -> int:
    print(f"skip: {reason}")
    return 0


def _schemas() -> list[dict]:
    from gev.router import query_schema, view_schema
    return [view_schema(), query_schema()]


def _redact(text: str) -> str:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:2000]


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"model HTTP {exc.code}: {_redact(body)}") from None


def _openai(task: str, schemas: list[dict]) -> tuple[str, dict]:
    tools = [{
        "type": "function",
        "function": {"name": s["name"], "description": s["description"], "parameters": s["parameters"]},
    } for s in schemas]
    data = _post(
        "https://api.openai.com/v1/chat/completions",
        {
            "model": os.environ.get("GODSEYE_EVAL_MODEL") or "gpt-4o-mini",
            "messages": [{"role": "user", "content": task}],
            "tools": tools,
            "tool_choice": "required",
        },
        {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
    )
    calls = ((data.get("choices") or [{}])[0].get("message") or {}).get("tool_calls") or []
    if not calls:
        raise SystemExit("model returned no tool call")
    fn = calls[0].get("function") or {}
    raw = fn.get("arguments") or "{}"
    args = json.loads(raw) if isinstance(raw, str) else raw
    return fn.get("name") or "", args if isinstance(args, dict) else {}


def _anthropic(task: str, schemas: list[dict]) -> tuple[str, dict]:
    tools = [{
        "name": s["name"], "description": s["description"], "input_schema": s["parameters"],
    } for s in schemas]
    data = _post(
        "https://api.anthropic.com/v1/messages",
        {
            "model": os.environ.get("GODSEYE_EVAL_MODEL") or "claude-3-5-haiku-latest",
            "max_tokens": 400,
            "tools": tools,
            "tool_choice": {"type": "any"},
            "messages": [{"role": "user", "content": task}],
        },
        {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            args = block.get("input") if isinstance(block.get("input"), dict) else {}
            return block.get("name") or "", args
    raise SystemExit("model returned no tool_use block")


def main() -> int:
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return _skip("CI is set; the live selection eval does not run in CI")
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not has_openai and not has_anthropic:
        return _skip("no API key (set OPENAI_API_KEY or ANTHROPIC_API_KEY); not calling a model")
    call = _openai if has_openai else _anthropic
    schemas = _schemas()
    failed = 0
    for task in load_golden()["tasks"]:
        tool, args = call(task["task"], schemas)
        err = score(task, tool, args)
        if err:
            failed += 1
            print(f"FAIL {task['id']}: {err} (got {tool} {json.dumps(args, ensure_ascii=False)[:300]})")
        else:
            print(f"ok   {task['id']}: {tool} op={args.get('op')}")
    print(f"{failed} failed of {len(load_golden()['tasks'])}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
