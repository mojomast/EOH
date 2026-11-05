"""godseye_admin op=batch runs several composable calls and returns each result."""
from __future__ import annotations

import contextvars
import json

_IN_BATCH = contextvars.ContextVar("eoh_batch", default=False)
MAX_CALLS = 16
TOOLS = (
    "godseye_view", "godseye_query", "godseye_layers", "godseye_feeds", "godseye_annotate",
    "godseye_nav", "godseye_track", "godseye_scene", "godseye_watch", "godseye_capture",
    "godseye_admin",
)


def run(args: dict | None, session_id: str | None = None, **kw) -> dict:
    if _IN_BATCH.get():
        return {"ok": False, "error": "nested_batch",
                "message": "A batch cannot contain another batch.",
                "fix": "Put the inner calls directly in calls[]."}
    args = args or {}
    calls = args.get("calls")
    if not isinstance(calls, list) or not calls:
        return {"ok": False, "error": "bad_args", "message": "batch needs calls: [{tool, args}, ...].",
                "fix": "Pass up to 16 calls. Each call is {tool, args}. Set stop_on_error to stop at the first failure."}
    if len(calls) > MAX_CALLS:
        return {"ok": False, "error": "bad_args", "message": f"batch accepts at most {MAX_CALLS} calls.",
                "fix": f"Split the work into batches of {MAX_CALLS}."}
    stop = args.get("stop_on_error") is True
    token = _IN_BATCH.set(True)
    results = []
    try:
        from .router import composable_handler
        for index, call in enumerate(calls):
            if not isinstance(call, dict):
                results.append({"index": index, "ok": False, "error": "bad_args",
                                "message": "Each call must be an object with tool and args."})
            else:
                tool = str(call.get("tool") or "")
                inner = call.get("args") if isinstance(call.get("args"), dict) else call.get("arguments")
                inner = dict(inner or {})
                if tool not in TOOLS:
                    results.append({"index": index, "ok": False, "error": "unknown_tool",
                                    "message": "batch tool must be one of the eleven composable tools.",
                                    "fix": "Use godseye_view, godseye_query, godseye_layers, godseye_feeds, "
                                           "godseye_annotate, godseye_nav, godseye_track, godseye_scene, "
                                           "godseye_watch, godseye_capture, or godseye_admin."})
                elif tool == "godseye_admin" and str(inner.get("op") or "") == "batch":
                    results.append({"index": index, "ok": False, "error": "nested_batch",
                                    "message": "A batch cannot contain another batch."})
                else:
                    try:
                        text = composable_handler(tool)(inner, session_id=session_id, **kw)
                        body = json.loads(text) if isinstance(text, str) else text
                    except Exception as exc:
                        body = {"ok": False, "error": "tool_failed", "message": type(exc).__name__}
                    if not isinstance(body, dict):
                        body = {"ok": False, "error": "tool_failed"}
                    results.append({"index": index, "tool": tool, "op": inner.get("op"), **body})
            if stop and results and results[-1].get("ok") is False:
                break
    finally:
        _IN_BATCH.reset(token)
    return {"ok": all(item.get("ok") for item in results), "count": len(results),
            "stopped": bool(stop and results and results[-1].get("ok") is False and len(results) < len(calls)),
            "results": results}
