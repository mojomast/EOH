"""Time slider for the Eye Of Hermes pane.

Two modes share one control:

- ``wayback`` scrubs Esri World Imagery Wayback release dates over the current view.
  The catalog is cached (see ``measure.wayback_releases``). Tiles are for the pane to
  display. This module does not download them and does not read photorealistic 3D tiles.
- ``events`` shows USGS earthquakes and NASA FIRMS fires, plus stored watch alerts, as
  pins that appear when the playhead reaches their time. FIRMS' area API only returns
  the last 10 days; older fires come from the watch history.

The scene stores the slider. A reopened pane and WebGL recovery replay it.
"""
from __future__ import annotations

import re
import time

from . import measure as M
from . import watch

MODES = ("wayback", "events")
FEEDS = ("earthquakes", "fires", "fire_perimeters")
TRANSITIONS = ("swipe", "fade")
MAX_RELEASES = 48
MAX_EVENTS = 80
FIRMS_DAYS = 10
_DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")
_FIELDS = ("mode", "from", "to", "at", "playing", "transition", "feeds", "index")


def parse_when(value) -> str | None:
    """UTC ``YYYY-MM-DDTHH:MM:SSZ``, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        sec = float(value)
        if sec > 1e12:
            sec /= 1000.0
        if sec <= 1e9:
            return None
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sec))
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value or "").strip().replace("UTC", "").replace("utc", "").strip()
    if not text:
        return None
    matched = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2})(\d{2})$", text)
    if matched:
        return f"{matched.group(1)}T{matched.group(2)}:{matched.group(3)}:00Z"
    matched = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?", text)
    if matched:
        return f"{matched.group(1)}T{matched.group(2)}:{matched.group(3)}:{matched.group(4) or '00'}Z"
    matched = _DATE.match(text)
    if not matched:
        return None
    year, month, day = matched.group(1), matched.group(2) or "01", matched.group(3) or "01"
    return f"{year}-{month}-{day}T00:00:00Z"


def day_of(stamp: str | None) -> str:
    return (stamp or "")[:10]


def _day_stamp(day: str, end: bool) -> str:
    return f"{day}T23:59:59Z" if end else f"{day}T00:00:00Z"


def _span_days(start: str, end: str) -> int:
    try:
        a = time.strptime(start[:10], "%Y-%m-%d")
        b = time.strptime(end[:10], "%Y-%m-%d")
        return max(1, int(round((time.mktime(b) - time.mktime(a)) / 86400.0)) + 1)
    except (TypeError, ValueError):
        return 1


def _http_links(links) -> list[dict]:
    out = []
    for link in links or []:
        if isinstance(link, str):
            link = {"url": link}
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "")
        if url.startswith(("https://", "http://")) and len(url) < 400:
            out.append({"label": str(link.get("label") or url)[:80], "url": url})
        if len(out) >= 4:
            break
    return out


def _release_row(row: dict) -> dict | None:
    try:
        release = int(row.get("release"))
    except (TypeError, ValueError):
        return None
    date = day_of(str(row.get("date") or ""))
    if not _DATE.match(date):
        return None
    url = str(row.get("url") or M.WAYBACK_TILES.replace("{release}", str(release)))
    if not url.startswith("https://") or len(url) > 400:
        return None
    label = str(row.get("label") or row.get("title") or f"Esri Wayback {date}")[:80]
    return {"release": release, "date": date, "label": label, "url": url}


def _event_row(row: dict) -> dict | None:
    if not isinstance(row, dict):
        return None
    try:
        lat, lon = float(row.get("lat", row.get("latitude"))), float(row.get("lon", row.get("longitude")))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    stamp = parse_when(row.get("time"))
    if not stamp:
        return None
    feed = str(row.get("feed") or "")
    if feed not in FEEDS:
        feed = "fires" if "fire" in str(row.get("title") or "").lower() else "earthquakes"
    title = str(row.get("title") or feed)[:120]
    return {
        "id": str(row.get("id") or f"{feed}:{lat:.4f},{lon:.4f},{stamp}")[:80],
        "feed": feed, "lat": round(lat, 5), "lon": round(lon, 5), "time": stamp, "title": title,
        "description_md": str(row.get("description_md") or "")[:1500],
        "links": _http_links(row.get("links")),
        "color": "red" if feed in ("fires", "fire_perimeters") else "amber",
    }


def clean_state(raw) -> dict | None:
    """A scene-sized slider record. None when there is nothing to keep."""
    if not isinstance(raw, dict):
        return None
    mode = str(raw.get("mode") or "")
    if mode not in MODES:
        return None
    start = parse_when(raw.get("from"))
    end = parse_when(raw.get("to"))
    at = parse_when(raw.get("at") or raw.get("date"))
    if start and end and start > end:
        start, end = end, start
    feeds = [f for f in (raw.get("feeds") or FEEDS) if f in FEEDS]
    transition = str(raw.get("transition") or "swipe")
    if transition not in TRANSITIONS:
        transition = "swipe"
    releases = []
    for row in raw.get("releases") or []:
        item = _release_row(row) if isinstance(row, dict) else None
        if item:
            releases.append(item)
        if len(releases) >= MAX_RELEASES:
            break
    events = []
    for row in raw.get("events") or []:
        item = _event_row(row) if isinstance(row, dict) else None
        if item:
            events.append(item)
        if len(events) >= MAX_EVENTS:
            break
    events.sort(key=lambda e: e["time"])
    try:
        index = int(raw.get("index") or 0)
    except (TypeError, ValueError):
        index = 0
    return {
        "mode": mode,
        "from": day_of(start) or None,
        "to": day_of(end) or None,
        "at": day_of(at) or (day_of(start) if start else None),
        "playing": bool(raw.get("playing")),
        "transition": transition,
        "feeds": feeds or list(FEEDS),
        "index": max(0, index),
        "releases": releases,
        "events": events,
    }


def merge_state(prev, args: dict, result: dict | None = None) -> dict | None:
    """Fold a pane command into the stored slider. ``state`` reads change nothing."""
    action = str((args or {}).get("action") or "set")
    if action == "state":
        return clean_state(prev) if prev else None
    if action == "clear" or (args or {}).get("clear"):
        return None
    base = dict(prev or {})
    for key in (*_FIELDS, "releases", "events", "from", "to", "at", "date"):
        if key in (args or {}) and (args or {})[key] not in (None, "", []):
            base[key] = args[key]
    reported = (result or {}).get("timeline") if isinstance(result, dict) else None
    if isinstance(reported, dict):
        for key in ("mode", "from", "to", "at", "playing", "transition", "index"):
            if reported.get(key) not in (None, ""):
                base[key] = reported[key]
    if action == "stop":
        base["playing"] = False
    elif action == "play":
        base["playing"] = True
    if "date" in base and "at" not in args and not (isinstance(reported, dict) and reported.get("at")):
        base["at"] = base.get("date")
    return clean_state(base)


def public_timeline(raw) -> dict | None:
    state = clean_state(raw) if isinstance(raw, dict) else None
    if not state:
        return None
    return {key: state[key] for key in ("mode", "from", "to", "at", "playing", "transition", "feeds", "index")} | {
        "releases": len(state["releases"]), "events": len(state["events"])}


def _window(args: dict, clock, default_days: int | None) -> tuple[str, str]:
    today = time.strftime("%Y-%m-%d", time.gmtime(clock()))
    end = day_of(parse_when(args.get("to"))) or today
    start = day_of(parse_when(args.get("from")))
    if not start:
        if default_days:
            start = time.strftime("%Y-%m-%d", time.gmtime(clock() - 86400 * (default_days - 1)))
        else:
            start = "2014-01-01"
    if start > end:
        start, end = end, start
    return start, end


def _in_window(releases: list[dict], start: str, end: str) -> list[dict]:
    """Oldest first. Include the release on or before ``start`` so the window has a first frame."""
    dated = [r for r in releases if r.get("date")]
    chosen = [r for r in dated if start <= r["date"] <= end]
    anchor = M.pick_release(dated, start)
    if anchor and anchor not in chosen and anchor.get("date") and anchor["date"] <= end:
        chosen.append(anchor)
    chosen = sorted({r["release"]: r for r in chosen}.values(), key=lambda r: r["date"])
    if len(chosen) > MAX_RELEASES:
        chosen = chosen[-MAX_RELEASES:]
    return chosen


def wayback_frame(args: dict, clock=None) -> dict:
    clock = clock or time.time
    rels = M.wayback_releases()
    if not rels:
        return {"ok": False, "error": "wayback_unavailable",
                "message": "Esri World Imagery Wayback config is unreachable."}
    start, end = _window(args, clock, None)
    if not args.get("from"):
        start = rels[-1]["date"] or start
    rows = []
    for rel in _in_window(rels, start, end):
        layer = M.compare_layer(f"wayback:{rel['date']}")
        if layer.get("kind") != "xyz":
            continue
        rows.append({"release": layer["release"], "date": layer["date"], "label": layer["label"], "url": layer["url"]})
    if not rows:
        return {"ok": False, "error": "no_dates",
                "message": "No Esri Wayback release falls in that window.",
                "oldest": rels[-1]["date"], "newest": rels[0]["date"]}
    at = day_of(parse_when(args.get("date") or args.get("at"))) or rows[0]["date"]
    if at < rows[0]["date"]:
        at = rows[0]["date"]
    if at > rows[-1]["date"]:
        at = rows[-1]["date"]
    index = 0
    for i, row in enumerate(rows):
        if row["date"] <= at:
            index = i
    return {"ok": True, "mode": "wayback", "from": rows[0]["date"], "to": rows[-1]["date"],
            "at": rows[index]["date"], "index": index, "transition": args.get("transition") or "swipe",
            "releases": rows,
            "note": "Wayback dates are Esri release dates shown on the current view. "
                    "The imagery under a spot can be older than the release. Tiles are for viewing only."}


def list_dates(args: dict | None = None, clock=None) -> dict:
    frame = wayback_frame(args or {}, clock=clock)
    if not frame.get("ok"):
        return frame
    return {"ok": True, "count": len(frame["releases"]), "oldest": frame["releases"][0]["date"],
            "newest": frame["releases"][-1]["date"], "from": frame["from"], "to": frame["to"],
            "dates": [r["date"] for r in frame["releases"]],
            "note": frame["note"]}


def _alert_events(watches, session_id, area, start: str, end: str, feeds: list[str]) -> list[dict]:
    if watches is None:
        return []
    out = []
    for alert in watches.history(session_id):
        for item in alert.get("items") or []:
            if not isinstance(item, dict):
                continue
            feed = item.get("feed")
            if feed not in feeds:
                continue
            stamp = parse_when(item.get("time")) or parse_when(alert.get("time"))
            if not stamp or not (start <= day_of(stamp) <= end):
                continue
            if not watch.contains(area, item.get("lat"), item.get("lon")):
                continue
            out.append({
                "id": item.get("id"), "feed": feed, "lat": item.get("lat"), "lon": item.get("lon"),
                "time": stamp, "title": item.get("title") or feed,
                "description_md": item.get("description_md") or (
                    f"**Watch:** {alert.get('name') or 'area watch'}\n**Time:** {stamp}\n\nStored watch alert."),
                "links": item.get("links") or [],
            })
    return out


def _perimeter_events(item: dict, feed: str, area: dict, start: str, end: str) -> list[dict]:
    dates: list[tuple[str, str]] = []
    for kind, value in (("discovered", item.get("discovered")), ("updated", item.get("updated"))):
        stamp = parse_when(value)
        if stamp:
            dates.append((day_of(stamp), kind))
    if not dates:
        stamp = parse_when(item.get("time"))
        if stamp:
            dates.append((day_of(stamp), "updated"))
    out, used = [], set()
    for day, kind in dates:
        if day in used or not (start <= day <= end):
            continue
        used.add(day)
        if not watch.contains(area, item.get("lat"), item.get("lon")):
            continue
        out.append({**item, "feed": feed, "time": f"{day}T00:00:00Z",
                    "id": f"{item.get('id')}:{kind}",
                    "title": f"{item.get('title') or feed} ({kind})",
                    "description_md": item.get("description_md") or f"**Updated:** {day}\n\nSource: WFIGS",
                    "links": item.get("links") or []})
    return out


def _feed_events(area, start: str, end: str, feeds: list[str]) -> tuple[list[dict], list[dict]]:
    items, warnings = [], []
    cond = {"start": start, "end": end + "T23:59:59", "days": min(FIRMS_DAYS, _span_days(start, end)),
            "lookback_days": min(FIRMS_DAYS, _span_days(start, end))}
    for feed in feeds:
        try:
            res = watch.FETCHERS[feed](area, cond)
        except Exception as exc:
            warnings.append({"feed": feed, "status": "ERROR", "error": type(exc).__name__})
            continue
        status = res.get("status") or "OK"
        if status != "OK":
            warnings.append({"feed": feed, "status": status, **({"error": res["error"]} if res.get("error") else {}),
                             **({"key": res["key"]} if res.get("key") else {})})
            continue
        for item in res.get("items") or []:
            if feed == "fire_perimeters":
                items.extend(_perimeter_events(item, feed, area, start, end))
                continue
            stamp = parse_when(item.get("time"))
            if not stamp or not (start <= day_of(stamp) <= end):
                continue
            if not watch.contains(area, item.get("lat"), item.get("lon")):
                continue
            items.append({**item, "feed": feed, "time": stamp})
    return items, warnings


def event_frame(args: dict, area: dict, session_id=None, watches=None, clock=None) -> dict:
    clock = clock or time.time
    feeds = [f for f in (args.get("feeds") or FEEDS) if f in FEEDS] or list(FEEDS)
    start, end = _window(args, clock, 7)
    fetched, warnings = _feed_events(area, start, end, feeds)
    by_id = {}
    for item in _alert_events(watches, session_id, area, start, end, feeds) + fetched:
        row = _event_row(item)
        if row:
            by_id[row["id"]] = row
    events = sorted(by_id.values(), key=lambda e: e["time"])[:MAX_EVENTS]
    at = day_of(parse_when(args.get("date") or args.get("at"))) or start
    if at < start:
        at = start
    if at > end:
        at = end
    note = "Pins appear when the playhead reaches their time."
    if "fires" in feeds and _span_days(start, end) > FIRMS_DAYS:
        note += " NASA FIRMS covers the last 10 days; older fires in this window come from stored watch alerts."
    if any(w.get("status") == "KEY_MISSING" for w in warnings) and not events:
        missing = next(w for w in warnings if w.get("status") == "KEY_MISSING")
        return {"ok": False, "error": "KEY_MISSING", "key": missing.get("key"),
                "message": missing.get("error") or "A feed key is missing.", "warnings": warnings}
    return {"ok": True, "mode": "events", "from": start, "to": end, "at": at, "feeds": feeds,
            "events": events, "warnings": warnings, "note": note}


def visible_at(state: dict) -> dict:
    """What the slider is showing at ``state['at']``."""
    state = clean_state(state) or {}
    at = state.get("at") or ""
    if state.get("mode") == "wayback":
        rows = state.get("releases") or []
        current = None
        for row in rows:
            if row["date"] <= at:
                current = row
        current = current or (rows[0] if rows else None)
        nxt = None
        if current:
            for row in rows:
                if row["date"] > current["date"]:
                    nxt = row
                    break
        return {"date": (current or {}).get("date"), "release": (current or {}).get("release"),
                "label": (current or {}).get("label"),
                "next": {"date": nxt["date"], "release": nxt["release"]} if nxt else None}
    shown = [e for e in state.get("events") or [] if state.get("from", "") <= e["time"][:10] <= at]
    brief = [{"id": e["id"], "feed": e["feed"], "title": e["title"], "lat": e["lat"], "lon": e["lon"], "time": e["time"]}
             for e in shown[:12]]
    return {"count": len(shown), "events": brief}


def _area_from(args: dict, live, session_id) -> tuple[dict | None, dict | None]:
    raw = args.get("area")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        raw = {"bbox": list(raw)}
    elif isinstance(raw, dict) and all(name in raw for name in ("south", "west", "north", "east")) and "bbox" not in raw:
        raw = {"bbox": raw}
    if isinstance(raw, dict) and not raw.get("view"):
        try:
            return watch.normalize_area(raw), None
        except ValueError as exc:
            return None, {"ok": False, "error": "BAD_AREA", "message": str(exc)}
    if live is None:
        return None, {"ok": False, "error": "no_pane",
                      "message": "Pass area [south, west, north, east], or open the EOH pane."}
    try:
        return watch.view_area(live("get_current_view_state", {}, session_id)), None
    except ValueError as exc:
        return None, {"ok": False, "error": "no_pane", "message": str(exc)}


def _payload(res: dict) -> dict:
    if not isinstance(res, dict):
        return {"ok": False, "error": "bad_pane"}
    inner = res.get("result")
    if isinstance(inner, dict) and "ok" not in res:
        return inner
    return res


def _show(live, session_id, state: dict, action: str) -> dict:
    if live is None:
        return {"ok": False, "error": "no_pane"}
    body = {"action": action, **{k: state[k] for k in (
        "mode", "from", "to", "at", "playing", "transition", "feeds", "index", "releases", "events")
        if k in state}}
    if action == "step":
        body["by"] = state.get("by", 1)
    return _payload(live("hermes_timeline", body, session_id))


def _save(scenes, session_id, state: dict | None, replace: bool = True) -> None:
    if state is None and not replace:
        return
    if scenes is not None:
        try:
            scenes.note_timeline(session_id, state, replace=replace)
        except Exception:
            return
        return
    try:
        from .composer import scene_op
        scene_op("timeline", session_id, timeline=state, replace=replace, clear=state is None)
    except Exception:
        return


def _load(scenes, session_id):
    if scenes is not None:
        try:
            return scenes.scene(session_id).get("timeline")
        except Exception:
            return None
    try:
        from .composer import scene_op
        res = scene_op("timeline", session_id)
    except Exception:
        return None
    if isinstance(res, dict):
        return res.get("stored")
    return None


def run(args: dict | None = None, session_id=None, live=None, scenes=None, watches=None, clock=None) -> dict:
    """Drive the slider. ``live`` is ``(name, args, session_id) -> dict``. ``scenes`` persists it."""
    args = dict(args or {})
    clock = clock or time.time
    op = str(args.get("op") or "state")
    if op not in ("set", "play", "step", "stop", "state", "list_dates"):
        return {"ok": False, "error": "bad_args", "message": "op must be set, play, step, stop, state, or list_dates."}
    if op == "list_dates":
        return list_dates(args, clock=clock)

    if live is None:
        from .composer import live_call
        live = live_call
    if watches is None and op == "set" and str(args.get("mode") or "") == "events":
        try:
            watches = watch.store()
        except Exception:
            watches = None

    if op == "state":
        pane = _show(live, session_id, {}, "state") if live else {"ok": False, "error": "no_pane"}
        stored = _load(scenes, session_id)
        if pane.get("ok") and isinstance(pane.get("timeline"), dict):
            merged = merge_state(stored, {"action": "state"}, pane) or clean_state(pane["timeline"])
            if merged and scenes is not None:
                _save(scenes, session_id, {**(stored or {}), **{k: pane["timeline"][k] for k in (
                    "at", "playing", "index") if k in pane["timeline"]}}, replace=False)
            visible = pane.get("visible") if isinstance(pane.get("visible"), dict) else visible_at(merged or {})
            return {"ok": True, "op": "state", "timeline": public_timeline(merged), "visible": visible,
                    "active": bool(pane.get("active", True))}
        if stored:
            return {"ok": True, "op": "state", "timeline": public_timeline(stored), "visible": visible_at(stored),
                    "active": False, "pane": False}
        return pane if pane.get("error") else {"ok": True, "op": "state", "active": False, "timeline": None}

    if op == "set":
        mode = str(args.get("mode") or "wayback")
        if mode not in MODES:
            return {"ok": False, "error": "bad_args", "message": "mode must be wayback or events."}
        transition = str(args.get("transition") or "swipe")
        if transition not in TRANSITIONS:
            transition = "swipe"
        if mode == "wayback":
            frame = wayback_frame({**args, "transition": transition}, clock=clock)
        else:
            area, err = _area_from(args, live, session_id)
            if err:
                return err
            frame = event_frame({**args, "transition": transition}, area, session_id=session_id,
                                 watches=watches, clock=clock)
        if not frame.get("ok"):
            return frame
        state = clean_state({**frame, "playing": False, "transition": transition})
        _save(scenes, session_id, state, replace=True)
        pane = _show(live, session_id, state, "set") if live else {"ok": False, "error": "no_pane"}
        out = {"ok": True, "op": "set", "timeline": public_timeline(state), "visible": visible_at(state),
               "note": frame.get("note")}
        if frame.get("warnings"):
            out["warnings"] = frame["warnings"]
        if not pane.get("ok"):
            out["pane"] = False
            out["message"] = "The slider is saved. It appears when the EOH pane is open."
        else:
            out["pane"] = pane.get("pane")
            reported = pane.get("timeline") if isinstance(pane.get("timeline"), dict) else {}
            if reported:
                state = merge_state(state, {"action": "set"}, pane) or state
                _save(scenes, session_id, state, replace=False)
                out["timeline"] = public_timeline(state)
                if isinstance(pane.get("visible"), dict):
                    out["visible"] = pane["visible"]
        return out

    stored = _load(scenes, session_id)
    state = clean_state(stored)
    if not state:
        return {"ok": False, "error": "no_timeline",
                "message": "No time slider is saved. Call op=set first."}
    if op == "play":
        state["playing"] = True
        action = "play"
    elif op == "stop":
        state["playing"] = False
        action = "stop"
    else:
        by = args.get("by", 1)
        try:
            by = int(by)
        except (TypeError, ValueError):
            by = 1
        by = max(-24, min(24, by))
        state = _step(state, by)
        state["playing"] = False
        action = "step"
    _save(scenes, session_id, state, replace=False)
    pane_action = "set" if op == "step" else action
    pane = _show(live, session_id, state, pane_action) if live else {"ok": False, "error": "no_pane"}
    if pane.get("ok") and isinstance(pane.get("timeline"), dict):
        state = merge_state(state, {"action": action}, pane) or state
        _save(scenes, session_id, state, replace=False)
    out = {"ok": True, "op": op, "timeline": public_timeline(state), "visible": pane.get("visible") if isinstance(pane.get("visible"), dict) else visible_at(state)}
    if not pane.get("ok"):
        out["pane"] = False
    return out


def _step(state: dict, by: int) -> dict:
    state = dict(state)
    if state["mode"] == "wayback":
        rows = state.get("releases") or []
        if not rows:
            return state
        index = max(0, min(len(rows) - 1, int(state.get("index") or 0) + by))
        state["index"] = index
        state["at"] = rows[index]["date"]
        return state
    at = state.get("at") or state.get("from") or ""
    times: list[str] = []
    for event in state.get("events") or []:
        day = event["time"][:10]
        if day not in times:
            times.append(day)
    if not times or by == 0:
        return state
    if by > 0:
        later = [day for day in times if day > at]
        state["at"] = later[min(by, len(later)) - 1] if later else (state.get("to") or at)
    else:
        earlier = [day for day in times if day < at]
        slot = len(earlier) + by
        state["at"] = earlier[slot] if earlier and slot >= 0 else (state.get("from") or at)
    return state
