"""Offline scoring for the fixed Eye Of Hermes selection golden set.

The live runner is ``evals/run_selection.py``. This module only loads the file
and checks a tool call against the expected tool, op, and argument shape.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = ROOT / "evals" / "selection_golden.json"


def load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text())


def score(task: dict, tool: str, arguments) -> str | None:
    """Return an error string, or None when the call matches the task."""
    expect = task["expect"]
    if tool != expect["tool"]:
        return f"tool: got {tool!r}, expected {expect['tool']!r}"
    if not isinstance(arguments, dict):
        return "arguments are not an object"
    if arguments.get("op") != expect["op"]:
        return f"op: got {arguments.get('op')!r}, expected {expect['op']!r}"
    shape = expect.get("shape") or {}
    for key in shape.get("required") or []:
        if key not in arguments:
            return f"missing {key}"
    for key, rule in (shape.get("fields") or {}).items():
        if key not in arguments:
            continue
        err = _match(key, arguments[key], rule)
        if err:
            return err
    one_of = shape.get("one_of") or []
    if one_of and not any(_group_ok(arguments, group) for group in one_of):
        return "arguments matched none of one_of"
    return None


def _group_ok(arguments: dict, group: dict) -> bool:
    if not isinstance(group, dict):
        return False
    for key, rule in group.items():
        if key not in arguments:
            return False
        if _match(key, arguments[key], rule):
            return False
    return True


def _match(key: str, value, rule: dict) -> str | None:
    if not isinstance(rule, dict):
        return f"{key}: bad rule"
    if "enum" in rule and value not in rule["enum"]:
        return f"{key}: {value!r} not in {rule['enum']}"
    if "equals" in rule and value != rule["equals"]:
        return f"{key}: got {value!r}"
    kind = rule.get("type")
    if kind and not _type_ok(kind, value):
        return f"{key}: expected {kind}"
    if "contains" in rule:
        if not isinstance(value, str) or rule["contains"].lower() not in value.lower():
            return f"{key}: expected it to contain {rule['contains']!r}"
    if "contains_item" in rule:
        if not isinstance(value, list) or rule["contains_item"] not in value:
            return f"{key}: expected {rule['contains_item']!r} in the list"
    if "subset" in rule:
        if not isinstance(value, dict):
            return f"{key}: expected an object"
        for sub, expected in rule["subset"].items():
            if value.get(sub) != expected:
                return f"{key}.{sub}: got {value.get(sub)!r}"
    return None


def _type_ok(kind: str, value) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True
