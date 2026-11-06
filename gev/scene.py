"""Per-session scene store (annotations + camera), kept by the live relay so a pane that is
reopened, reloaded or rotated gets its scene back without the agent redrawing or re-geocoding.

Annotations are stored with the coordinates the pane resolved, so a restore never geocodes."""
from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
from pathlib import Path

def _same_mark(ann: dict, ann_id: str) -> bool:
    if ann.get("id") == ann_id or ann.get("paneId") == ann_id:
        return True
    spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else {}
    return bool(ann_id) and (spec.get("clientId") == ann_id or spec.get("id") == ann_id)


def _public_annotation(ann: dict, full: bool) -> dict:
    spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else {}
    item = {"id": ann["id"], "type": spec.get("type", "pin"),
            "label": spec.get("label") or spec.get("target"),
            "latitude": spec.get("latitude"), "longitude": spec.get("longitude")}
    clips = spec.get("clips") if isinstance(spec.get("clips"), list) else []
    if clips:
        item["clips"] = clips[:4]
    if full:
        from .marks import public_meta
        item.update(public_meta(spec))
        if ann.get("paneId"):
            item["paneId"] = ann.get("paneId")
    return item


DEFAULT_KEY = "default"
MAX_ITEMS = 96
POINT_TYPES = {"pin", "highlight", "label", "area", "card"}
_LAYER_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def stored_shapes(raw) -> dict:
    """Investigation snapshots store shapes as a list; the scene keeps them by id."""
    if isinstance(raw, dict):
        return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
    if isinstance(raw, list):
        out = {}
        for i, item in enumerate(raw):
            if isinstance(item, dict):
                out[str(item.get("id") or i)] = dict(item)
        return out
    return {}


def default_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    return home / "plugin-data" / "godseye" / "scenes.json"


class SceneStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.scenes: dict[str, dict] = {}
        self.ids = itertools.count(1)
        if path and path.is_file():
            try:
                self.scenes = json.loads(path.read_text()).get("scenes", {})
                top = max((int(a["id"][1:]) for s in self.scenes.values() for a in s.get("annotations", [])
                           if str(a.get("id", "")).startswith("a") and a["id"][1:].isdigit()), default=0)
                self.ids = itertools.count(top + 1)
            except Exception:
                self.scenes = {}

    # -- storage -------------------------------------------------------------------------------
    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"scenes": self.scenes}))
            tmp.replace(self.path)
        except Exception:
            pass

    def scene(self, key: str | None) -> dict:
        key = str(key or DEFAULT_KEY)
        return self.scenes.setdefault(key, {"annotations": [], "camera": None, "caption": None, "updated": 0.0})

    def pick(self, sessions) -> str | None:
        """Scene key for a pane: one of its bound sessions, else the most recently updated scene."""
        for s in sessions or ():
            if s in self.scenes and self._has(self.scenes[s]):
                return s
        live = [(v.get("updated", 0), k) for k, v in self.scenes.items() if self._has(v)]
        return max(live)[1] if live else None

    @staticmethod
    def _has(scene: dict) -> bool:
        return bool(scene.get("annotations") or scene.get("camera") or scene.get("shapes")
                    or scene.get("timeline") or scene.get("motion"))

    @staticmethod
    def _drawer(sc: dict, args: dict, result: dict) -> bool:
        """Remember which pin's drawer is open (by coordinates + label: pane ids change on restore)."""
        action = str((args or {}).get("action") or "open")
        if action == "state":
            return False
        pin = result.get("pin") if isinstance(result.get("pin"), dict) else None
        if action == "close" or not result.get("open") or not pin:
            had = sc.get("drawer") is not None
            sc["drawer"] = None
            return had
        if not isinstance(pin.get("latitude"), (int, float)) or not isinstance(pin.get("longitude"), (int, float)):
            return False
        sc["drawer"] = {"latitude": pin["latitude"], "longitude": pin["longitude"], "label": pin.get("label")}
        return True

    def note_drawer(self, key: str | None, pin: dict | None) -> None:
        """A user click opened/closed a drawer in the pane (reported by the pane)."""
        with self.lock:
            sc = self.scene(key)
            self._drawer(sc, {"action": "open" if pin else "close"}, {"open": bool(pin), "pin": pin})
            sc["updated"] = time.time()
            self._save()

    FILTER_KEYS = ("categories", "min_rating", "open_now", "include_unknown", "cluster")

    @classmethod
    def _filters(cls, sc: dict, args: dict, result: dict) -> bool:
        """Keep the pane's pin filters (categories / min_rating / open_now / cluster) in the scene."""
        if str((args or {}).get("action") or "set") in ("state", "expand"):
            return False
        f = result.get("filters") if isinstance(result.get("filters"), dict) else None
        if f is None:
            return False
        f = {k: f.get(k) for k in cls.FILTER_KEYS if k in f}
        default = {"categories": [], "min_rating": None, "open_now": False, "include_unknown": True, "cluster": True}
        sc["filters"] = None if all(f.get(k, v) == v for k, v in default.items()) else f
        return True

    def note_timeline(self, key: str | None, patch, replace: bool = False) -> bool:
        """Keep the time slider. ``replace`` writes the whole record; a scrub merges the playhead."""
        from .timeline import clean_state
        with self.lock:
            sc = self.scene(key)
            if patch is None or (isinstance(patch, dict) and patch.get("clear")):
                had = sc.get("timeline") is not None
                sc["timeline"] = None
                if had:
                    sc["updated"] = time.time()
                    self._save()
                return had
            if not isinstance(patch, dict):
                return False
            base = {} if replace or not isinstance(sc.get("timeline"), dict) else dict(sc["timeline"])
            for field, value in patch.items():
                if field == "clear" or value in (None, "", []):
                    continue
                base[field] = value
            cleaned = clean_state(base)
            if cleaned is None:
                return False
            sc["timeline"] = cleaned
            sc["updated"] = time.time()
            self._save()
            return True

    def _timeline(self, sc: dict, args: dict, result: dict) -> bool:
        from .timeline import merge_state
        action = str(args.get("action") or "set")
        if action == "state":
            return False
        merged = merge_state(sc.get("timeline"), args, result)
        if merged is None:
            had = sc.get("timeline") is not None
            sc["timeline"] = None
            return had
        if action in ("set", "stop") or not args.get("playing"):
            if action != "play":
                merged["playing"] = False
        sc["timeline"] = merged
        return True

    def note_motion(self, key: str | None, motion: dict | None) -> None:
        """Remember a street walk or dolly so a reloaded pane can say it was interrupted."""
        with self.lock:
            sc = self.scene(key)
            if motion is None:
                sc.pop("motion", None)
            elif isinstance(motion, dict):
                sc["motion"] = {k: v for k, v in motion.items() if v is not None}
            else:
                return
            sc["updated"] = time.time()
            self._save()

    def interrupt_motion(self, key: str | None) -> dict | None:
        """A fresh pane means a running walk or dolly did not finish. Report it once per reconnect."""
        with self.lock:
            sc = self.scenes.get(str(key or DEFAULT_KEY))
            if not sc or not isinstance(sc.get("motion"), dict):
                return None
            motion = dict(sc["motion"])
            if motion.get("state") not in ("running", "paused", "interrupted"):
                return None
            motion["state"] = "interrupted"
            sc["motion"] = motion
            sc["updated"] = time.time()
            self._save()
            return motion

    def note_filters(self, key: str | None, filters: dict | None) -> None:
        """The user toggled a filter chip in the pane (reported by the pane)."""
        with self.lock:
            sc = self.scene(key)
            self._filters(sc, {"action": "set"}, {"filters": filters if isinstance(filters, dict) else {}})
            sc["updated"] = time.time()
            self._save()

    # -- recording -----------------------------------------------------------------------------
    def record(self, key: str | None, name: str, args: dict, res: dict) -> None:
        if not isinstance(res, dict) or not res.get("ok"):
            return
        result = res.get("result") if isinstance(res.get("result"), dict) else res
        with self.lock:
            sc = self.scene(key)
            changed = True
            if name == "annotate_map":
                self._add_annotations(sc, args, result)
            elif name == "clear_annotations":
                sc["annotations"] = []
                sc["drawer"] = None
            elif name == "hermes_pin_drawer":
                changed = self._drawer(sc, args, result)
            elif name == "hermes_pin_filter":
                changed = self._filters(sc, args, result)
            elif name == "hermes_draw":
                changed = self._shape(sc, args or {}, result)
            elif name == "hermes_caption":
                sc["caption"] = (args or {}).get("text") or None
            elif name == "hermes_timeline":
                changed = self._timeline(sc, args or {}, result)
            elif name == "set_layer_visibility":
                changed = self._set_layer(sc, args or {})
            elif name in ("get_current_view_state", "fly_to_location", "move_camera"):
                changed = self._camera(sc, name, args, result)
                changed = self._take_layers(sc, result) or changed
            else:
                changed = False
            if changed:
                sc["updated"] = time.time()
                self._save()

    @staticmethod
    def _shape(sc: dict, args: dict, result: dict) -> bool:
        """Hermes-drawn lines/polygons (measurements, isochrones): kept so a reopened pane redraws them."""
        shapes = sc.setdefault("shapes", {})
        action = str(args.get("action") or "add")
        if action == "add" and isinstance(result.get("shape"), dict):
            sh = result["shape"]
            shapes[sh["id"]] = {"action": "add", "id": sh["id"], "kind": sh.get("kind"), "points": sh.get("points"),
                                "label": args.get("label", sh.get("label")), "color": args.get("color"),
                                **({"labelAt": args["labelAt"]} if args.get("labelAt") else {})}
            while len(shapes) > 40:
                shapes.pop(next(iter(shapes)))
            return True
        if action == "remove":
            return shapes.pop(str(args.get("id") or ""), None) is not None
        if action == "clear" and args.get("who", "all") in ("all", "hermes"):
            had = bool(shapes)
            shapes.clear()
            return had
        return False

    def _add_annotations(self, sc: dict, args: dict, result: dict) -> None:
        items = result.get("items") if isinstance(result.get("items"), list) else []
        for i, spec in enumerate((args or {}).get("annotations") or []):
            if not isinstance(spec, dict):
                continue
            got = items[i] if i < len(items) and isinstance(items[i], dict) else {}
            if items and not got.get("ok", True):
                continue  # the pane could not draw it; do not resurrect it later
            item = {k: v for k, v in spec.items() if k != "flyTo"}
            lat, lon = got.get("latitude"), got.get("longitude")
            if item.get("type", "pin") in POINT_TYPES and isinstance(lat, (int, float)) and isinstance(lon, (int, float)) \
                    and item.get("type") != "area":
                item["latitude"], item["longitude"] = lat, lon
                if item.get("target") and not item.get("label"):
                    item["label"] = str(item["target"])[:120]
                item.pop("target", None)  # coordinates now; never re-geocode
            sc["annotations"].append({"id": f"a{next(self.ids)}", "spec": item, "paneId": got.get("id")})
        del sc["annotations"][:-MAX_ITEMS]

    @staticmethod
    def _camera(sc: dict, name: str, args: dict, result: dict) -> bool:
        cam = result.get("camera") if isinstance(result.get("camera"), dict) else {}
        lat = cam.get("latitude", result.get("latitude"))
        lon = cam.get("longitude", result.get("longitude"))
        if name == "fly_to_location" and not isinstance(lat, (int, float)):
            lat, lon = (args or {}).get("latitude"), (args or {}).get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            if cam.get("pitchDeg") is not None and sc.get("camera"):
                extra = {"pitchDeg": cam.get("pitchDeg"), "headingDeg": cam.get("headingDeg")}
                if isinstance(cam.get("rollDeg"), (int, float)):
                    extra["rollDeg"] = cam["rollDeg"]
                sc["camera"].update(extra)
                return True
            return False
        height = cam.get("heightM") or (args or {}).get("rangeM") or (sc.get("camera") or {}).get("heightM")
        if isinstance(cam.get("targetLat"), (int, float)) and isinstance(cam.get("rangeM"), (int, float)):
            # look-at point + range: what fly_to_location needs to put the camera back where it was
            lat, lon, height = cam["targetLat"], cam["targetLon"], cam["rangeM"]
        sc["camera"] = {"lat": lat, "lon": lon, "heightM": height, "pitchDeg": cam.get("pitchDeg"),
                        "headingDeg": cam.get("headingDeg"), "rollDeg": cam.get("rollDeg")}
        return True

    @staticmethod
    def _layer_id(value) -> str | None:
        text = str(value or "")[:64]
        return text if _LAYER_ID.fullmatch(text) else None

    def _set_layer(self, sc: dict, args: dict) -> bool:
        lid = self._layer_id((args or {}).get("layerId") or (args or {}).get("id"))
        if not lid:
            return False
        layers = [x for x in (sc.get("layers") or []) if x != lid]
        if (args or {}).get("enabled") is not False:
            layers.append(lid)
        sc["layers"] = layers[:16]
        return True

    @classmethod
    def _take_layers(cls, sc: dict, result: dict) -> bool:
        raw = result.get("layers") if isinstance(result, dict) else None
        if not isinstance(raw, list) or not raw:
            return False
        ids: list[str] = []
        for layer in raw:
            lid = None
            if isinstance(layer, str):
                lid = cls._layer_id(layer)
            elif isinstance(layer, dict) and layer.get("enabled") is not False and layer.get("visible") is not False:
                lid = cls._layer_id(layer.get("id") or layer.get("layerId"))
            if lid and lid not in ids:
                ids.append(lid)
            if len(ids) >= 16:
                break
        if not ids:
            return False
        sc["layers"] = ids
        return True

    # -- queries -------------------------------------------------------------------------------
    def list(self, key: str | None, full: bool = False) -> dict:
        with self.lock:
            sc = self.scene(key)
            from .timeline import public_timeline
            out = {"ok": True, "scene": key or DEFAULT_KEY, "camera": sc.get("camera"), "drawer": sc.get("drawer"),
                   "filters": sc.get("filters"), "timeline": public_timeline(sc.get("timeline")),
                   "annotations": [_public_annotation(a, full) for a in sc["annotations"]]}
            if isinstance(sc.get("motion"), dict):
                motion = sc["motion"]
                out["motion"] = {k: motion.get(k) for k in ("kind", "state", "label", "index", "total") if k in motion}
            return out

    def delete_many(self, key: str | None, ids: list) -> list[str]:
        gone = []
        for ann_id in ids or []:
            if ann_id and self.delete(key, str(ann_id)):
                gone.append(str(ann_id))
        return gone

    def append_pins(self, key: str | None, specs: list) -> int:
        added = 0
        with self.lock:
            sc = self.scene(key)
            for spec in specs or []:
                if not isinstance(spec, dict):
                    continue
                sc["annotations"].append({"id": f"a{next(self.ids)}", "spec": dict(spec), "paneId": spec.get("paneId")})
                added += 1
            del sc["annotations"][:-MAX_ITEMS]
            if added:
                sc["updated"] = time.time()
                self._save()
        return added

    def delete(self, key: str | None, ann_id: str) -> bool:
        with self.lock:
            sc = self.scene(key)
            before = len(sc["annotations"])
            gone = [a for a in sc["annotations"] if _same_mark(a, ann_id)]
            sc["annotations"] = [a for a in sc["annotations"] if not _same_mark(a, ann_id)]
            dr = sc.get("drawer")
            for a in gone:
                if dr and a["spec"].get("latitude") == dr.get("latitude") and a["spec"].get("longitude") == dr.get("longitude"):
                    sc["drawer"] = None
            if len(sc["annotations"]) != before:
                sc["updated"] = time.time()
                self._save()
                return True
            return False


    def annotations(self, key: str | None) -> list[dict]:
        """Full annotation records (id + spec) for research / investigations."""
        with self.lock:
            return [{"id": a["id"], "paneId": a.get("paneId"), "spec": dict(a.get("spec") or {})}
                    for a in self.scene(key).get("annotations") or []]

    def update_annotation(self, key: str | None, ann_id: str, fields: dict) -> bool:
        """Merge drawer/fields into an annotation spec by scene id (a1, a2, ...) or paneId."""
        if not isinstance(fields, dict) or not ann_id:
            return False
        with self.lock:
            sc = self.scene(key)
            for a in sc["annotations"]:
                if _same_mark(a, ann_id):
                    spec = dict(a.get("spec") or {})
                    for k, v in fields.items():
                        if v is None:
                            continue
                        if k in ("description_md", "title", "label") or k in ("images", "links", "rows"):
                            spec[k] = v
                        elif k not in ("id", "flyTo"):
                            spec[k] = v
                    a["spec"] = spec
                    sc["updated"] = time.time()
                    self._save()
                    return True
            return False

    def dump(self, key: str | None) -> dict:
        with self.lock:
            sc = self.scene(key)
            out = {"scene": key or DEFAULT_KEY, "camera": sc.get("camera"), "caption": sc.get("caption"),
                   "drawer": sc.get("drawer"), "timeline": sc.get("timeline"), "updated": sc.get("updated"),
                   "annotations": [{"id": a["id"], "paneId": a.get("paneId"), "spec": dict(a.get("spec") or {})}
                                   for a in sc.get("annotations") or []]}
            if isinstance(sc.get("motion"), dict):
                out["motion"] = dict(sc["motion"])
            return out

    def load_into(self, key: str | None, data: dict) -> dict:
        """Replace the session scene with a saved investigation / dump."""
        with self.lock:
            sc = self.scene(key)
            anns = []
            for a in (data.get("annotations") or []):
                if not isinstance(a, dict):
                    continue
                spec = dict(a.get("spec") or a)
                aid = str(a.get("id") or f"a{next(self.ids)}")
                if not (aid.startswith("a") and aid[1:].isdigit()):
                    aid = f"a{next(self.ids)}"
                anns.append({"id": aid, "spec": spec, "paneId": a.get("paneId")})
            sc["annotations"] = anns[-MAX_ITEMS:]
            sc["camera"] = data.get("camera")
            sc["caption"] = data.get("caption")
            sc["drawer"] = data.get("drawer")
            sc["shapes"] = stored_shapes(data.get("shapes"))
            timeline = data.get("timeline") if isinstance(data.get("timeline"), dict) else None
            if timeline:
                from .timeline import clean_state
                sc["timeline"] = clean_state(timeline)
            else:
                sc["timeline"] = None
            sc["motion"] = data.get("motion") if isinstance(data.get("motion"), dict) else None
            sc["updated"] = time.time()
            self._save()
            return {"ok": True, "scene": key or DEFAULT_KEY, "annotations": len(sc["annotations"])}

    def restore_commands(self, key: str | None, redraw_only: bool = False, clear: bool = False) -> list[tuple[str, dict]]:
        """Commands that rebuild the scene in a fresh pane, in order."""
        with self.lock:
            sc = self.scene(key)
            cmds: list[tuple[str, dict]] = []
            cam = sc.get("camera")
            if cam and not redraw_only:
                cmds.append(("fly_to_location", {"latitude": cam["lat"], "longitude": cam["lon"],
                                                 "rangeM": max(100, min(20_000_000, int(cam.get("heightM") or 1_000_000))),
                                                 "waitForArrival": True}))
                angled = any(isinstance(cam.get(k), (int, float)) for k in ("pitchDeg", "headingDeg", "rollDeg"))
                if angled:  # exact pose, not an eased nudge
                    pose = {}
                    if isinstance(cam.get("pitchDeg"), (int, float)):
                        pose["pitchDeg"] = cam["pitchDeg"]
                    if isinstance(cam.get("headingDeg"), (int, float)):
                        pose["headingDeg"] = cam["headingDeg"]
                    if isinstance(cam.get("rollDeg"), (int, float)):
                        pose["rollDeg"] = cam["rollDeg"]
                    if cam.get("heightM") is not None:
                        pose["rangeM"] = cam.get("heightM")
                    cmds.append(("hermes_frame", pose))
            if not redraw_only:
                for lid in sc.get("layers") or []:
                    if self._layer_id(lid):
                        cmds.append(("set_layer_visibility", {"layerId": lid, "enabled": True}))
            specs = [a["spec"] for a in sc["annotations"]]
            if redraw_only or clear:
                cmds.append(("clear_annotations", {}))  # filters stay; re-applied after the redraw
            for i in range(0, len(specs), 24):
                cmds.append(("annotate_map", {"annotations": specs[i:i + 24], "persist": True}))
            if sc.get("filters") and specs:
                cmds.append(("hermes_pin_filter", dict(sc["filters"])))
            dr = sc.get("drawer")
            if dr and specs:
                cmds.append(("hermes_pin_drawer", {"action": "open", "latitude": dr["latitude"],
                                                   "longitude": dr["longitude"], "label": dr.get("label"),
                                                   "flyTo": False}))
            for sh in (sc.get("shapes") or {}).values():
                cmds.append(("hermes_draw", dict(sh)))
            if sc.get("caption") and not redraw_only:
                cmds.append(("hermes_caption", {"text": sc["caption"]}))
            tl = sc.get("timeline")
            if isinstance(tl, dict) and tl.get("mode"):
                body = dict(tl)
                body["action"] = "set"
                body["playing"] = False
                cmds.append(("hermes_timeline", body))
            return cmds
