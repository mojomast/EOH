"""HUD summary through Hermes inference, using upstream's own prompt builders."""
from __future__ import annotations

import json
import subprocess

from .security import child_env
from pathlib import Path

from . import config

FEED_STATES = {"nominal", "loading", "degraded", "stale", "partial", "fallback", "unavailable", "off"}
MAX_CHARS = 120
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "hud_prompt.mjs"


def build_prompt(context: dict) -> dict:
    node, home = config.node_bin(), config.checkout()
    if not node or not home:
        raise RuntimeError("God's Eye View checkout or Node 24 not found")
    out = subprocess.run([node, str(_SCRIPT), str(home)], input=json.dumps(context),
                         env=child_env(node), capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


def parse_reply(text: str) -> dict:
    try:
        reply = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except ValueError:
        return {"summary": None, "feedState": None}
    summary = " ".join(str(reply.get("summary") or "").split())
    if len(summary) > MAX_CHARS:
        summary = summary[:MAX_CHARS].rsplit(" ", 1)[0]
    state = reply.get("feedState")
    return {"summary": summary or None, "feedState": state if state in FEED_STATES else None}


def summarize(llm, context: dict) -> dict:
    prompt = build_prompt(context)
    result = llm.complete(
        messages=[{"role": "system", "content": prompt["instructions"] +
                   "\nReply with JSON only: {\"summary\": string, \"feedState\": string}."},
                  {"role": "user", "content": prompt["input"] if isinstance(prompt["input"], str)
                   else json.dumps(prompt["input"])}],
        max_tokens=200, temperature=0.2, purpose="godseye.hud-summary")
    return parse_reply(result.text or "")
