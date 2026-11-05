"""Thin and split dense routes before they reach the pane.

A road trace from OSRM can be a point every few metres. Sending that list in
one command is what froze the globe. Douglas-Peucker drops points that sit on
an almost straight line. A distance step resamples a trace that is denser than
the step, and never adds points to a trace that is already sparse. Chunks
overlap by one point so a split dolly does not jump.
"""
from __future__ import annotations

import math

from .measure import R_EARTH, bearing_deg, haversine_m, path_length_m

CHUNK_POINTS = 300
FLY_STEP_M = 40.0
FLY_EPSILON_M = 12.0
WALK_STEP_M = 75.0
WALK_STEP_MIN_M = 20.0
WALK_STEP_MAX_M = 250.0
MAX_WALK_SAMPLES = 800


def as_latlon(point) -> tuple[float, float]:
    """(lat, lon) from a pair, a {lat, lon}, or a {latitude, longitude}."""
    if isinstance(point, dict):
        lat = point.get("lat", point.get("latitude"))
        lon = point.get("lon", point.get("lng", point.get("longitude")))
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        lat, lon = point[0], point[1]
    else:
        raise ValueError("point")
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("point out of range")
    return lat, lon


def as_points(raw) -> list[tuple[float, float]]:
    """Coordinates from a list of points, or from stops that carry a point."""
    out: list[tuple[float, float]] = []
    for item in raw or []:
        if isinstance(item, dict) and item.get("points"):
            out.extend(as_points(item.get("points")))
            continue
        try:
            out.append(as_latlon(item))
        except (TypeError, ValueError):
            continue
    return out


def _xy(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    return (
        math.radians(lon) * math.cos(math.radians(lat0)) * R_EARTH,
        math.radians(lat) * R_EARTH,
    )


def _segment_distance_m(point, start, end) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    span = math.hypot(dx, dy)
    if span == 0:
        return math.hypot(px - ax, py - ay)
    return abs(dy * px - dx * py + bx * ay - by * ax) / span


def douglas_peucker(points, epsilon_m: float = FLY_EPSILON_M) -> list[tuple[float, float]]:
    """Keep endpoints and vertices that leave the chord by more than epsilon_m."""
    pts = as_points(points) if points and not isinstance(points[0], tuple) else [as_latlon(p) for p in points]
    if len(pts) < 3:
        return list(pts)
    epsilon = max(0.5, float(epsilon_m))
    lat0 = pts[0][0]
    xy = [_xy(lat, lon, lat0) for lat, lon in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True

    def mark(i: int, j: int) -> None:
        if j <= i + 1:
            return
        best = -1
        best_d = -1.0
        for k in range(i + 1, j):
            dist = _segment_distance_m(xy[k], xy[i], xy[j])
            if dist > best_d:
                best_d = dist
                best = k
        if best >= 0 and best_d > epsilon:
            keep[best] = True
            mark(i, best)
            mark(best, j)

    mark(0, len(pts) - 1)
    return [pts[i] for i, flag in enumerate(keep) if flag]


def _interpolate(a, b, fraction: float) -> tuple[float, float]:
    lat = a[0] + (b[0] - a[0]) * fraction
    lon = a[1] + (b[1] - a[1]) * fraction
    return round(lat, 6), round((lon + 540) % 360 - 180, 6)


def sample_polyline(points, step_m: float, *, densify: bool = True) -> list[tuple[float, float]]:
    """Points about every step_m along the path, including both ends.

    ``densify=False`` only inserts a point when the previous kept point is at
    least step_m behind, so a sparse route stays sparse.
    """
    pts = as_points(points) if points and not (isinstance(points[0], tuple) and len(points[0]) == 2) else [as_latlon(p) for p in (points or [])]
    if len(pts) < 2:
        return list(pts)
    step = max(1.0, float(step_m))
    out = [pts[0]]
    since = 0.0
    for index in range(len(pts) - 1):
        start, end = pts[index], pts[index + 1]
        span = haversine_m(start, end)
        if span <= 0:
            continue
        if not densify:
            since += span
            if since >= step or index == len(pts) - 2:
                out.append(end)
                since = 0.0
            continue
        walked = 0.0
        while since + (span - walked) >= step - 1e-6:
            need = step - since
            if need > span - walked + 1e-6:
                break
            walked += need
            if walked < span - 0.05:
                out.append(_interpolate(start, end, walked / span))
            since = 0.0
        since += max(0.0, span - walked)
    if haversine_m(out[-1], pts[-1]) > 1:
        out.append(pts[-1])
    return out


def mean_spacing_m(points) -> float:
    pts = points if points and isinstance(points[0], tuple) else as_points(points)
    if len(pts) < 2:
        return 0.0
    return path_length_m(pts) / (len(pts) - 1)


def simplify(points, step_m: float = FLY_STEP_M, epsilon_m: float = FLY_EPSILON_M) -> list[tuple[float, float]]:
    """Resample a dense trace, then drop colinear points. Sparse traces are not densified."""
    pts = as_points(points)
    if len(pts) < 3:
        return pts
    step = max(1.0, float(step_m))
    if mean_spacing_m(pts) < step * 0.75:
        pts = sample_polyline(pts, step, densify=True)
    thinned = douglas_peucker(pts, epsilon_m)
    return thinned if len(thinned) >= 2 else pts[:1] + pts[-1:]


def chunks(points, size: int = CHUNK_POINTS) -> list[list]:
    """Slices of at most ``size`` points. Neighbours share their endpoint."""
    pts = list(points or [])
    cap = max(2, int(size))
    if len(pts) <= cap:
        return [pts] if pts else []
    out = []
    index = 0
    while index < len(pts):
        end = min(len(pts), index + cap)
        out.append(pts[index:end])
        if end >= len(pts) or end <= index + 1:
            break
        index = end - 1
    return out


def _heading(points, index: int) -> float:
    if index + 1 < len(points):
        return bearing_deg(points[index], points[index + 1])
    if index > 0:
        return bearing_deg(points[index - 1], points[index])
    return 0.0


def fit_walk_step(points, step_m: float, cap: int = MAX_WALK_SAMPLES) -> float:
    """Widen the step until the walk stays within the sample cap."""
    pts = as_points(points)
    step = min(WALK_STEP_MAX_M, max(WALK_STEP_MIN_M, float(step_m or WALK_STEP_M)))
    if len(pts) < 2:
        return step
    length = path_length_m(pts)
    limit = max(2, int(cap))
    while length / step + 2 > limit and step < WALK_STEP_MAX_M:
        step = min(WALK_STEP_MAX_M, step * 1.25)
    return step


def walk_samples(points, step_m: float = WALK_STEP_M, cap: int = MAX_WALK_SAMPLES) -> list[dict]:
    """Sample a route for a street walk. Each row has lat, lon, heading, and alongM."""
    pts = as_points(points)
    if not pts:
        return []
    step = fit_walk_step(pts, step_m, cap=cap)
    sampled = sample_polyline(pts, step, densify=True) if len(pts) >= 2 else pts
    if len(sampled) > cap:
        sampled = sampled[: cap - 1] + [sampled[-1]]
    along = 0.0
    rows = []
    for index, point in enumerate(sampled):
        if index:
            along += haversine_m(sampled[index - 1], point)
        rows.append({
            "lat": point[0], "lon": point[1],
            "heading": round(_heading(sampled, index), 1),
            "alongM": round(along, 1),
        })
    return rows
