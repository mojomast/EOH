"""One bbox shape for every tool that takes a box.

Feeds document ``[west, south, east, north]``. The time slider and watches
document ``[south, west, north, east]``. A four-number list that only makes
sense in the other order is corrected. A list that makes sense both ways keeps
the order that tool documents and carries a warning. Named keys are unambiguous.

``area: {place, radius_km}`` is geocoded with the existing geocoder.
"""
from __future__ import annotations

from .watch import haversine_km

_NAMES = ("south", "west", "north", "east")


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _swne_ok(south: float, west: float, north: float, east: float) -> bool:
    return -90 <= south < north <= 90 and -180 <= west < east <= 180


def _as_swne(values: list[float], order: str) -> list[float] | None:
    if order == "swne" and _swne_ok(*values):
        return list(values)
    if order == "wsen":
        west, south, east, north = values
        if _swne_ok(south, west, north, east):
            return [south, west, north, east]
    return None


def interpret_bbox(value, prefer: str = "swne") -> dict:
    """Return ``{bbox: [south, west, north, east]}``.

    ``warning`` is set when the list was not already in ``prefer`` order, or
    when both orders are plausible. Raises ``ValueError`` when neither order
    is a real box.
    """
    prefer = "wsen" if prefer == "wsen" else "swne"
    if isinstance(value, dict):
        if not all(name in value for name in _NAMES):
            raise ValueError("A bbox object needs south, west, north, and east.")
        nums = [_num(value[name]) for name in _NAMES]
        if any(item is None for item in nums) or not _swne_ok(*nums):
            raise ValueError("bbox {south, west, north, east} is outside range or is empty.")
        return {"bbox": nums, "order": "named"}
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox must be {south, west, north, east} or a list of four numbers.")
    nums = [_num(item) for item in value]
    if any(item is None for item in nums):
        raise ValueError("bbox values must be numbers.")
    swne = _as_swne(nums, "swne")
    wsen = _as_swne(nums, "wsen")
    if swne and wsen and swne != wsen:
        chosen = swne if prefer == "swne" else wsen
        return {
            "bbox": chosen,
            "order": prefer,
            "warning": "This box is valid as both [south, west, north, east] and "
                       "[west, south, east, north]. Pass {south, west, north, east}.",
        }
    if swne and not wsen:
        out = {"bbox": swne, "order": "swne"}
        if prefer == "wsen":
            out["warning"] = "Read as [south, west, north, east] and converted for this tool."
        return out
    if wsen and not swne:
        out = {"bbox": wsen, "order": "wsen"}
        if prefer == "swne":
            out["warning"] = "Read as [west, south, east, north] and converted to [south, west, north, east]."
        return out
    raise ValueError(
        "bbox is implausible. Latitudes must be -90..90 with south < north, longitudes "
        "-180..180 with west < east. Pass {south, west, north, east}."
    )


def to_order(bbox: list[float], order: str) -> list[float]:
    south, west, north, east = bbox
    if order == "wsen":
        return [west, south, east, north]
    return [south, west, north, east]


def circle_from(lat: float, lon: float, radius_km: float) -> dict:
    from .watch import normalize_area
    return normalize_area({"lat": lat, "lon": lon, "radius_km": radius_km})


def area_from_place(place: str, radius_km: float, geocode=None) -> dict:
    if geocode is None:
        from .poi import geocode as geocode
    found = geocode(place) if geocode else None
    if not found or not isinstance(found.get("lat"), (int, float)) or not isinstance(found.get("lon"), (int, float)):
        raise ValueError(f"Could not geocode {place!r}.")
    area = circle_from(float(found["lat"]), float(found["lon"]), float(radius_km))
    area["place"] = str(found.get("name") or place)[:160]
    return area


def rewrite_feed_args(args: dict, prefer: str) -> tuple[dict, str | None]:
    """Copy ``args`` with bbox fields normalized. The warning is for the caller."""
    if not isinstance(args, dict):
        return args, None
    out = dict(args)
    warning = None
    if "bbox" in out and out["bbox"] is not None:
        parsed = interpret_bbox(out["bbox"], prefer=prefer)
        out["bbox"] = to_order(parsed["bbox"], prefer)
        warning = parsed.get("warning")
    area = out.get("area")
    if isinstance(area, dict) and area.get("place") and area.get("radius_km") is not None and area.get("bbox") is None and area.get("polygon") is None:
        radius = area.get("radius_km")
        if not isinstance(radius, (int, float)) or isinstance(radius, bool):
            raise ValueError("area {place, radius_km} needs radius_km.")
        parsed_area = area_from_place(str(area["place"]), float(radius), None)
        out["area"] = {"bbox": to_order(parsed_area["bbox"], prefer)}
        return out, warning
    if isinstance(area, dict):
        area = dict(area)
        if "bbox" in area and area["bbox"] is not None:
            parsed = interpret_bbox(area["bbox"], prefer=prefer)
            area["bbox"] = to_order(parsed["bbox"], prefer)
            warning = parsed.get("warning") or warning
        elif all(name in area for name in _NAMES):
            parsed = interpret_bbox(area, prefer=prefer)
            area = {"bbox": to_order(parsed["bbox"], prefer)}
            warning = parsed.get("warning")
        out["area"] = area
    elif isinstance(area, (list, tuple)) and len(area) == 4:
        parsed = interpret_bbox(area, prefer=prefer)
        out["area"] = {"bbox": to_order(parsed["bbox"], prefer)}
        warning = parsed.get("warning") or warning
    return out, warning


def centers_far(a: list[float], b: list[float]) -> bool:
    """True when two swne boxes are not the same place. Used by tests."""
    a_lat = (a[0] + a[2]) / 2
    a_lon = (a[1] + a[3]) / 2
    b_lat = (b[0] + b[2]) / 2
    b_lon = (b[1] + b[3]) / 2
    return haversine_km(a_lat, a_lon, b_lat, b_lon) > 1
