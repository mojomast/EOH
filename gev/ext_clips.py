"""YouTube and X clips on pin drawers.

Director media packs are local scene assets and do not embed third-party
players. A clip is drawer content: title, https URL, and an optional start
second. List returns it. The pane renders it when the drawer opens.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from .ext_ops import ExtOp

YT_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,16})",
    re.I)
X_RE = re.compile(r"(?:twitter\.com|x\.com)/(?:[^/\s?#]+/status|i/status)/(\d+)", re.I)
CLOCK = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
MAX_CLIPS = 4


def live(name: str, args: dict | None = None, session_id: str | None = None):
    from .composer import live_call
    return live_call(name, args or {}, session_id=session_id)


def store():
    from . import live as live_mod
    relay = live_mod.current()
    if relay is not None and getattr(relay, "scenes", None) is not None:
        return relay.scenes
    from .scene import SceneStore, default_path
    return SceneStore(default_path())


def _fail(error: str, message: str, fix: str, **extra) -> str:
    body = {"ok": False, "error": error, "code": error, "message": message, "fix": fix}
    body.update(extra)
    return json.dumps(body, ensure_ascii=False)


def _clock(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "bad"
    if isinstance(value, (int, float)):
        number = int(value)
        return number if 0 <= number <= 86400 and float(value) == number else "bad"
    text = str(value).strip().lower()
    if text.isdigit():
        number = int(text)
        return number if 0 <= number <= 86400 else "bad"
    match = CLOCK.fullmatch(text)
    if match and any(match.groups()):
        hours, minutes, seconds = (int(part or 0) for part in match.groups())
        total = hours * 3600 + minutes * 60 + seconds
        return total if total <= 86400 else "bad"
    return "bad"


def _start(url: str, explicit):
    chosen = _clock(explicit)
    if chosen == "bad":
        return "start_sec must be seconds, or a clock such as 1m30s."
    if chosen is not None:
        return chosen
    query = parse_qs(urlparse(url).query)
    for key in ("start", "t"):
        values = query.get(key)
        if not values:
            continue
        parsed = _clock(values[0])
        if parsed == "bad":
            return None
        return parsed
    return None


def parse_clip(url, title=None, start_sec=None):
    """Return ``(clip, error)``. The clip is safe to store and to embed."""
    if not isinstance(url, str):
        return None, "url must be a YouTube or X link."
    raw = url.strip()
    if not raw.startswith("https://") or " " in raw or len(raw) > 400:
        return None, "Clip URLs must be https YouTube or X links."
    label = str(title or "").strip()[:120]
    yt = YT_RE.search(raw)
    if yt:
        start = _start(raw, start_sec)
        if isinstance(start, str):
            return None, start
        video = yt.group(1)
        embed = f"https://www.youtube-nocookie.com/embed/{video}"
        if start:
            embed += f"?start={start}"
        clip = {
            "provider": "youtube",
            "url": f"https://www.youtube.com/watch?v={video}",
            "embed": embed,
            "title": label or "YouTube",
        }
        if start:
            clip["start_sec"] = start
        return clip, None
    status = X_RE.search(raw)
    if status:
        if start_sec not in (None, "", 0) and _clock(start_sec) not in (None, 0, "bad"):
            return None, "X clips do not take a start time."
        if _clock(start_sec) == "bad":
            return None, "start_sec must be seconds, or a clock such as 1m30s."
        clip = {
            "provider": "x",
            "url": f"https://x.com/i/status/{status.group(1)}",
            "embed": f"https://platform.twitter.com/embed/Tweet.html?id={status.group(1)}",
            "title": label or "X",
        }
        return clip, None
    return None, "Clips accept YouTube and X status URLs."


def _match(records: list[dict], args: dict):
    ident = str(args.get("id") or "").strip()
    label = str(args.get("label") or "").strip().lower()
    for ann in records:
        spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else {}
        keys = {str(ann.get("id") or ""), str(ann.get("paneId") or ""), str(spec.get("id") or ""), str(spec.get("clientId") or "")}
        if ident and ident in keys:
            return ann
    if label:
        for ann in records:
            spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else {}
            if str(spec.get("label") or spec.get("title") or "").strip().lower() == label:
                return ann
    return None


def _merge(existing, clip: dict) -> tuple[list, str | None]:
    clips = []
    replaced = False
    for item in existing or []:
        if not isinstance(item, dict):
            continue
        if item.get("url") == clip["url"]:
            merged = dict(clip)
            if "start_sec" not in clip and isinstance(item.get("start_sec"), int):
                merged["start_sec"] = item["start_sec"]
                embed = merged.get("embed") or ""
                if merged.get("provider") == "youtube" and "start=" not in embed:
                    merged["embed"] = embed + f"?start={item['start_sec']}"
            clips.append(merged)
            replaced = True
        else:
            clips.append(item)
    if not replaced:
        if len(clips) >= MAX_CLIPS:
            return clips, f"A pin holds at most {MAX_CLIPS} clips."
        clips.append(clip)
    return clips[:MAX_CLIPS], None


def attach(args: dict, **kw) -> str:
    clip, error = parse_clip(args.get("url"), args.get("title"), args.get("start_sec"))
    if error:
        return _fail("bad_args", error, "Pass a https YouTube or X status URL. YouTube accepts start_sec.")
    session_id = kw.get("session_id")
    book = store()
    records = book.annotations(session_id)
    ann = _match(records, args)
    if ann is None:
        labels = []
        for item in records[:8]:
            spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
            label = spec.get("label") or spec.get("title")
            if label:
                labels.append(str(label)[:40])
        return _fail(
            "pin_not_found",
            "No stored pin matched that id or label.",
            "Annotate a pin, then retry godseye_annotate op=clip with its id or label.",
            pins=labels,
        )
    spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else {}
    clips, error = _merge(spec.get("clips"), clip)
    if error:
        return _fail("too_many", error, "Replace an existing clip url or delete the pin's clips first.")
    if not book.update_annotation(session_id, str(ann.get("id") or ""), {"clips": clips}):
        return _fail("pin_not_found", "The pin disappeared before the clip was saved.",
                     "List pins and retry godseye_annotate op=clip.")
    pane_updated = False
    pane_error = None
    try:
        pane = live("hermes_pin_update", {
            "id": ann.get("paneId") or ann.get("id"),
            "label": spec.get("label"),
            "title": spec.get("title") or spec.get("label"),
            "clips": clips,
        }, session_id=session_id)
    except Exception as exc:
        pane = {"ok": False, "error": str(exc)}
    if isinstance(pane, dict) and pane.get("ok") is not False and pane.get("error") in (None, ""):
        pane_updated = True
    elif isinstance(pane, dict):
        pane_error = pane.get("error") or pane.get("code")
    body = {
        "ok": True,
        "op": "clip",
        "id": ann.get("id"),
        "clips": clips,
        "pane_updated": pane_updated,
    }
    if not pane_updated:
        body["pane"] = pane_error or "not_on_pane"
        body["message"] = "Clip stored. The open drawer updates when this pin is on the pane."
    return json.dumps(body, ensure_ascii=False)


EXT_OPS = [
    ExtOp(
        tool="godseye_annotate",
        op="clip",
        handler=attach,
        props={
            "id": {"type": "string", "description": "Pin id from godseye_annotate op=list."},
            "label": {"type": "string", "description": "Pin label, when id is omitted."},
            "url": {"type": "string", "required": True, "description": "https YouTube or X status URL."},
            "title": {"type": "string", "description": "Drawer title for the clip."},
            "start_sec": {"type": "integer", "description": "YouTube start time in seconds. X ignores this."},
        },
        blurb="Attach a YouTube or X clip to a pin drawer. List returns it. The same URL updates in place.",
    ),
]
