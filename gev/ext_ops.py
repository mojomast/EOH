"""Extension seam for new ops on the eleven composable tools.

A module named ``gev/ext_<name>.py`` exports ``EXT_OPS``: a list of
:class:`ExtOp` records. ``discover`` imports every such module, skips one that
fails to import (the error is kept for the doctor), and refuses a duplicate
``(tool, op)`` pair. The router reads the discovered records for the op enum,
the schema union, and dispatch, so adding an op never edits the router.

``props`` is the argument mapping: property name to JSON Schema property. A
property marked with ``"required": True`` is enforced by the router; the marker
is stripped before the schema is shown to the model.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Callable

_PACKAGE = __name__.rsplit(".", 1)[0] if "." in __name__ else "gev"
_DIR = Path(__file__).resolve().parent
_EXCLUDED = {"ext_ops"}
_ERRORS: dict[str, str] = {}


@dataclass(frozen=True)
class ExtOp:
    tool: str
    op: str
    handler: Callable[..., str]
    props: dict
    blurb: str


def _module_names() -> list[str]:
    names = {
        f"{_PACKAGE}.{path.stem}"
        for path in _DIR.glob("ext_*.py")
        if path.stem not in _EXCLUDED
    }
    prefix = f"{_PACKAGE}.ext_"
    for name in sys.modules:
        if name.startswith(prefix) and name.rsplit(".", 1)[-1] not in _EXCLUDED:
            names.add(name)
    return sorted(names)


def _tool_names() -> set[str]:
    from . import router
    return {target.composable for target in router.CATALOG}


def _existing_ops() -> set[tuple[str, str]]:
    from . import router
    return {(target.composable, target.op) for target in router.CATALOG}


def discover() -> list[ExtOp]:
    """Import every ``gev/ext_*.py`` module and return its ops in name order.

    Raises ``ValueError`` when an op names an unknown tool or duplicates an
    existing or already-discovered ``(tool, op)``. A module that cannot be
    imported is skipped and its error is available from :func:`errors`.
    """
    _ERRORS.clear()
    tools = _tool_names()
    seen = _existing_ops()
    found: list[ExtOp] = []
    for name in _module_names():
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # deliberately broad: one bad module must not hide the others
            _ERRORS[name] = f"{type(exc).__name__}: {exc}"
            continue
        if not isinstance(module, ModuleType):
            _ERRORS[name] = "not a module"
            continue
        records = getattr(module, "EXT_OPS", ())
        if not isinstance(records, (list, tuple)):
            _ERRORS[name] = "EXT_OPS must be a list of ExtOp"
            continue
        for record in records:
            if not isinstance(record, ExtOp):
                _ERRORS[name] = f"EXT_OPS contains {type(record).__name__}, not ExtOp"
                continue
            if record.tool not in tools:
                raise ValueError(
                    f"{record.tool} op={record.op}: not one of the eleven composable tools")
            key = (record.tool, record.op)
            if key in seen:
                raise ValueError(
                    f"{record.tool} op={record.op}: duplicate op (already registered)")
            seen.add(key)
            found.append(record)
    return found


def ops_for(tool: str) -> list[ExtOp]:
    return [record for record in discover() if record.tool == tool]


def find(tool: str, op: str) -> ExtOp | None:
    return next((record for record in discover()
                 if record.tool == tool and record.op == op), None)


def errors() -> dict[str, str]:
    """Import errors from the last discovery, keyed by module name."""
    return dict(_ERRORS)
