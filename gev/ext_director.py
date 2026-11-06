"""Director Mode scenes on godseye_scene.

God's Eye View keeps Director on ``window.__godsEyeView.sceneDirector``.
Voice ``control_scene`` only lists, plays, stops, and advances. A new story
is a scene document applied with ``importProjectFile`` onto the current
project (other scenes stay). YouTube and X URLs are not Director media packs;
those belong on ``godseye_annotate`` ``op=clip``.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from .ext_ops import ExtOp

HERE = {"here", "this area", "the view", "current view", "this view"}
STYLES = {"normal", "retro", "surveillance", "thermal", "anime", "noir"}
STYLE_ALIASES = {
    "nvg": "surveillance", "night": "surveillance", "night vision": "surveillance",
    "night-vision": "surveillance", "flir": "thermal", "ir": "thermal", "infrared": "thermal",
}
LAYER_WORDS = (
    ("wildfire", "local-firms"), ("fires", "local-firms"), ("fire", "local-firms"),
    ("aircraft", "flights"), ("flights", "flights"), ("flight", "flights"),
    ("vessels", "ais-live-vessels"), ("ships", "ais-live-vessels"), ("ship", "ais-live-vessels"),
    ("satellite", "satellites"), ("weather", "weather-radar"),
)
ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,80}$")
ASSET_PART = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
URL_RE = re.compile(r"https?://[^\s)>\]]+")
YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,16})",
    re.I)
X_RE = re.compile(r"(?:twitter\.com|x\.com)/[^/\s]+/status/(\d+)", re.I)
MAX_SHOTS = 24


def live(name: str, args: dict | None = None, session_id: str | None = None):
    from .composer import live_call
    return live_call(name, args or {}, session_id=session_id)


def geocode(place: str):
    from .poi import geocode as lookup
    return lookup(place)


def camera(session_id):
    from .router import camera as read
    return read(session_id)


def _store_path() -> Path:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    return home / "plugin-data" / "godseye" / "director.json"


def _load() -> dict:
    path = _store_path()
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    scenes = data.get("scenes") if isinstance(data, dict) else None
    return scenes if isinstance(scenes, dict) else {}


def _save(scenes: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"scenes": scenes}, ensure_ascii=False))
    tmp.replace(path)


def _fail(error: str, message: str, fix: str, **extra) -> str:
    body = {"ok": False, "error": error, "code": error, "message": message, "fix": fix}
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _slug(text: str, fallback: str = "scene") -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40]
    if not raw or not raw[0].isalnum():
        raw = fallback
    return "hermes-" + raw


def _style(text: str) -> str | None:
    lowered = text.lower()
    for alias, name in STYLE_ALIASES.items():
        if alias in lowered:
            return name
    for name in STYLES:
        if name in lowered:
            return name
    return None


def _layers_from(text: str) -> dict:
    lowered = f" {text.lower()} "
    found = {}
    for word, layer in LAYER_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            found[layer] = {"enabled": True}
    return found


def _third_party(text: str) -> list[dict]:
    clips = []
    seen = set()
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,)")
        if url in seen:
            continue
        if YT_RE.search(url) or X_RE.search(url):
            seen.add(url)
            clips.append({"url": url})
    return clips


def _asset_path(value) -> str | None:
    if not isinstance(value, str):
        return None
    path = value.strip().lstrip("./")
    if not path or path.startswith(("/", "\\")) or "\\" in path or ".." in path.split("/"):
        return None
    parts = path.split("/")
    if not parts or any(not ASSET_PART.match(part) for part in parts):
        return None
    return path


def _offset(lat: float, lon: float, east_km: float, north_km: float) -> tuple[float, float]:
    lat2 = max(-85.0, min(85.0, lat + north_km / 111.32))
    lon2 = lon + east_km / (111.32 * max(0.2, math.cos(math.radians(lat))))
    lon2 = ((lon2 + 180.0) % 360.0) - 180.0
    return round(lat2, 5), round(lon2, 5)


def _pose(lat: float, lon: float, alt: float, heading: float, pitch: float) -> dict:
    return {
        "lat": lat, "lon": lon, "alt": alt,
        "heading": heading, "pitch": pitch, "roll": 0,
    }


def _shot(ident: str, title: str, camera: dict, duration: float, hold: float,
          layers: dict | None = None, style: str | None = None) -> dict:
    shot = {
        "id": ident, "title": title,
        "durationSec": duration, "holdSec": hold, "camera": camera,
    }
    if layers:
        shot["layers"] = layers
    if style and style != "normal":
        shot["visual"] = {"style": style}
    return shot


def _template(lat: float, lon: float, title: str, layers: dict, style: str | None) -> list[dict]:
    west_lat, west_lon = _offset(lat, lon, -4.0, 0.4)
    east_lat, east_lon = _offset(lat, lon, 0.7, -0.3)
    return [
        _shot("approach", "Approach", _pose(west_lat, west_lon, 6500, 90, -32), 6, 1, layers, style),
        _shot("overhead", "Overhead", _pose(round(lat, 5), round(lon, 5), 2200, 0, -70), 5, 2, layers, style),
        _shot("detail", "Detail", _pose(east_lat, east_lon, 800, 200, -25), 4, 2, layers, style),
    ]


def _check_id(value, label: str):
    if not isinstance(value, str) or not ID_OK.match(value):
        return f"{label} must be an id of letters, numbers, and . _ : -."
    return None


def _check_pose(pose: dict, label: str, explicit: bool):
    if not isinstance(pose, dict):
        return f"{label} must be a camera pose."
    lat, lon = _num(pose.get("lat")), _num(pose.get("lon"))
    alt = _num(pose.get("alt"))
    if lat is None or not -90 <= lat <= 90 or lon is None or not -180 <= lon <= 180:
        return f"{label} needs lat and lon in range."
    if explicit and (alt is None or not -12000 <= alt <= 1_000_000_000):
        return f"{label} needs an ellipsoid altitude in meters."
    if alt is not None and not -12000 <= alt <= 1_000_000_000:
        return f"{label} altitude is out of range."
    for key, lo, hi in (("heading", -360, 360), ("pitch", -90, 90), ("roll", -360, 360)):
        number = _num(pose.get(key))
        if number is not None and not lo <= number <= hi:
            return f"{label} {key} is out of range."
    return None


def _normalize_layers(value):
    if value is None:
        return {}, None
    if isinstance(value, list):
        layers = {}
        for item in value:
            if not isinstance(item, str) or not ID_OK.match(item):
                return None, "Layer ids must be short names such as local-firms or flights."
            layers[item] = {"enabled": True}
        return layers, None
    if not isinstance(value, dict):
        return None, "layers must be a list of ids or {id: true}."
    layers = {}
    for key, entry in value.items():
        if not isinstance(key, str) or not ID_OK.match(key):
            return None, "Layer ids must be short names such as local-firms or flights."
        if isinstance(entry, bool):
            layers[key] = {"enabled": entry}
        elif isinstance(entry, dict) and isinstance(entry.get("enabled"), bool):
            layers[key] = {"enabled": entry["enabled"]}
        else:
            return None, f"Layer {key} must be true, false, or {{enabled: true}}."
    return layers, None


def _media_from(items, lat: float, lon: float, alt: float):
    """Local scene-asset media packs. Third-party URLs are returned aside."""
    anchors, packs, refused = [], [], []
    for item in items or []:
        if not isinstance(item, dict):
            return None, None, None, "media entries must be objects."
        url = item.get("url")
        if isinstance(url, str) and (YT_RE.search(url) or X_RE.search(url)):
            refused.append({"url": url.strip(), "title": item.get("title") or "", "start_sec": item.get("start_sec")})
            continue
        path = _asset_path(item.get("asset") or item.get("path"))
        if path is None:
            if isinstance(url, str):
                refused.append({"url": url.strip(), "title": item.get("title") or ""})
                continue
            return None, None, None, "A media pack asset is a relative path such as scene/clip.mp4, not a URL."
        text = item.get("attribution") or item.get("title")
        license_name = item.get("license")
        if not isinstance(text, str) or not text.strip() or not isinstance(license_name, str) or not license_name.strip():
            return None, None, None, "A Director media pack needs attribution and license. YouTube and X are pin clips, not packs."
        anchor_id = f"media-{len(anchors) + 1}"
        anchors.append({
            "id": anchor_id,
            "title": str(item.get("title") or "Media")[:120],
            "lat": lat, "lon": lon, "alt": alt if alt is not None else 300,
            "altitudeReference": "ellipsoid",
        })
        packs.append({
            "id": f"pack-{len(packs) + 1}",
            "version": 1,
            "format": "media",
            "source": {"adapter": "assets", "path": path},
            "attribution": {"text": text.strip()[:200], "license": license_name.strip()[:200]},
            "placement": {"anchorId": anchor_id},
        })
    return anchors, packs, refused, None


def _normalize_shot(raw, index: int):
    if not isinstance(raw, dict):
        return None, "Each shot must be an object."
    ident = raw.get("id") or f"shot-{index + 1}"
    error = _check_id(ident, "shot id")
    if error:
        return None, error
    title = str(raw.get("title") or f"Shot {index + 1}")[:120]
    duration = _num(raw.get("durationSec", raw.get("duration_sec", 4)))
    hold = _num(raw.get("holdSec", raw.get("hold_sec", 1)))
    if duration is None or not 0 <= duration <= 86400 or hold is None or not 0 <= hold <= 86400:
        return None, "durationSec and holdSec must be seconds from 0 to 86400."
    camera = raw.get("camera") if isinstance(raw.get("camera"), dict) else {}
    if "lat" not in camera and _num(raw.get("lat")) is not None:
        camera = {
            "lat": raw.get("lat"), "lon": raw.get("lon"),
            "alt": raw.get("alt", raw.get("altitude_m", 2200)),
            "heading": raw.get("heading", 0), "pitch": raw.get("pitch", -45), "roll": 0,
        }
    error = _check_pose(camera, f"shot {ident}", True)
    if error:
        return None, error
    layers, error = _normalize_layers(raw.get("layers"))
    if error:
        return None, error
    shot = {
        "id": ident, "title": title,
        "durationSec": int(duration) if float(duration).is_integer() else duration,
        "holdSec": int(hold) if float(hold).is_integer() else hold,
        "camera": {
            "lat": _num(camera.get("lat")), "lon": _num(camera.get("lon")),
            "alt": _num(camera.get("alt")),
            "heading": _num(camera.get("heading")) or 0,
            "pitch": -35 if _num(camera.get("pitch")) is None else _num(camera.get("pitch")),
            "roll": _num(camera.get("roll")) or 0,
        },
    }
    if layers:
        shot["layers"] = layers
    pack_ids = raw.get("dataPackIds")
    if isinstance(pack_ids, list) and pack_ids and all(isinstance(item, str) and ID_OK.match(item) for item in pack_ids):
        shot["dataPackIds"] = pack_ids[:8]
    style = raw.get("style")
    visual = raw.get("visual") if isinstance(raw.get("visual"), dict) else {}
    style = style or visual.get("style")
    if isinstance(style, str):
        mapped = STYLES.get(STYLE_ALIASES.get(style.strip().lower(), style.strip().lower()))
        if mapped is None:
            return None, f"Unknown style {style!r}."
        if mapped != "normal":
            shot["visual"] = {"style": mapped}
    move = raw.get("move")
    if isinstance(move, dict):
        easing = move.get("easing")
        if easing not in ("linear", "cubic-in-out"):
            return None, "move.easing must be linear or cubic-in-out."
        origin = move.get("from") if isinstance(move.get("from"), dict) else None
        error = _check_pose(origin or {}, f"shot {ident} move.from", True)
        if error:
            return None, error
        if duration < 0.2:
            return None, "An explicit camera move needs durationSec of at least 0.2."
        shot["move"] = {
            "from": {
                "lat": _num(origin.get("lat")), "lon": _num(origin.get("lon")),
                "alt": _num(origin.get("alt")),
                "heading": _num(origin.get("heading")) or 0,
                "pitch": -35 if _num(origin.get("pitch")) is None else _num(origin.get("pitch")),
                "roll": _num(origin.get("roll")) or 0,
                "altitudeReference": "ellipsoid",
            },
            "easing": easing,
        }
        shot["camera"]["altitudeReference"] = "ellipsoid"
    return shot, None


def _point_of(scene: dict):
    for shot in scene.get("shots") or []:
        camera = shot.get("camera") if isinstance(shot, dict) else None
        if isinstance(camera, dict) and _num(camera.get("lat")) is not None and _num(camera.get("lon")) is not None:
            return _num(camera["lat"]), _num(camera["lon"]), _num(camera.get("alt")) or 300
    return None


def build_scene(args: dict, session_id=None):
    """Return ``(scene, notes, error_json)``."""
    args = dict(args or {})
    brief = args.get("brief")
    if brief is not None and not isinstance(brief, str):
        return None, None, _fail("bad_args", "brief must be text.", "Pass a short description of the scene.")
    brief = brief.strip() if isinstance(brief, str) else ""
    supplied = args.get("scene") if isinstance(args.get("scene"), dict) else {}
    raw_shots = args.get("shots") if isinstance(args.get("shots"), list) else supplied.get("shots")
    title = args.get("title") or supplied.get("title") or ""
    if not isinstance(title, str):
        return None, None, _fail("bad_args", "title must be text.", "Pass a short scene title.")
    title = title.strip()
    if not title:
        title = (brief or args.get("place") or "Hermes scene").strip().split("\n", 1)[0][:80]
    ident = args.get("id") or supplied.get("id") or _slug(title)
    error = _check_id(str(ident), "scene id")
    if error:
        return None, None, _fail("bad_args", error, "Use an id of letters, numbers, and . _ : -.")

    notes = {"third_party_media": _third_party(brief)}

    text = " ".join(part for part in (brief, title, str(args.get("place") or "")) if part)
    layers = _layers_from(text)
    style = _style(text)
    point = None
    shots = []
    if raw_shots:
        if len(raw_shots) > MAX_SHOTS:
            return None, None, _fail("bad_args", f"A Hermes scene has at most {MAX_SHOTS} shots.",
                                     "Shorten the scene and retry.")
        ids = set()
        for index, raw in enumerate(raw_shots):
            shot, error = _normalize_shot(raw, index)
            if error:
                return None, None, _fail("bad_scene", error, "Fix the shot camera and retry.")
            if shot["id"] in ids:
                return None, None, _fail("bad_scene", f"Duplicate shot id {shot['id']}.", "Give each shot its own id.")
            ids.add(shot["id"])
            if layers and "layers" not in shot:
                shot["layers"] = layers
            shots.append(shot)
        point = _point_of({"shots": shots})

    if point is None:
        lat, lon = _num(args.get("lat")), _num(args.get("lon"))
        place = args.get("place")
        if isinstance(place, str) and place.strip().lower() in HERE:
            place = None
        if lat is None or lon is None:
            if isinstance(place, str) and place.strip():
                found = geocode(place.strip())
                if not found or _num(found.get("lat")) is None:
                    return None, None, _fail(
                        "geocode_failed", f"Could not locate {place.strip()!r}.",
                        "Pass lat and lon, or open the pane and use place=here.")
                lat, lon = float(found["lat"]), float(found["lon"])
                notes["place"] = str(found.get("name") or place)[:160]
            else:
                located, err = camera(session_id)
                if err:
                    message = err.get("message") if isinstance(err, dict) else "The pane has no camera."
                    return None, None, _fail(
                        "here_unavailable", str(message),
                        "Pass place, lat and lon, or a scene whose shots include cameras.")
                lat, lon = located["lat"], located["lon"]
                notes["place"] = "here"
        if not shots:
            shots = _template(lat, lon, title, layers, style)
        point = (lat, lon, 300)

    anchors, packs, refused, error = _media_from(supplied.get("media"), point[0], point[1], point[2] or 300)
    if error:
        return None, None, _fail("bad_scene", error,
                                 "Use a scene-asset path with attribution and license, or attach YouTube/X with godseye_annotate op=clip.")
    seen = {clip.get("url") for clip in notes["third_party_media"]}
    for clip in refused or []:
        url = clip.get("url")
        if url and url not in seen and (YT_RE.search(url) or X_RE.search(url)):
            seen.add(url)
            notes["third_party_media"].append({
                "url": url,
                **({"title": clip["title"]} if clip.get("title") else {}),
                **({"start_sec": clip["start_sec"]} if clip.get("start_sec") is not None else {}),
            })
    scene = {"id": str(ident), "title": title[:120], "shots": shots}
    layer_ids = []
    for shot in shots:
        for layer_id, entry in (shot.get("layers") or {}).items():
            enabled = entry.get("enabled") if isinstance(entry, dict) else entry
            if enabled and layer_id not in layer_ids:
                layer_ids.append(layer_id)
    if layer_ids:
        scene["releaseLayerIds"] = layer_ids
    if anchors:
        scene["anchors"] = anchors
    elif isinstance(supplied.get("anchors"), list):
        scene["anchors"] = supplied["anchors"]
    if packs:
        scene["dataPacks"] = packs
        scene["shots"][0]["dataPackIds"] = [pack["id"] for pack in packs]
    elif isinstance(supplied.get("dataPacks"), list):
        scene["dataPacks"] = supplied["dataPacks"]
    if not layer_ids and isinstance(supplied.get("releaseLayerIds"), list):
        scene["releaseLayerIds"] = [item for item in supplied["releaseLayerIds"] if isinstance(item, str)]
    notes["layers"] = list(scene.get("releaseLayerIds") or [])
    return scene, notes, None


def _summary(scene: dict, applied: bool, notes: dict) -> str:
    names = ", ".join(shot["title"] for shot in scene["shots"][:6])
    where = "applied on the pane" if applied else "not applied"
    text = f"Director scene {scene['title']} ({scene['id']}): {len(scene['shots'])} shots ({names}), {where}."
    if notes.get("third_party_media"):
        text += " YouTube and X are not Director media; attach them with godseye_annotate op=clip."
    return text


def _public(scene: dict, applied: bool | None = None) -> dict:
    out = {
        "id": scene["id"],
        "title": scene["title"],
        "shots": [
            {"id": shot["id"], "title": shot.get("title"),
             "durationSec": shot.get("durationSec"), "holdSec": shot.get("holdSec"),
             "camera": {key: shot["camera"][key] for key in ("lat", "lon", "alt", "heading", "pitch")
                        if isinstance(shot.get("camera"), dict) and key in shot["camera"]}}
            for shot in scene.get("shots") or []
        ],
    }
    if scene.get("releaseLayerIds"):
        out["layers"] = list(scene["releaseLayerIds"])
    if scene.get("dataPacks"):
        out["media"] = [{"id": pack["id"], "path": (pack.get("source") or {}).get("path")}
                        for pack in scene["dataPacks"]]
    if applied is not None:
        out["applied"] = applied
    return out


def _remember(scene: dict, applied: bool) -> None:
    scenes = _load()
    scenes[scene["id"]] = {"id": scene["id"], "title": scene["title"], "applied": applied, "document": scene}
    _save(scenes)


def _stored(scene_id: str) -> dict | None:
    record = _load().get(scene_id)
    if not isinstance(record, dict):
        return None
    document = record.get("document")
    return document if isinstance(document, dict) else None


def _pane_error(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return _fail("director_unavailable", "Director did not answer.",
                     "Open a God's Eye View v0.3 pane and retry.")
    code = payload.get("code") or payload.get("error")
    if payload.get("ok") is False and code in {
        "no_pane", "pane_lost", "pane_busy", "relay_unavailable", "timeout",
        "director_unavailable", "director_import_unavailable", "director_project_unreadable",
        "director_import_failed", "director_import_rejected", "scene_not_found",
        "scene_running", "director_play_failed", "director_stop_failed",
    }:
        return json.dumps(payload, ensure_ascii=False)
    if payload.get("ok") is False:
        return json.dumps(payload, ensure_ascii=False)
    return None


def _call(action: str, session_id, **args) -> dict:
    try:
        payload = live("hermes_director", {"action": action, **args}, session_id=session_id)
    except Exception as exc:
        return {"ok": False, "error": "director_unavailable", "code": "director_unavailable",
                "message": f"Director call failed: {exc}.",
                "fix": "Open a God's Eye View v0.3 pane and retry."}
    return payload if isinstance(payload, dict) else {
        "ok": False, "error": "director_unavailable", "code": "director_unavailable",
        "message": "Director returned nothing.",
        "fix": "Open a God's Eye View v0.3 pane and retry.",
    }


def compose(args: dict, **kw) -> str:
    session_id = kw.get("session_id")
    scene, notes, error = build_scene(args, session_id)
    if error:
        return error
    version = 6 if scene.get("anchors") or scene.get("dataPacks") else None
    payload = _call("apply", session_id, scene=scene, mode="compose",
                    **({"version": version} if version else {}))
    failed = _pane_error(payload)
    applied = bool(payload.get("applied")) and not failed
    _remember(scene, applied)
    body = {
        "ok": applied,
        "op": "director",
        **_public(scene, applied),
        "summary": _summary(scene, applied, notes),
    }
    if notes.get("third_party_media"):
        body["third_party_media"] = notes["third_party_media"]
        body["clip_fix"] = "Attach each url with godseye_annotate op=clip on a pin at this place."
    if failed:
        try:
            pane = json.loads(failed)
        except ValueError:
            pane = {"error": "director_unavailable", "message": failed}
        body["ok"] = False
        body["error"] = pane.get("error") or pane.get("code") or "director_unavailable"
        body["code"] = body["error"]
        body["message"] = pane.get("message") or "Director did not apply the scene."
        body["fix"] = pane.get("fix") or "Open a God's Eye View v0.3 pane and retry godseye_scene op=director."
        return json.dumps(body, ensure_ascii=False)
    return json.dumps(body, ensure_ascii=False)


def update(args: dict, **kw) -> str:
    scene_id = str(args.get("id") or "").strip()
    error = _check_id(scene_id, "id")
    if error:
        return _fail("bad_args", error, "Pass id from godseye_scene op=director_list.")
    stored = _stored(scene_id)
    merged = dict(args)
    merged["id"] = scene_id
    if stored and not args.get("scene") and not args.get("shots"):
        merged["scene"] = stored
        if args.get("title"):
            merged["title"] = args["title"]
        if args.get("brief") or args.get("place") or args.get("lat") is not None:
            # A new brief rebuilds shots at the same id unless shots were supplied.
            merged.pop("scene", None)
            if not args.get("title"):
                merged["title"] = stored.get("title") or scene_id
    elif stored and isinstance(args.get("scene"), dict):
        scene = dict(args["scene"])
        scene["id"] = scene_id
        merged["scene"] = scene
    scene, notes, error = build_scene(merged, kw.get("session_id"))
    if error:
        return error
    scene["id"] = scene_id
    version = 6 if scene.get("anchors") or scene.get("dataPacks") else None
    payload = _call("apply", kw.get("session_id"), scene=scene, mode="update",
                    **({"version": version} if version else {}))
    failed = _pane_error(payload)
    applied = bool(payload.get("applied")) and not failed
    if stored or applied:
        _remember(scene, applied)
    if failed:
        try:
            pane = json.loads(failed)
        except ValueError:
            pane = {}
        return _fail(
            pane.get("error") or "director_unavailable",
            pane.get("message") or "Director did not update the scene.",
            pane.get("fix") or "Compose the scene on an open v0.3 pane, then update it.",
            **_public(scene, False),
        )
    body = {"ok": True, "op": "director_update", **_public(scene, True),
            "summary": _summary(scene, True, notes or {})}
    return json.dumps(body, ensure_ascii=False)


def director_list(args: dict, **kw) -> str:
    payload = _call("list", kw.get("session_id"))
    failed = _pane_error(payload)
    local = _load()
    if failed:
        try:
            pane = json.loads(failed)
        except ValueError:
            pane = {"error": "director_unavailable", "message": failed}
        unapplied = [_public(record["document"], False) for record in local.values()
                     if isinstance(record, dict) and isinstance(record.get("document"), dict)]
        pane = dict(pane)
        pane["op"] = "director_list"
        if unapplied:
            pane["local"] = [{"id": item["id"], "title": item["title"], "shots": len(item["shots"]), "applied": False}
                             for item in unapplied]
            pane["message"] = (pane.get("message") or "Director is not available on this pane.") + " Local scenes were not applied."
        return json.dumps(pane, ensure_ascii=False)
    scenes = []
    for item in payload.get("scenes") or []:
        if not isinstance(item, dict):
            continue
        row = {"id": item.get("id"), "title": item.get("title"), "shots": item.get("shots")}
        if item.get("id") in local:
            row["hermes"] = True
        scenes.append(row)
    known = {row["id"] for row in scenes}
    for scene_id, record in local.items():
        if scene_id in known or not isinstance(record, dict):
            continue
        document = record.get("document")
        if isinstance(document, dict):
            scenes.append({"id": scene_id, "title": record.get("title"),
                           "shots": len(document.get("shots") or []),
                           "applied": False, "hermes": True})
    return json.dumps({
        "ok": True, "op": "director_list", "scenes": scenes,
        "running": bool(payload.get("running")),
        "selectedSceneId": payload.get("selectedSceneId"),
        "count": len(scenes),
    }, ensure_ascii=False)


def director_play(args: dict, **kw) -> str:
    scene_id = str(args.get("id") or "").strip()
    if not scene_id:
        return _fail("bad_args", "Play needs a scene id or title.", "Pass id from godseye_scene op=director_list.")
    payload = _call("play", kw.get("session_id"), id=scene_id)
    failed = _pane_error(payload)
    if failed:
        return failed
    return json.dumps({
        "ok": True, "op": "director_play",
        "id": payload.get("id") or scene_id,
        "playing": payload.get("playing"),
        "shots": payload.get("shots"),
    }, ensure_ascii=False)


def director_stop(args: dict, **kw) -> str:
    payload = _call("stop", kw.get("session_id"))
    failed = _pane_error(payload)
    if failed:
        return failed
    return json.dumps({"ok": True, "op": "director_stop", "running": False}, ensure_ascii=False)


_SCENE = {"type": "object", "description": "Scene document: id, title, shots[{camera, durationSec, holdSec, layers}], optional media asset packs."}
_SHOTS = {"type": "array", "items": {"type": "object"}, "description": "Shots with camera lat, lon, alt and optional layers."}

EXT_OPS = [
    ExtOp(
        tool="godseye_scene",
        op="director",
        handler=compose,
        props={
            "brief": {"type": "string", "description": "Short description. Builds approach, overhead, and detail shots."},
            "place": {"type": "string", "description": "Place name, or here for the open pane."},
            "lat": {"type": "number"},
            "lon": {"type": "number"},
            "title": {"type": "string"},
            "scene": _SCENE,
            "shots": _SHOTS,
        },
        blurb="Build a Director scene from a brief or scene document and apply it. YouTube and X are pin clips, not scene media.",
    ),
    ExtOp(
        tool="godseye_scene",
        op="director_update",
        handler=update,
        props={
            "id": {"type": "string", "required": True, "description": "Scene id to replace."},
            "brief": {"type": "string"},
            "place": {"type": "string"},
            "lat": {"type": "number"},
            "lon": {"type": "number"},
            "title": {"type": "string"},
            "scene": _SCENE,
            "shots": _SHOTS,
        },
        blurb="Replace one Director scene by id and apply it again.",
    ),
    ExtOp(
        tool="godseye_scene",
        op="director_list",
        handler=director_list,
        props={},
        blurb="List Director scenes on the pane, including Hermes scenes that were not applied.",
    ),
    ExtOp(
        tool="godseye_scene",
        op="director_play",
        handler=director_play,
        props={"id": {"type": "string", "required": True, "description": "Scene id or title. Playback returns immediately."}},
        blurb="Play one Director scene. Stop with op=director_stop.",
    ),
    ExtOp(
        tool="godseye_scene",
        op="director_stop",
        handler=director_stop,
        props={},
        blurb="Stop Director playback.",
    ),
]
