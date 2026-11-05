"""Per-session scene store (annotations + camera), kept by the live relay so a pane that is
reopened, reloaded or rotated gets its scene back without the agent redrawing or re-geocoding.

Annotations are stored with the coordinates the pane resolved, so a restore never geocodes."""
from __future__ import annotations

import itertools
import json
import os
import threading
import time
from pathlib import Path

DEFAULT_KEY = "default"
MAX_ITEMS = 96
POINT_TYPES = {"pin", "highlight", "label", "area", "card"}


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
        return bool(scene.get("annotations") or scene.get("camera"))

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
            elif name == "hermes_caption":
                sc["caption"] = (args or {}).get("text") or None
            elif name in ("get_current_view_state", "fly_to_location", "move_camera"):
                changed = self._camera(sc, name, args, result)
            else:
                changed = False
            if changed:
                sc["updated"] = time.time()
                self._save()

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
                sc["camera"].update(pitchDeg=cam.get("pitchDeg"), headingDeg=cam.get("headingDeg"))
                return True
            return False
        height = cam.get("heightM") or (args or {}).get("rangeM") or (sc.get("camera") or {}).get("heightM")
        if isinstance(cam.get("targetLat"), (int, float)) and isinstance(cam.get("rangeM"), (int, float)):
            # look-at point + range: what fly_to_location needs to put the camera back where it was
            lat, lon, height = cam["targetLat"], cam["targetLon"], cam["rangeM"]
        sc["camera"] = {"lat": lat, "lon": lon, "heightM": height, "pitchDeg": cam.get("pitchDeg"),
                        "headingDeg": cam.get("headingDeg")}
        return True

    # -- queries -------------------------------------------------------------------------------
    def list(self, key: str | None) -> dict:
        with self.lock:
            sc = self.scene(key)
            return {"ok": True, "scene": key or DEFAULT_KEY, "camera": sc.get("camera"),
                    "annotations": [{"id": a["id"], "type": a["spec"].get("type", "pin"),
                                     "label": a["spec"].get("label") or a["spec"].get("target"),
                                     "latitude": a["spec"].get("latitude"), "longitude": a["spec"].get("longitude")}
                                    for a in sc["annotations"]]}

    def delete(self, key: str | None, ann_id: str) -> bool:
        with self.lock:
            sc = self.scene(key)
            before = len(sc["annotations"])
            sc["annotations"] = [a for a in sc["annotations"] if a["id"] != ann_id]
            if len(sc["annotations"]) != before:
                sc["updated"] = time.time()
                self._save()
                return True
            return False

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
                if isinstance(cam.get("pitchDeg"), (int, float)):  # exact pose, not an eased nudge
                    cmds.append(("hermes_frame", {"pitchDeg": cam["pitchDeg"], "headingDeg": cam.get("headingDeg"),
                                                  "rangeM": cam.get("heightM")}))
            specs = [a["spec"] for a in sc["annotations"]]
            if redraw_only or clear:
                cmds.append(("clear_annotations", {}))
            for i in range(0, len(specs), 24):
                cmds.append(("annotate_map", {"annotations": specs[i:i + 24], "persist": True}))
            if sc.get("caption") and not redraw_only:
                cmds.append(("hermes_caption", {"text": sc["caption"]}))
            return cmds
