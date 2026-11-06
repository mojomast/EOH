"""Overlay registry for the Hermes sidebar.

One tree covers the marks God's Eye View is already storing separately:

- scene annotations (pins, cards, labels, routes, areas, arrows)
- scene shapes (measured lines, polygons, boxes, isochrone rings)
- area watches
- saved investigation scenes

The bottom-middle pin chip strip only toggled pin categories. This registry is
what the Overlays sidebar and ``godseye_annotate`` ``op=overlays`` read and write.
``op=filter`` still sets the same category, rating, and open-now filters.

Export document (``eoh.overlays.v1``), JSON, round-trip safe::

    {
      "schema": "eoh.overlays.v1",
      "scene": "<session key>",
      "filters": {"categories": [], "min_rating": null, "open_now": false,
                  "include_unknown": true, "cluster": true},
      "annotations": [{"id": "a1", "paneId": "anno-1", "spec": { ... }}],
      "shapes": [{"id": "shape-1", "action": "add", "kind": "line", "points": [],
                  "label": "", "visible": true}],
      "watches": [{"id": "w-0123abcd", "name": "", "area": {}, "feeds": [],
                   "active": true}],
      "scenes": [{"name": "Harbor", "title": "", "notes": "", "visible": true}]
    }

``replace`` on import replaces annotations and shapes for the current scene.
Watches and saved scenes are upserted by id and are not removed when the file
omits them, unless ``purge`` and ``confirm`` are both true. Writes stay under
``plugin-data/godseye/overlays/``. A missing ``visible`` means shown. Paths and
secret-like keys are not part of the document.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

SCHEMA = "eoh.overlays.v1"
MIGRATION = (
    "The bottom pin-category strip was retired. Category, rating, and open-now "
    "filters are in the Overlays sidebar. godseye_annotate op=filter still sets them."
)
KINDS = ("pins", "routes", "areas", "watches", "scenes")
KIND_LABELS = {
    "pins": "Pins",
    "routes": "Routes, trails, and lines",
    "areas": "Areas",
    "watches": "Watches",
    "scenes": "Scenes",
}
_PIN_TYPES = {"pin", "marker", "point", "highlight", "label", "card"}
_ROUTE_TYPES = {"route", "trail", "line", "linestring", "polyline", "path", "track", "arrow"}
_AREA_TYPES = {"area", "polygon", "circle", "isochrone", "geofence", "box", "rectangle"}
_CAT_KEYS = ("category", "amenity", "cuisine", "shop", "tourism", "leisure")
_SECRET_KEY = re.compile(r"(api[_-]?key|secret|password|token|authorization|cookie)", re.I)
_ASSIGN = re.compile(r"(?i)(api[_-]?key|secret|password|token|authorization|cookie)\s*[=:]\s*\S+")
_WATCH_ID = re.compile(r"^w-[0-9a-f]{8}$")
_FILTER_DEFAULT = {
    "categories": [], "min_rating": None, "open_now": False,
    "include_unknown": True, "cluster": True,
}


def _fail(error: str, message: str, fix: str, **extra) -> dict:
    return {"ok": False, "error": error, "message": message, "fix": fix, **extra}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(text or "").lower()).strip("-")
    return (slug or "unlabeled")[:48]


def scrub(value):
    """Drop secret-like keys and assignment snippets. Ordinary prose stays."""
    if isinstance(value, dict):
        return {str(k): scrub(v) for k, v in value.items() if not _SECRET_KEY.search(str(k))}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, str):
        return _ASSIGN.sub("[redacted]", value)
    return value


def kind_of(spec: dict, source: str = "annotation") -> str:
    if source == "watch":
        return "watches"
    if source == "scene":
        return "scenes"
    if source == "shape":
        shape = str(spec.get("kind") or spec.get("type") or "").lower()
        return "areas" if shape in {"polygon", "box", "area"} else "routes"
    kind = str(spec.get("type") or "pin").lower()
    if kind in _ROUTE_TYPES:
        return "routes"
    if kind in _AREA_TYPES:
        return "areas"
    if kind in _PIN_TYPES:
        return "pins"
    points = spec.get("points") if isinstance(spec.get("points"), list) else []
    if len(points) >= 3:
        return "areas"
    if len(points) >= 2:
        return "routes"
    return "pins"


def _tag(spec: dict, key: str):
    tags = spec.get("tags") if isinstance(spec.get("tags"), dict) else {}
    return tags.get(key)


def group_label(spec: dict, kind: str) -> str:
    """Batch, session, or label when the mark has one, else a pin category."""
    for key in ("batch", "group", "session"):
        value = spec.get(key)
        if not isinstance(value, str) or not value.strip():
            value = _tag(spec, key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    if kind == "pins":
        category = spec.get("category")
        if not isinstance(category, str) or not category.strip():
            for key in _CAT_KEYS:
                raw = _tag(spec, key)
                if isinstance(raw, str) and raw.strip():
                    category = raw.split(";")[0].split(",")[0]
                    break
        if isinstance(category, str) and category.strip():
            return category.strip()[:80]
    for key in ("label", "title", "name"):
        value = spec.get(key)
        if kind != "pins" and isinstance(value, str) and value.strip():
            return value.strip()[:80]
    intent = spec.get("intent")
    if isinstance(intent, str) and intent.strip():
        return intent.strip()[:80]
    return "unlabeled"


def stamp_batch(item: dict, args: dict | None) -> None:
    """Remember a Hermes call's intent as the batch when the mark has no group yet."""
    if not isinstance(item, dict):
        return
    if any(isinstance(item.get(key), str) and item.get(key).strip() for key in ("batch", "group", "session")):
        return
    if any(isinstance(_tag(item, key), str) and str(_tag(item, key)).strip() for key in ("batch", "group", "session")):
        return
    if item.get("category") or any(_tag(item, key) for key in _CAT_KEYS):
        return
    intent = (args or {}).get("intent") or (args or {}).get("context")
    if isinstance(intent, str) and intent.strip():
        item["batch"] = intent.strip()[:80]


def _entry_visible(spec: dict, source: str) -> bool:
    if source == "watch":
        return spec.get("active") is not False
    if source == "scene":
        return spec.get("overlayVisible", spec.get("visible", True)) is not False
    return spec.get("visible") is not False


def _description(spec: dict, source: str) -> str:
    if source == "scene":
        return str(spec.get("notes") or spec.get("description") or "")[:2000]
    if source == "watch":
        return str(spec.get("description") or "")[:2000]
    return str(spec.get("description_md") or spec.get("description") or "")[:2000]


def _title(spec: dict, source: str) -> str:
    if source == "watch":
        return str(spec.get("name") or spec.get("title") or "")[:120]
    if source == "scene":
        return str(spec.get("title") or spec.get("name") or "")[:120]
    return str(spec.get("title") or spec.get("label") or "")[:120]


def _label(spec: dict, source: str) -> str:
    if source == "watch":
        return str(spec.get("name") or spec.get("id") or "Watch")[:120]
    if source == "scene":
        return str(spec.get("title") or spec.get("name") or "Scene")[:120]
    return str(spec.get("label") or spec.get("title") or spec.get("name") or spec.get("type") or "Overlay")[:120]


class Book:
    """Read and edit one scene's overlays plus the shared watch and scene stores."""

    def __init__(self, scenes, key: str, watches=None, relay=None, session_id: str | None = None):
        self.scenes = scenes
        self.key = key or "default"
        self.watches = watches
        self.relay = relay
        self.session_id = session_id

    def _watch_store(self):
        if self.watches is not None:
            return self.watches
        from .watch import store
        return store()

    def _snapshot(self) -> dict:
        with self.scenes.lock:
            scene = self.scenes.scene(self.key)
            annotations = []
            for ann in scene.get("annotations") or []:
                if isinstance(ann, dict):
                    annotations.append({
                        "id": ann.get("id"),
                        "paneId": ann.get("paneId"),
                        "spec": dict(ann.get("spec") or {}),
                    })
            shapes = {}
            for sid, shape in (scene.get("shapes") or {}).items():
                if isinstance(shape, dict):
                    shapes[str(sid)] = dict(shape)
            filters = scene.get("filters")
            filters = dict(filters) if isinstance(filters, dict) else None
        return {"annotations": annotations, "shapes": shapes, "filters": filters}

    def _scenes(self) -> list[dict]:
        from . import investigations
        rows = []
        try:
            folder = investigations.inv_dir()
        except Exception:
            return rows
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            name = str(data.get("name") or path.stem)[:81]
            rows.append({
                "name": name,
                "title": data.get("title") or name,
                "notes": data.get("notes") or "",
                "overlayVisible": data.get("overlayVisible", True) is not False,
                "pins": len(data.get("pins") or []),
                "routes": len(data.get("routes") or []),
            })
        return rows

    def _watches(self) -> list[dict]:
        try:
            listed = self._watch_store().list(None)
        except Exception:
            return []
        return [row for row in (listed.get("watches") or []) if isinstance(row, dict)]

    def entries(self) -> list[dict]:
        snap = self._snapshot()
        rows = []
        for ann in snap["annotations"]:
            spec = ann["spec"]
            kind = kind_of(spec, "annotation")
            rows.append(self._row(
                f"ann:{ann['id']}", kind, "annotation", spec,
                extra={"storeId": ann["id"], "paneId": ann.get("paneId"), "type": spec.get("type") or "pin"},
            ))
        for sid, shape in snap["shapes"].items():
            kind = kind_of(shape, "shape")
            rows.append(self._row(
                f"shape:{sid}", kind, "shape", shape,
                extra={"storeId": sid, "type": shape.get("kind") or "line"},
            ))
        for watch in self._watches():
            rows.append(self._row(
                f"watch:{watch.get('id')}", "watches", "watch", watch,
                extra={"storeId": watch.get("id"), "type": "watch",
                       "session": watch.get("session_id") or ""},
            ))
        for scene in self._scenes():
            rows.append(self._row(
                "scene:" + scene["name"], "scenes", "scene", scene,
                extra={"storeId": scene["name"], "type": "scene"},
            ))
        return rows

    def _row(self, eid: str, kind: str, source: str, spec: dict, extra: dict) -> dict:
        group = group_label(spec if source != "watch" else {**spec, "session": spec.get("session_id")}, kind)
        if source == "watch":
            group = "This session" if spec.get("session_id") and spec.get("session_id") == self.session_id else (
                str(spec.get("session_id"))[:18] if spec.get("session_id") else "Watches"
            )
        if source == "scene":
            group = "Saved"
        visible = _entry_visible(spec, source)
        return {
            "id": eid,
            "kind": kind,
            "source": source,
            "role": "entry",
            "type": extra.get("type") or kind,
            "label": _label(spec, source),
            "title": _title(spec, source),
            "description": _description(spec, source),
            "visible": visible,
            "batch": group,
            "storeId": extra.get("storeId"),
            "paneId": extra.get("paneId"),
            "session": extra.get("session") or "",
        }

    def tree(self) -> dict:
        rows = self.entries()
        groups = []
        hidden = 0
        counts = {kind: 0 for kind in KINDS}
        for kind in KINDS:
            members = [row for row in rows if row["kind"] == kind]
            counts[kind] = len(members)
            batches: dict[str, list] = {}
            for row in members:
                batches.setdefault(row["batch"], []).append(row)
                if not row["visible"]:
                    hidden += 1
            children = []
            for label, items in sorted(batches.items(), key=lambda pair: pair[0].lower()):
                kids = [_public_entry(item) for item in items]
                children.append({
                    "id": f"kind:{kind}/batch:{_slug(label)}",
                    "kind": kind,
                    "role": "batch",
                    "label": label,
                    "shown": all(item["visible"] for item in items) if items else True,
                    "count": len(items),
                    "children": kids,
                })
            groups.append({
                "id": f"kind:{kind}",
                "kind": kind,
                "role": "kind",
                "label": KIND_LABELS[kind],
                "shown": all(item["visible"] for item in members) if members else True,
                "count": len(members),
                "children": children,
            })
        snap = self._snapshot()
        filters = dict(_FILTER_DEFAULT)
        if snap["filters"]:
            filters.update({k: snap["filters"].get(k, filters[k]) for k in _FILTER_DEFAULT})
        return {
            "ok": True,
            "schema": SCHEMA,
            "scene": self.key,
            "migration": MIGRATION,
            "filters": filters,
            "counts": {**counts, "hidden": hidden, "total": len(rows)},
            "groups": groups,
        }

    def _match(self, eid: str) -> list[dict]:
        rows = self.entries()
        if eid.startswith("kind:"):
            body = eid[5:]
            if "/batch:" in body:
                kind, _, slug = body.partition("/batch:")
                return [row for row in rows if row["kind"] == kind and _slug(row["batch"]) == slug]
            return [row for row in rows if row["kind"] == body]
        return [row for row in rows if row["id"] == eid]

    def _redraw(self, structural: bool) -> dict:
        relay = self.relay
        if not structural or relay is None or not relay.open_panes():
            return {"pane": False}
        steps = []
        for name, args in self.scenes.restore_commands(self.key, redraw_only=True):
            result = relay.submit(name, args, session_id=self.session_id, record=False, wait=4)
            ok = bool(isinstance(result, dict) and result.get("ok"))
            steps.append({"name": name, "ok": ok})
            if isinstance(result, dict) and result.get("error") in {"no_pane", "pane_lost", "relay_unavailable"}:
                break
        return {"pane": True, "steps": len(steps), "synced": all(step["ok"] for step in steps)}

    def _overlay_push(self, rows: list[dict]) -> dict:
        relay = self.relay
        if relay is None or not relay.open_panes() or not rows:
            return {"pane": False}
        items = [{
            "id": row["id"], "paneId": row.get("paneId"), "kind": row["kind"],
            "source": row["source"], "type": row.get("type"), "visible": row["visible"],
            "storeId": row.get("storeId"),
        } for row in rows]
        result = relay.submit("hermes_overlay", {"items": items}, session_id=self.session_id, record=False, wait=4)
        needs = bool(isinstance(result, dict) and result.get("needsRedraw"))
        out = {"pane": True, "overlay": bool(isinstance(result, dict) and result.get("ok")), "needsRedraw": needs}
        if needs:
            out["redraw"] = self._redraw(True)
        return out

    def set_visible(self, eid: str, visible: bool) -> dict:
        rows = self._match(eid)
        if not rows:
            return _fail("not_found", f"No overlay {eid!r}.",
                         "Pass an id from godseye_annotate op=overlays action=tree.")
        changed = []
        for row in rows:
            if row["source"] == "annotation":
                if not self.scenes.update_annotation(self.key, str(row["storeId"]), {"visible": bool(visible)}):
                    continue
                if visible:
                    self._drop_visible_true(str(row["storeId"]))
            elif row["source"] == "shape":
                self._set_shape(str(row["storeId"]), {"visible": bool(visible)})
            elif row["source"] == "watch":
                result = self._watch_store().set_active(str(row["storeId"]), bool(visible))
                if not result.get("ok"):
                    return result
            elif row["source"] == "scene":
                result = patch_scene(str(row["storeId"]), {"visible": bool(visible)})
                if not result.get("ok"):
                    return result
            changed.append({**row, "visible": bool(visible)})
        globe = [row for row in changed if row["source"] in {"annotation", "shape"}]
        needs_draw = any(
            row["kind"] in {"routes", "areas"} or (row["source"] == "annotation" and visible)
            for row in globe
        )
        if needs_draw:
            pane = self._redraw(True)
        elif globe:
            pane = self._overlay_push(changed)
        else:
            pane = {"pane": False}
        tree = self.tree()
        return {"ok": True, "action": "show" if visible else "hide", "updated": [row["id"] for row in changed],
                "visible": bool(visible), "pane": pane, "counts": tree["counts"]}

    def _drop_visible_true(self, ann_id: str) -> None:
        """Shown marks omit ``visible`` so a restore payload stays the stored spec."""
        with self.scenes.lock:
            for ann in self.scenes.scene(self.key).get("annotations") or []:
                if str(ann.get("id")) == ann_id:
                    spec = ann.get("spec")
                    if isinstance(spec, dict) and spec.get("visible") is True:
                        spec.pop("visible", None)
                        self.scenes._save()
                    return

    def _set_shape(self, shape_id: str, fields: dict) -> bool:
        with self.scenes.lock:
            shapes = self.scenes.scene(self.key).setdefault("shapes", {})
            shape = shapes.get(shape_id)
            if not isinstance(shape, dict):
                return False
            for key, value in fields.items():
                if value is None:
                    continue
                shape[key] = value
            if fields.get("visible") is True:
                shape.pop("visible", None)
            self.scenes.scene(self.key)["updated"] = time.time()
            self.scenes._save()
            return True

    def delete(self, eid: str, confirm: bool) -> dict:
        rows = self._match(eid)
        if not rows:
            if str(eid).startswith("kind:"):
                return {"ok": True, "action": "delete", "deleted": [], "removed": 0,
                        "counts": self.tree()["counts"]}
            return _fail("not_found", f"No overlay {eid!r}.",
                         "Pass an id from godseye_annotate op=overlays action=tree.")
        grouped = eid.startswith("kind:") or len(rows) > 1
        guarded = grouped or any(row["source"] in {"watch", "scene"} for row in rows)
        if guarded and not confirm:
            return _fail(
                "confirm_required",
                f"Deleting {eid} removes {len(rows)} overlay(s).",
                "Ask the user, then call godseye_annotate op=overlays action=delete with confirm=true.",
                count=len(rows), id=eid,
            )
        gone = []
        for row in rows:
            if row["source"] == "annotation":
                if self.scenes.delete(self.key, str(row["storeId"])):
                    gone.append(row["id"])
            elif row["source"] == "shape":
                if self._delete_shape(str(row["storeId"])):
                    gone.append(row["id"])
            elif row["source"] == "watch":
                result = self._watch_store().delete(str(row["storeId"]), True)
                if result.get("ok"):
                    gone.append(row["id"])
            elif row["source"] == "scene":
                from . import investigations
                result = investigations.delete(str(row["storeId"]), confirm=True)
                if result.get("ok"):
                    gone.append(row["id"])
        structural = any(row["source"] in {"annotation", "shape"} for row in rows)
        pane = self._redraw(structural)
        if any(row["source"] == "shape" for row in rows) and self.relay is not None and self.relay.open_panes():
            for row in rows:
                if row["source"] == "shape":
                    self.relay.submit("hermes_draw", {"action": "remove", "id": row["storeId"]},
                                      session_id=self.session_id, record=False, wait=4)
        return {"ok": True, "action": "delete", "deleted": gone, "removed": len(gone), "pane": pane,
                "counts": self.tree()["counts"]}

    def _delete_shape(self, shape_id: str) -> bool:
        with self.scenes.lock:
            shapes = self.scenes.scene(self.key).get("shapes") or {}
            if shape_id not in shapes:
                return False
            shapes.pop(shape_id, None)
            self.scenes.scene(self.key)["updated"] = time.time()
            self.scenes._save()
            return True

    def update(self, eid: str, fields: dict) -> dict:
        rows = self._match(eid)
        if not rows:
            return _fail("not_found", f"No overlay {eid!r}.",
                         "Pass an id from godseye_annotate op=overlays action=tree.")
        if eid.startswith("kind:") or (len(rows) > 1 and not fields.get("label") and not fields.get("title")):
            if len(rows) > 1 and not (fields.get("label") or fields.get("title")):
                return _fail("bad_args", "A group update needs a new label.",
                             "Pass label or title to rename the batch, or update one entry id.")
        changed = []
        batch_name = fields.get("label") or fields.get("title")
        renaming_group = eid.startswith("kind:") or (len(rows) > 1 and batch_name)
        for row in rows:
            if renaming_group:
                one = {"batch": str(batch_name)[:80]}
            else:
                one = dict(fields)
            if row["source"] == "annotation":
                patch = _annotation_patch(one)
                if patch and self.scenes.update_annotation(self.key, str(row["storeId"]), patch):
                    changed.append(row["id"])
            elif row["source"] == "shape":
                patch = {}
                if one.get("label") is not None:
                    patch["label"] = str(one["label"])[:80]
                if one.get("title") is not None and "label" not in one:
                    patch["label"] = str(one["title"])[:80]
                if one.get("color") is not None:
                    patch["color"] = one["color"]
                if one.get("batch") is not None:
                    patch["batch"] = str(one["batch"])[:80]
                if isinstance(one.get("points"), list):
                    patch["points"] = one["points"][:600]
                if patch and self._set_shape(str(row["storeId"]), patch):
                    changed.append(row["id"])
            elif row["source"] == "watch":
                watch_fields = {}
                if one.get("label") or one.get("title"):
                    watch_fields["name"] = one.get("label") or one.get("title")
                if one.get("description_md") is not None:
                    watch_fields["description"] = one["description_md"]
                if one.get("interval_s") is not None:
                    watch_fields["interval_s"] = one["interval_s"]
                result = self._watch_store().update_fields(str(row["storeId"]), watch_fields)
                if not result.get("ok"):
                    return result
                changed.append(row["id"])
            elif row["source"] == "scene":
                result = patch_scene(str(row["storeId"]), one)
                if not result.get("ok"):
                    return result
                changed.append(row["id"])
        structural = any(row["source"] in {"annotation", "shape"} for row in rows)
        pane = self._redraw(structural)
        return {"ok": True, "action": "update", "updated": changed, "pane": pane}

    def set_filters(self, fields: dict) -> dict:
        current = dict(_FILTER_DEFAULT)
        snap = self._snapshot()
        if snap["filters"]:
            current.update(snap["filters"])
        if fields.get("clear"):
            current = dict(_FILTER_DEFAULT)
        if isinstance(fields.get("categories"), list):
            current["categories"] = [str(item)[:40] for item in fields["categories"][:16]]
        if "min_rating" in fields:
            current["min_rating"] = fields["min_rating"]
        if "open_now" in fields:
            current["open_now"] = bool(fields["open_now"])
        if "include_unknown" in fields:
            current["include_unknown"] = fields["include_unknown"] is not False
        if "cluster" in fields:
            current["cluster"] = fields["cluster"] is not False
        self.scenes.note_filters(self.key, current)
        pane = {"pane": False}
        if self.relay is not None and self.relay.open_panes():
            result = self.relay.submit("hermes_pin_filter", current, session_id=self.session_id, record=False, wait=4)
            pane = {"pane": True, "ok": bool(isinstance(result, dict) and result.get("ok"))}
        return {"ok": True, "action": "filters", "filters": self.tree()["filters"], "pane": pane,
                "migration": MIGRATION}

    def document(self) -> dict:
        snap = self._snapshot()
        annotations = []
        for ann in snap["annotations"]:
            spec = scrub(dict(ann["spec"]))
            spec["visible"] = ann["spec"].get("visible") is not False
            annotations.append({"id": ann["id"], "paneId": ann.get("paneId"), "spec": spec})
        shapes = []
        for shape in snap["shapes"].values():
            row = scrub({key: shape.get(key) for key in (
                "id", "action", "kind", "points", "label", "color", "labelAt", "batch", "group", "session")
                if shape.get(key) is not None})
            row["action"] = "add"
            row["visible"] = shape.get("visible") is not False
            shapes.append(row)
        watches = []
        for watch in self._watches():
            watches.append(scrub({key: watch.get(key) for key in (
                "id", "name", "description", "area", "feeds", "conditions", "interval_s",
                "active", "deliver", "pin", "baseline") if key in watch}))
        scenes = []
        for scene in self._scenes():
            scenes.append({
                "name": scene["name"],
                "title": scene.get("title") or scene["name"],
                "notes": scene.get("notes") or "",
                "visible": scene.get("overlayVisible") is not False,
            })
        filters = snap["filters"]
        return {
            "schema": SCHEMA,
            "scene": self.key,
            "filters": scrub(filters) if filters else None,
            "annotations": annotations,
            "shapes": shapes,
            "watches": watches,
            "scenes": scenes,
        }

    def export(self, source: str | None, include_document: bool = True) -> dict:
        try:
            path = export_path(source)
        except ValueError as exc:
            return _fail("bad_args", str(exc),
                         "Pass source as a file name under plugin-data/godseye/overlays, or omit it.")
        doc = self.document()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True))
        tmp.replace(path)
        out = {
            "ok": True, "action": "export", "schema": SCHEMA, "path": str(path),
            "counts": self.tree()["counts"],
            "migration": MIGRATION,
        }
        if include_document:
            out["document"] = doc
        return out

    def import_doc(self, doc: dict, replace: bool, purge: bool, confirm: bool) -> dict:
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            return _fail("bad_args", "Import needs an eoh.overlays.v1 JSON document.",
                         "Use godseye_annotate op=overlays action=export, then import that file or its document.")
        doc = scrub(doc)
        annotations = []
        for ann in doc.get("annotations") or []:
            if not isinstance(ann, dict):
                continue
            spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else {}
            aid = str(ann.get("id") or "")
            if not (aid.startswith("a") and aid[1:].isdigit()):
                aid = ""
            annotations.append({"id": aid, "paneId": ann.get("paneId"), "spec": spec})
        shapes = {}
        for shape in doc.get("shapes") or []:
            if not isinstance(shape, dict) or not shape.get("id"):
                continue
            stored = {key: shape.get(key) for key in (
                "id", "kind", "points", "label", "color", "labelAt", "batch", "group", "session")
                if shape.get(key) is not None}
            stored["action"] = "add"
            stored["id"] = str(shape["id"])[:80]
            if shape.get("visible") is False:
                stored["visible"] = False
            shapes[stored["id"]] = stored
        if purge and not confirm:
            return _fail("confirm_required", "purge removes watches and scenes missing from the file.",
                         "Call again with confirm=true after the user agrees, or import without purge.")
        self._replace_scene(annotations, shapes, doc.get("filters") if isinstance(doc.get("filters"), dict) else None,
                            replace=replace)
        watch_ids = []
        for watch in doc.get("watches") or []:
            if isinstance(watch, dict):
                result = self._watch_store().upsert_exported(watch)
                if result.get("ok"):
                    watch_ids.append(result["watch"]["id"])
        scene_names = []
        for scene in doc.get("scenes") or []:
            if isinstance(scene, dict) and scene.get("name"):
                result = upsert_scene(scene)
                if result.get("ok"):
                    scene_names.append(result["name"])
        if purge and confirm:
            self._purge_missing(watch_ids, scene_names)
        pane = self._redraw(True)
        counts = self.tree()["counts"]
        return {"ok": True, "action": "import", "schema": SCHEMA, "replace": bool(replace),
                "annotations": counts["pins"] + counts["routes"] + counts["areas"],
                "watches": len(watch_ids), "scenes": len(scene_names), "pane": pane, "counts": counts}

    def _replace_scene(self, annotations: list, shapes: dict, filters: dict | None, replace: bool) -> None:
        with self.scenes.lock:
            scene = self.scenes.scene(self.key)
            if replace:
                cleaned = []
                for ann in annotations[-96:]:
                    spec = dict(ann.get("spec") or {})
                    if spec.get("visible") is True:
                        spec.pop("visible", None)
                    aid = ann.get("id") or f"a{next(self.scenes.ids)}"
                    cleaned.append({"id": str(aid), "spec": spec, "paneId": ann.get("paneId")})
                scene["annotations"] = cleaned
                scene["shapes"] = shapes
                scene["filters"] = None
            else:
                have = {ann.get("id") for ann in scene.get("annotations") or []}
                for ann in annotations:
                    aid = ann.get("id") or f"a{next(self.scenes.ids)}"
                    row = {"id": str(aid), "spec": dict(ann.get("spec") or {}), "paneId": ann.get("paneId")}
                    if row["id"] in have:
                        scene["annotations"] = [row if item.get("id") == row["id"] else item
                                                for item in scene["annotations"]]
                    else:
                        scene["annotations"].append(row)
                scene.setdefault("shapes", {}).update(shapes)
                del scene["annotations"][:-96]
                while len(scene["shapes"]) > 40:
                    scene["shapes"].pop(next(iter(scene["shapes"])))
            if filters is not None:
                kept = {key: filters[key] for key in _FILTER_DEFAULT if key in filters}
                scene["filters"] = kept or None
            scene["updated"] = time.time()
            self._bump_ids()
            self.scenes._save()

    def _bump_ids(self) -> None:
        import itertools
        top = 0
        for scene in self.scenes.scenes.values():
            for ann in scene.get("annotations") or []:
                aid = str(ann.get("id") or "")
                if aid.startswith("a") and aid[1:].isdigit():
                    top = max(top, int(aid[1:]))
        self.scenes.ids = itertools.count(top + 1)

    def _purge_missing(self, watch_ids: list, scene_names: list) -> None:
        store = self._watch_store()
        for watch in list(self._watches()):
            if watch.get("id") not in watch_ids:
                store.delete(str(watch.get("id")), True)
        from . import investigations
        for scene in self._scenes():
            if scene["name"] not in scene_names:
                investigations.delete(scene["name"], confirm=True)


def _public_entry(row: dict) -> dict:
    # ``shown`` rather than ``visible``: concise tool output drops list rows that
    # carry a ``visible`` field, and hidden overlays must stay in the tree.
    return {
        "id": row["id"],
        "kind": row["kind"],
        "role": "entry",
        "type": row.get("type"),
        "source": row["source"],
        "label": row["label"],
        "title": row["title"],
        "description": row["description"],
        "shown": row["visible"],
        "batch": row["batch"],
    }


def _annotation_patch(fields: dict) -> dict:
    patch = {}
    for key in ("label", "title", "description_md", "images", "links", "tags", "category", "color",
                "batch", "group", "session", "mode"):
        if key in fields and fields[key] is not None:
            patch[key] = fields[key]
    if fields.get("description_md") is None and fields.get("description") is not None:
        patch["description_md"] = fields["description"]
    if isinstance(fields.get("points"), list):
        patch["points"] = fields["points"][:600]
    return patch


def data_root() -> Path:
    from .investigations import data_dir
    root = data_dir() / "overlays"
    root.mkdir(parents=True, exist_ok=True)
    return root


def export_path(source: str | None) -> Path:
    root = data_root().resolve()
    if not source:
        return root / "overlays.json"
    raw = Path(str(source)).expanduser()
    dest = (root / raw.name).resolve() if not raw.is_absolute() else raw.resolve()
    if dest != root and root not in dest.parents:
        raise ValueError("Export path must stay under plugin-data/godseye/overlays.")
    if dest.suffix.lower() != ".json":
        dest = dest.with_suffix(dest.suffix + ".json")
    return dest


def import_path(source: str) -> Path:
    raw = Path(str(source or "")).expanduser()
    if not raw.is_file():
        alt = data_root() / raw.name
        if alt.is_file():
            raw = alt
        else:
            raise ValueError(f"No overlay file at {source!r}.")
    if raw.suffix.lower() != ".json":
        raise ValueError("Import source must be a .json file.")
    if raw.name.lower().startswith(".") or "env" in raw.name.lower():
        raise ValueError("Refusing to import that path.")
    if raw.stat().st_size > 2_000_000:
        raise ValueError("Import file is over 2 MB.")
    return raw


def patch_scene(name: str, fields: dict) -> dict:
    from . import investigations
    try:
        clean = investigations.validate_name(name)
    except ValueError as exc:
        return _fail("bad_args", str(exc), "Use the investigation name from op=overlays action=tree.")
    path = investigations._path(clean)
    if not path.is_file():
        return _fail("not_found", f"No investigation named {clean!r}.",
                     "Save it with godseye_scene op=save, or import a scene row.")
    return _write_scene(path, clean, fields, create=False)


def upsert_scene(row: dict) -> dict:
    from . import investigations
    try:
        clean = investigations.validate_name(str(row.get("name") or ""))
    except ValueError as exc:
        return _fail("bad_args", str(exc), "Scene names are letters, digits, spaces, and _ . -")
    path = investigations._path(clean)
    fields = {
        "title": row.get("title"),
        "description_md": row.get("notes", row.get("description")),
        "visible": row.get("visible", True),
    }
    return _write_scene(path, clean, fields, create=not path.is_file())


def _write_scene(path: Path, name: str, fields: dict, create: bool) -> dict:
    if path.is_file():
        try:
            data = json.loads(path.read_text())
        except Exception:
            return _fail("bad_args", "That investigation file is not JSON.",
                         "Open the file under plugin-data/godseye/investigations and fix the JSON.")
        if not isinstance(data, dict):
            return _fail("bad_args", "That investigation file is not an object.",
                         "Restore the investigation from a backup, then retry.")
    elif create:
        data = {"name": name, "saved_at": time.time(), "pins": [], "routes": [], "annotations": [], "cards": []}
    else:
        return _fail("not_found", f"No investigation named {name!r}.",
                     "Save it with godseye_scene op=save first.")
    if fields.get("title") is not None:
        data["title"] = str(fields["title"])[:120]
    elif fields.get("label") is not None:
        data["title"] = str(fields["label"])[:120]
    if fields.get("description_md") is not None:
        data["notes"] = str(fields["description_md"])[:2000]
    if "visible" in fields and fields["visible"] is not None:
        data["overlayVisible"] = bool(fields["visible"])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)
    return {"ok": True, "name": data.get("name") or name, "title": data.get("title") or name,
            "notes": data.get("notes") or "", "visible": data.get("overlayVisible", True) is not False}


def handle(scenes, key: str, body: dict, watches=None, relay=None, session_id: str | None = None) -> dict:
    """Run one overlay action. ``body`` uses the same fields as ``op=overlays``."""
    book = Book(scenes, key, watches=watches, relay=relay, session_id=session_id)
    action = str(body.get("action") or "tree")
    eid = str(body.get("id") or "")
    if action == "tree":
        return book.tree()
    if action in {"show", "hide"}:
        if not eid:
            return _fail("bad_args", "show and hide need an id.",
                         "Pass id of an entry or a group from action=tree.")
        return book.set_visible(eid, action == "show")
    if action == "delete":
        if not eid and not body.get("ids"):
            return _fail("bad_args", "delete needs an id.",
                         "Pass id of an entry or a group. Groups, watches, and scenes need confirm=true.")
        if body.get("ids") and not eid:
            gone = []
            for item in body.get("ids") or []:
                result = book.delete(str(item), body.get("confirm") is True)
                if not result.get("ok"):
                    result["deleted"] = gone
                    return result
                gone.extend(result.get("deleted") or [])
            return {"ok": True, "action": "delete", "deleted": gone, "removed": len(gone), "counts": book.tree()["counts"]}
        return book.delete(eid, body.get("confirm") is True)
    if action == "update":
        if not eid:
            return _fail("bad_args", "update needs an id.",
                         "Pass id plus label, title, or description_md.")
        fields = {key: body.get(key) for key in (
            "label", "title", "description_md", "description", "images", "links", "tags", "category",
            "color", "batch", "group", "session", "mode", "points", "interval_s") if key in body}
        if not fields:
            return _fail("bad_args", "update needs a field to change.",
                         "Pass label, title, description_md, category, color, batch, or points.")
        return book.update(eid, fields)
    if action == "filters":
        return book.set_filters(body)
    if action == "export":
        return book.export(body.get("source") or body.get("file"),
                           include_document=body.get("includeDocument", True) is not False)
    if action == "import":
        doc = body.get("document") if isinstance(body.get("document"), dict) else None
        if doc is None and body.get("source"):
            try:
                doc = json.loads(import_path(str(body.get("source"))).read_text())
            except ValueError as exc:
                return _fail("bad_args", str(exc),
                             "Pass source as a .json path, or pass document as the eoh.overlays.v1 object.")
            except json.JSONDecodeError:
                return _fail("bad_args", "That file is not JSON.",
                             "Export again with op=overlays action=export and import that file.")
        if not isinstance(doc, dict):
            return _fail("bad_args", "import needs source or document.",
                         "Pass source as a .json path or document as the exported object.")
        return book.import_doc(doc, replace=body.get("replace", True) is not False,
                               purge=body.get("purge") is True, confirm=body.get("confirm") is True)
    return _fail("bad_args", f"Unknown overlay action {action!r}.",
                 "Use action tree, show, hide, delete, update, filters, export, or import.")


def run(args: dict, session_id: str | None = None) -> dict:
    from . import live
    relay = live.current()
    if relay is None:
        return _fail("relay_unavailable", "The live-globe relay is not running.",
                     "Restart Hermes with the godseye plugin enabled, then retry op=overlays.")
    body = dict(args or {})
    body["session_id"] = session_id
    return relay.overlays_op(body)
