"""Export a saved investigation, or the current scene, as GeoJSON and KML.

GeoJSON follows RFC 7946: positions are ``[longitude, latitude]``, polygon exteriors
are counterclockwise, and there is no ``crs`` member. KML 2.2 uses the same axis order
(``lon,lat,alt``). Property values are the investigation's own pins, drawers and
enrichment. Google photoreal tile imagery is not embedded: those terms allow viewing
only, so tile URLs and inline image bytes are dropped and globe screenshots of a
photoreal stack are left out of the pack.
"""
from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from . import investigations

FORMATS = ("geojson", "kml", "both", "pack")
NS = "http://www.opengis.net/kml/2.2"
_PHOTO_URL = re.compile(r"https?://[^\s)<>\"]+", re.I)
_SENSITIVE_NAME = re.compile(r"(api[_-]?key|secret|password|token|authorization|cookie)", re.I)
_BAD_XML = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_ENRICH = ("cuisine", "amenity", "shop", "tourism", "leisure", "category", "opening_hours",
           "website", "phone", "menu", "address", "wikipedia", "wikidata", "osm",
           "rating", "stars", "source", "kind", "color", "intent")
_LINE_TYPES = {"route", "line", "linestring", "polyline"}
_POLY_TYPES = {"polygon", "area", "box", "rectangle"}


def is_photoreal_imagery(url: str) -> bool:
    """True for Google photoreal / map-tile imagery URLs (viewing only; do not store)."""
    try:
        parts = urllib.parse.urlsplit(str(url).strip())
    except Exception:
        return False
    host = (parts.hostname or "").lower()
    path = (parts.path or "").lower()
    query = (parts.query or "").lower()
    blob = host + path + "?" + query
    if not host:
        return False
    if host == "tile.googleapis.com" or host.endswith(".tile.googleapis.com"):
        return True
    if host.endswith(".google.com") and re.match(r"^(khms?|mt)\d\.", host):
        return True
    if host.endswith("earth.google.com"):
        return True
    if "googleapis.com" in host and any(x in blob for x in ("3dtiles", "photoreal", "/vt", "maptiles", "staticmap")):
        return True
    if "cesium.com" in host and "photoreal" in blob:
        return True
    if "photoreal" in blob and any(x in blob for x in ("tile", "google", "3dtiles")):
        return True
    return False


def _finite(n) -> float | None:
    try:
        x = float(n)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def _pair(lat, lon) -> tuple[float, float] | None:
    la, lo = _finite(lat), _finite(lon)
    if la is None or lo is None or not (-90 <= la <= 90 and -180 <= lo <= 180):
        return None
    return round(la, 6), round(lo, 6)


def _points(raw) -> list[tuple[float, float]]:
    """Project points to (lat, lon). Lists are [lat, lon] unless the first value cannot be a latitude."""
    out = []
    if not isinstance(raw, list):
        return out
    for p in raw:
        if isinstance(p, dict):
            pair = _pair(p.get("lat", p.get("latitude")), p.get("lon", p.get("lng", p.get("longitude"))))
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            a, b = _finite(p[0]), _finite(p[1])
            if a is None or b is None:
                pair = None
            elif abs(a) > 90 and abs(b) <= 90:
                pair = _pair(b, a)
            else:
                pair = _pair(a, b)
        else:
            pair = None
        if pair:
            out.append(pair)
    return out


def _pos(lat: float, lon: float) -> list[float]:
    return [lon, lat]


def _close(ring: list[list[float]]) -> list[list[float]]:
    if not ring:
        return ring
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    return ring


def _signed_area(ring: list[list[float]]) -> float:
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    total = 0.0
    for i, (x, y) in enumerate(pts):
        x2, y2 = pts[(i + 1) % len(pts)]
        total += x * y2 - x2 * y
    return total / 2


def _ccw(ring: list[list[float]]) -> list[list[float]]:
    ring = _close(ring)
    if len(ring) >= 4 and _signed_area(ring) < 0:
        body = list(reversed(ring[:-1]))
        ring = body + [body[0]]
    return ring


def _scrub_text(text: str, omitted: dict) -> str:
    def repl(m):
        if is_photoreal_imagery(m.group(0)):
            omitted["photoreal"] += 1
            return "[omitted photoreal imagery]"
        return m.group(0)
    return _PHOTO_URL.sub(repl, str(text))[:4000]


def _clean_url(url, omitted: dict, *, image: bool = False) -> str | None:
    if not isinstance(url, str):
        return None
    u = url.strip()
    if u.startswith("data:"):
        omitted["inline_images"] += 1
        return None
    if not u.startswith(("http://", "https://")) or len(u) > 500:
        return None
    if is_photoreal_imagery(u):
        omitted["photoreal"] += 1
        return None
    return u


def _links(raw, omitted: dict) -> list[dict]:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        url = _clean_url(item.get("url"), omitted)
        if not url:
            continue
        row = {"url": url}
        if item.get("label"):
            row["label"] = _scrub_text(item["label"], omitted)[:200]
        out.append(row)
        if len(out) >= 12:
            break
    return out


def _images(raw, omitted: dict) -> list[dict]:
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        url = _clean_url(item.get("url"), omitted, image=True)
        if not url:
            continue
        row = {"url": url}
        for k in ("alt", "caption"):
            if item.get(k):
                row[k] = _scrub_text(item[k], omitted)[:300]
        out.append(row)
        if len(out) >= 8:
            break
    return out


def _enrichment(spec: dict, omitted: dict) -> dict:
    out = {}
    tags = spec.get("tags") if isinstance(spec.get("tags"), dict) else {}
    merged = {**{k: spec.get(k) for k in _ENRICH}, **tags}
    for k, v in merged.items():
        if v is None or _SENSITIVE_NAME.search(str(k)):
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
            out[str(k)[:80]] = v
        elif isinstance(v, str) and v.strip():
            raw = v.strip()
            if raw.startswith(("http://", "https://")) and is_photoreal_imagery(raw):
                omitted["photoreal"] += 1
                continue
            text = _scrub_text(raw, omitted)
            if text:
                out[str(k)[:80]] = text[:500]
    return out


def _drawer(spec: dict, omitted: dict, *, open_: bool) -> dict | None:
    title = spec.get("title") or spec.get("label")
    body = spec.get("description_md") or spec.get("description") or ""
    links = _links(spec.get("links"), omitted)
    images = _images(spec.get("images"), omitted)
    drawer = {}
    if title:
        drawer["title"] = _scrub_text(title, omitted)[:200]
    if body:
        drawer["description_md"] = _scrub_text(body, omitted)
    if links:
        drawer["links"] = links
    if images:
        drawer["images"] = images
    if open_:
        drawer["open"] = True
    return drawer or None


def _properties(name: str, role: str, lat: float, lon: float, spec: dict, omitted: dict, *, open_: bool) -> dict:
    props = {"name": _scrub_text(name, omitted)[:200] or role, "role": role,
             "latitude": lat, "longitude": lon}
    if spec.get("color") and not _SENSITIVE_NAME.search("color"):
        props["color"] = str(spec["color"])[:40]
    drawer = _drawer(spec, omitted, open_=open_)
    if drawer:
        props["drawer"] = drawer
        if drawer.get("description_md"):
            props["description"] = drawer["description_md"]
    enrich = _enrichment(spec, omitted)
    if enrich:
        props["enrichment"] = enrich
    rows = spec.get("rows")
    if isinstance(rows, list) and rows:
        clean = []
        for row in rows[:12]:
            if isinstance(row, dict):
                clean.append({k: _scrub_text(row[k], omitted)[:200] for k in ("text", "price") if row.get(k)})
        if clean:
            props["rows"] = clean
    return {k: v for k, v in props.items() if not _SENSITIVE_NAME.search(str(k))}


def _feature(fid: str, role: str, geom: dict, spec: dict, omitted: dict, *, open_: bool = False) -> dict | None:
    coords = geom.get("coordinates")
    if geom["type"] == "Point":
        lon, lat = coords
    elif geom["type"] == "LineString":
        lon, lat = coords[0]
    else:
        lon, lat = coords[0][0]
    name = spec.get("label") or spec.get("title") or spec.get("name") or fid
    return {"type": "Feature", "id": fid, "properties": _properties(str(name), role, lat, lon, spec, omitted, open_=open_),
            "geometry": geom}


def _line(fid, role, pts, spec, omitted, open_=False) -> dict | None:
    if len(pts) < 2:
        return None
    return _feature(fid, role, {"type": "LineString", "coordinates": [_pos(a, b) for a, b in pts]}, spec, omitted, open_=open_)


def _polygon(fid, role, pts, spec, omitted, open_=False) -> dict | None:
    uniq = []
    for p in pts:
        if not uniq or p != uniq[-1]:
            uniq.append(p)
    if len(uniq) >= 2 and uniq[0] == uniq[-1]:
        uniq = uniq[:-1]
    if len(uniq) < 3:
        return None
    ring = _ccw([_pos(a, b) for a, b in uniq])
    return _feature(fid, role, {"type": "Polygon", "coordinates": [ring]}, spec, omitted, open_=open_)


def _point_feature(fid, role, lat, lon, spec, omitted, open_=False) -> dict | None:
    return _feature(fid, role, {"type": "Point", "coordinates": _pos(lat, lon)}, spec, omitted, open_=open_)


def _drawer_hits(spec: dict, drawer: dict | None) -> bool:
    if not isinstance(drawer, dict):
        return False
    pair = _pair(spec.get("latitude", spec.get("lat")), spec.get("longitude", spec.get("lon")))
    mark = _pair(drawer.get("latitude", drawer.get("lat")), drawer.get("longitude", drawer.get("lon")))
    if not pair or not mark or pair != mark:
        return False
    lab = drawer.get("label")
    if lab and spec.get("label") and str(lab) != str(spec.get("label")):
        return False
    return True


def _from_annotation(ann: dict, drawer, omitted: dict, seen: set) -> dict | None:
    if not isinstance(ann, dict):
        return None
    spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else ann
    kind = str(spec.get("type") or "pin").lower()
    raw_id = str(ann.get("id") or spec.get("id") or f"f{len(seen) + 1}")
    pts = _points(spec.get("points") or spec.get("polygon") or spec.get("line"))
    pair = _pair(spec.get("latitude", spec.get("lat")), spec.get("longitude", spec.get("lon")))
    open_ = _drawer_hits(spec, drawer)
    feat = None
    if kind in _POLY_TYPES and len(pts) >= 3:
        feat = _polygon(_fid("area", raw_id, seen), "area", pts, spec, omitted, open_)
    elif kind in _LINE_TYPES and len(pts) >= 2:
        role = "route" if kind == "route" else "drawing"
        feat = _line(_fid(role, raw_id, seen), role, pts, spec, omitted, open_)
    elif kind == "card" and pair:
        feat = _point_feature(_fid("card", raw_id, seen), "card", pair[0], pair[1], spec, omitted, open_)
    elif pair:
        role = "area" if kind in _POLY_TYPES else "pin"
        feat = _point_feature(_fid(role, raw_id, seen), role, pair[0], pair[1], spec, omitted, open_)
    elif len(pts) >= 3:
        feat = _polygon(_fid("area", raw_id, seen), "area", pts, spec, omitted, open_)
    elif len(pts) >= 2:
        feat = _line(_fid("drawing", raw_id, seen), "drawing", pts, spec, omitted, open_)
    if feat is None and (pts or pair):
        omitted["skipped"] += 1
    return feat


def _from_shape(sh: dict, omitted: dict, seen: set) -> dict | None:
    if not isinstance(sh, dict):
        return None
    kind = str(sh.get("kind") or sh.get("type") or "line").lower()
    pts = _points(sh.get("points"))
    raw_id = str(sh.get("id") or f"shape{len(seen) + 1}")
    spec = {"label": sh.get("label"), "color": sh.get("color"), "kind": kind}
    if kind in ("polygon", "box", "area", "rectangle"):
        return _polygon(_fid("area", raw_id, seen), "area", pts, spec, omitted)
    return _line(_fid("drawing", raw_id, seen), "drawing", pts, spec, omitted)


def _fid(role: str, raw: str, seen: set) -> str:
    base = f"{role}:{raw}"
    fid, n = base, 2
    while fid in seen:
        fid = f"{base}-{n}"
        n += 1
    seen.add(fid)
    return fid


def _shape_list(raw) -> list:
    if isinstance(raw, dict):
        return [v for v in raw.values() if isinstance(v, dict)]
    if isinstance(raw, list):
        return [v for v in raw if isinstance(v, dict)]
    return []


def _bbox(features: list[dict]) -> list[float] | None:
    lons, lats = [], []

    def walk(coords, depth):
        if depth == 0:
            lons.append(coords[0]); lats.append(coords[1])
            return
        for c in coords:
            walk(c, depth - 1)

    depth = {"Point": 0, "LineString": 1, "Polygon": 2}
    for f in features:
        g = f.get("geometry") or {}
        if g.get("type") in depth:
            walk(g["coordinates"], depth[g["type"]])
    if not lons:
        return None
    return [round(min(lons), 6), round(min(lats), 6), round(max(lons), 6), round(max(lats), 6)]


def build_feature_collection(data: dict) -> tuple[dict, dict]:
    """Turn an investigation snapshot into a FeatureCollection and a small count dict."""
    omitted = {"photoreal": 0, "inline_images": 0, "skipped": 0}
    seen: set[str] = set()
    features = []
    drawer = data.get("drawer")
    for ann in data.get("annotations") or []:
        feat = _from_annotation(ann, drawer, omitted, seen)
        if feat:
            features.append(feat)
    for sh in _shape_list(data.get("shapes")):
        feat = _from_shape(sh, omitted, seen)
        if feat:
            features.append(feat)
        elif sh.get("points"):
            omitted["skipped"] += 1
    name = str(data.get("name") or "Investigation")[:120]
    fc: dict = {"type": "FeatureCollection", "name": name, "features": features}
    notes = data.get("notes")
    if isinstance(notes, str) and notes.strip():
        fc["notes"] = _scrub_text(notes, omitted)
    box = _bbox(features)
    if box:
        fc["bbox"] = box
    counts = {"points": 0, "lines": 0, "polygons": 0, "skipped": omitted["skipped"],
              "photoreal_omitted": omitted["photoreal"], "inline_images_omitted": omitted["inline_images"]}
    for f in features:
        t = f["geometry"]["type"]
        counts["points" if t == "Point" else "lines" if t == "LineString" else "polygons"] += 1
    return fc, counts


def _positions(coords, depth, into: list):
    if depth == 0:
        into.append(coords)
        return
    for c in coords:
        _positions(c, depth - 1, into)


def validate_geojson(obj: dict) -> list[str]:
    """RFC 7946 checks for the types this exporter writes, plus lon/lat order.

    When a feature has numeric ``latitude`` and ``longitude`` properties, the first
    position must be ``[longitude, latitude]``. A Montreal point stored as
    ``[45.5, -73.5]`` is in range either way; the properties are what make the axis
    order checkable.
    """
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["GeoJSON root must be an object"]
    if "crs" in obj:
        errs.append("crs is not allowed (RFC 7946)")
    if obj.get("type") != "FeatureCollection":
        errs.append("type must be FeatureCollection")
        return errs
    feats = obj.get("features")
    if not isinstance(feats, list):
        errs.append("features must be an array")
        return errs
    all_pos: list[list] = []
    for i, f in enumerate(feats):
        if not isinstance(f, dict) or f.get("type") != "Feature":
            errs.append(f"features[{i}] must be a Feature")
            continue
        if "crs" in f:
            errs.append(f"features[{i}] must not contain crs")
        props = f.get("properties")
        if props is None:
            props = {}
        if not isinstance(props, dict):
            errs.append(f"features[{i}].properties must be an object")
            props = {}
        geom = f.get("geometry")
        if not isinstance(geom, dict):
            errs.append(f"features[{i}].geometry must be an object")
            continue
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if gtype == "Point":
            _check_pos(coords, f"features[{i}].geometry", errs)
            poss = [coords] if isinstance(coords, list) else []
        elif gtype == "LineString":
            if not isinstance(coords, list) or len(coords) < 2:
                errs.append(f"features[{i}] LineString needs 2+ positions")
                poss = []
            else:
                poss = coords
                for j, p in enumerate(coords):
                    _check_pos(p, f"features[{i}].geometry.coordinates[{j}]", errs)
        elif gtype == "Polygon":
            poss = []
            if not isinstance(coords, list) or not coords:
                errs.append(f"features[{i}] Polygon needs one or more rings")
            else:
                for r, ring in enumerate(coords):
                    where = f"features[{i}].geometry.coordinates[{r}]"
                    if not isinstance(ring, list) or len(ring) < 4:
                        errs.append(f"{where} ring needs 4+ positions")
                        continue
                    for j, p in enumerate(ring):
                        _check_pos(p, f"{where}[{j}]", errs)
                    if ring[0] != ring[-1]:
                        errs.append(f"{where} ring must be closed")
                    area = _signed_area(ring) if ring and isinstance(ring[0], list) else 0
                    if r == 0 and area < 0:
                        errs.append(f"{where} exterior must be counterclockwise (RFC 7946)")
                    if r > 0 and area > 0:
                        errs.append(f"{where} hole must be clockwise (RFC 7946)")
                    poss.extend(ring)
        else:
            errs.append(f"features[{i}] geometry type {gtype!r} is not exported")
            poss = []
        all_pos.extend(p for p in poss if isinstance(p, list) and len(p) >= 2)
        _check_axis(props, poss, f"features[{i}]", errs)
    bbox = obj.get("bbox")
    if bbox is not None:
        if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(x, (int, float)) for x in bbox)):
            errs.append("bbox must be [west, south, east, north]")
        elif bbox[0] > bbox[2] or bbox[1] > bbox[3] or not (-180 <= bbox[0] <= 180 and -180 <= bbox[2] <= 180
                                                            and -90 <= bbox[1] <= 90 and -90 <= bbox[3] <= 90):
            errs.append("bbox is outside lon/lat range or is not [west, south, east, north]")
        else:
            for p in all_pos:
                if not (bbox[0] - 1e-6 <= p[0] <= bbox[2] + 1e-6 and bbox[1] - 1e-6 <= p[1] <= bbox[3] + 1e-6):
                    errs.append("bbox does not contain every position")
                    break
    return errs


def _check_pos(p, where: str, errs: list[str]) -> None:
    if not isinstance(p, list) or len(p) not in (2, 3) or not all(isinstance(x, (int, float)) and math.isfinite(float(x)) for x in p):
        errs.append(f"{where} position must be [longitude, latitude]")
        return
    lon, lat = float(p[0]), float(p[1])
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        errs.append(f"{where} is outside longitude [-180,180] / latitude [-90,90] (got {lon}, {lat})")


def _check_axis(props: dict, poss: list, where: str, errs: list[str]) -> None:
    lat, lon = props.get("latitude"), props.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) or not poss:
        return
    p = poss[0]
    if not isinstance(p, list) or len(p) < 2:
        return
    if abs(float(p[0]) - float(lon)) > 1e-5 or abs(float(p[1]) - float(lat)) > 1e-5:
        errs.append(f"{where} coordinates are not [longitude, latitude] "
                    f"(properties longitude={lon} latitude={lat}, position={p[0]},{p[1]})")


def to_kml(fc: dict) -> str:
    name = escape(_xml(fc.get("name") or "Investigation"))
    marks = []
    for f in fc.get("features") or []:
        marks.append(_placemark(f))
    body = "\n".join(marks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="{NS}">
  <Document>
    <name>{name}</name>
{body}
  </Document>
</kml>
"""


def _xml(s: str) -> str:
    return _BAD_XML.sub("", str(s))


def _cdata(s: str) -> str:
    return _xml(s).replace("]]>", "]]]]><![CDATA[>")


def _data(name: str, value) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(value)
    else:
        text = str(value)
    return f"        <Data name={quoteattr(_xml(name)[:80])}><value>{escape(_xml(text)[:500])}</value></Data>"


def _placemark(feat: dict) -> str:
    props = feat.get("properties") or {}
    geom = feat.get("geometry") or {}
    pid = _xml(str(feat.get("id") or "f").replace(":", "-"))[:80]
    title = escape(_xml(props.get("name") or feat.get("id") or "Feature"))
    bits = [f"    <Placemark id={quoteattr(pid)}>", f"      <name>{title}</name>"]
    desc = props.get("description") or (props.get("drawer") or {}).get("description_md")
    if desc:
        bits.append(f"      <description><![CDATA[{_cdata(desc)}]]></description>")
    data = [_data("latitude", props.get("latitude")), _data("longitude", props.get("longitude")),
            _data("role", props.get("role") or "")]
    if props.get("color"):
        data.append(_data("color", props["color"]))
    drawer = props.get("drawer") or {}
    if drawer.get("open"):
        data.append(_data("drawer_open", True))
    for i, link in enumerate((drawer.get("links") or [])[:8], 1):
        if isinstance(link, dict) and link.get("url"):
            data.append(_data(f"link_{i}", f"{link.get('label') or ''} {link['url']}".strip()))
    for i, im in enumerate((drawer.get("images") or [])[:8], 1):
        if isinstance(im, dict) and im.get("url"):
            data.append(_data(f"image_{i}", im["url"]))
    for k, v in (props.get("enrichment") or {}).items():
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            data.append(_data(k, v))
    bits.append("      <ExtendedData>")
    bits.extend(data)
    bits.append("      </ExtendedData>")
    bits.append(_kml_geom(geom))
    bits.append("    </Placemark>")
    return "\n".join(bits)


def _coord(p: list) -> str:
    lon, lat = float(p[0]), float(p[1])
    alt = float(p[2]) if len(p) > 2 else 0.0
    return f"{lon:.6f},{lat:.6f},{alt:.0f}"


def _kml_geom(geom: dict) -> str:
    t = geom.get("type")
    c = geom.get("coordinates") or []
    if t == "Point":
        return f"      <Point><coordinates>{_coord(c)}</coordinates></Point>"
    if t == "LineString":
        text = " ".join(_coord(p) for p in c)
        return f"      <LineString><tessellate>1</tessellate><coordinates>{text}</coordinates></LineString>"
    ring = " ".join(_coord(p) for p in (c[0] if c else []))
    return ("      <Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{ring}</coordinates></LinearRing></outerBoundaryIs></Polygon>")


def validate_kml(text: str) -> list[str]:
    """Parse KML 2.2 and check lon,lat order against ExtendedData latitude/longitude."""
    import xml.etree.ElementTree as ET
    errs: list[str] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [f"KML did not parse: {exc}"]
    if root.tag != f"{{{NS}}}kml":
        errs.append("root element must be kml in the OGC 2.2 namespace")
    ns = {"k": NS}
    marks = root.findall(".//k:Placemark", ns)
    if root.find("k:Document", ns) is None:
        errs.append("KML needs a Document")
    for i, pm in enumerate(marks):
        data = {}
        for d in pm.findall("k:ExtendedData/k:Data", ns):
            val = d.find("k:value", ns)
            data[d.attrib.get("name") or ""] = (val.text or "") if val is not None else ""
        geoms = [c.tag.rsplit("}", 1)[-1] for c in list(pm) if c.tag.rsplit("}", 1)[-1] in ("Point", "LineString", "Polygon")]
        if len(geoms) != 1:
            errs.append(f"Placemark {i} must have one Point, LineString or Polygon")
            continue
        coord_el = pm.find(".//k:coordinates", ns)
        if coord_el is None or not (coord_el.text or "").strip():
            errs.append(f"Placemark {i} has no coordinates")
            continue
        tuples = []
        for tok in (coord_el.text or "").split():
            parts = tok.split(",")
            if len(parts) < 2:
                errs.append(f"Placemark {i} coordinate {tok!r} is not lon,lat")
                continue
            try:
                lon, lat = float(parts[0]), float(parts[1])
            except ValueError:
                errs.append(f"Placemark {i} coordinate {tok!r} is not numeric")
                continue
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                errs.append(f"Placemark {i} coordinate out of range ({lon}, {lat})")
            tuples.append((lon, lat))
        if geoms[0] == "Polygon" and tuples and tuples[0] != tuples[-1]:
            errs.append(f"Placemark {i} polygon ring is not closed")
        if geoms[0] == "LineString" and len(tuples) < 2:
            errs.append(f"Placemark {i} line needs 2+ positions")
        if "latitude" in data and "longitude" in data and tuples:
            try:
                lon_p, lat_p = float(data["longitude"]), float(data["latitude"])
            except ValueError:
                errs.append(f"Placemark {i} latitude/longitude are not numbers")
                continue
            lon, lat = tuples[0]
            if abs(lon - lon_p) > 1e-4 or abs(lat - lat_p) > 1e-4:
                errs.append(f"Placemark {i} coordinates are not lon,lat "
                            f"(got {lon},{lat}; properties {lon_p},{lat_p})")
    return errs


def _photoreal_scene(data: dict) -> bool:
    parts = []
    for src in (data, data.get("meta") if isinstance(data.get("meta"), dict) else {},
                data.get("camera") if isinstance(data.get("camera"), dict) else {}):
        for k in ("map_stack", "stack", "basemap", "imagery", "style"):
            if src.get(k):
                parts.append(str(src[k]))
    blob = " ".join(parts).lower()
    return "photoreal" in blob or "google 3d" in blob or "3d tiles" in blob


def _under_data(path: Path) -> bool:
    try:
        path.resolve().relative_to(investigations.data_dir().resolve())
        return True
    except Exception:
        return False


def _screenshot_ok(path: Path, *, photoreal_scene: bool) -> bool:
    if photoreal_scene or not path.is_file() or path.suffix.lower() not in _IMAGE_EXT:
        return False
    if path.stat().st_size > 8_000_000 or path.stat().st_size <= 0:
        return False
    name = path.name.lower()
    if "photoreal" in name or ("google" in name and "tile" in name):
        return False
    return _under_data(path)


def _resolve_shot(item: str) -> Path:
    p = Path(item).expanduser()
    if not p.is_absolute():
        p = investigations.data_dir() / p
    return p


def _collect_screenshots(data: dict, slug: str) -> tuple[list[Path], int]:
    """Local image files for this investigation. Never fetches, never includes photoreal tiles."""
    blocked = _photoreal_scene(data)
    found: list[Path] = []
    seen = set()
    omitted = 0

    def consider(p: Path):
        nonlocal omitted
        try:
            p = p.resolve()
        except Exception:
            return
        if p in seen or not p.is_file() or p.suffix.lower() not in _IMAGE_EXT or not _under_data(p):
            return
        seen.add(p)
        if not _screenshot_ok(p, photoreal_scene=blocked):
            omitted += 1
            return
        found.append(p)

    raw = data.get("screenshots")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                consider(_resolve_shot(item))
    root = investigations.data_dir()
    for folder in (root / "screenshots", investigations.reports_dir()):
        if folder.is_dir():
            for p in sorted(folder.glob(slug + "*")):
                consider(p)
    return found, omitted


def _summary(name: str, counts: dict, written: list[str], shots: list[Path], shot_omitted: int) -> str:
    bits = [f"{name}: {counts['points']} point(s), {counts['lines']} line(s), {counts['polygons']} polygon(s)"]
    if counts.get("skipped"):
        bits.append(f"{counts['skipped']} skipped")
    text = ", ".join(bits) + f". Wrote {', '.join(written)}."
    if counts.get("photoreal_omitted") or shot_omitted:
        text += (f" Omitted {counts.get('photoreal_omitted', 0)} photoreal image link(s)"
                 + (f" and {shot_omitted} screenshot(s)" if shot_omitted else "")
                 + " (Google photoreal tiles are viewing only).")
    else:
        text += " Google photoreal tile imagery is not included."
    if shots:
        text += f" Packed {len(shots)} saved screenshot(s)."
    return text


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _dest(slug: str, ext: str) -> Path:
    folder = investigations.data_dir() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = folder / f"{slug}-{stamp}{ext}"
    n = 2
    while path.exists():
        path = folder / f"{slug}-{stamp}-{n}{ext}"
        n += 1
    return path


def _scrub_spec(spec: dict, omitted: dict) -> None:
    if spec.get("description_md"):
        spec["description_md"] = _scrub_text(spec["description_md"], omitted)
    if spec.get("description"):
        spec["description"] = _scrub_text(spec["description"], omitted)
    spec["images"] = _images(spec.get("images"), omitted)
    spec["links"] = _links(spec.get("links"), omitted)
    for k in list(spec):
        if _SENSITIVE_NAME.search(str(k)):
            spec.pop(k, None)


def _sanitize_for_report(data: dict, omitted: dict) -> dict:
    """Copy used for the markdown report so photoreal URLs are not written there either.

    Saved investigations keep a ``pins`` list beside ``annotations``. The report reads
    ``pins`` first, so both copies are scrubbed.
    """
    import copy
    data = copy.deepcopy(data)
    for key in ("annotations", "pins", "routes", "cards"):
        for ann in data.get(key) or []:
            if not isinstance(ann, dict):
                continue
            spec = ann.get("spec") if isinstance(ann.get("spec"), dict) else ann
            _scrub_spec(spec, omitted)
    if isinstance(data.get("notes"), str):
        data["notes"] = _scrub_text(data["notes"], omitted)
    return data


def export_data(data: dict, *, fmt: str = "both") -> dict:
    """Write GeoJSON and/or KML (and an optional zip pack) under plugin-data/godseye/exports/."""
    fmt = str(fmt or "both").lower()
    if fmt not in FORMATS:
        return {"ok": False, "error": "bad_args", "message": "format must be geojson, kml, both or pack."}
    if not isinstance(data, dict):
        return {"ok": False, "error": "bad_args", "message": "Nothing to export."}
    fc, counts = build_feature_collection(data)
    errs = validate_geojson(fc)
    if errs:
        return {"ok": False, "error": "invalid_geojson", "message": "; ".join(errs[:6])}
    kml = to_kml(fc)
    kerrs = validate_kml(kml)
    if kerrs:
        return {"ok": False, "error": "invalid_kml", "message": "; ".join(kerrs[:6])}
    slug = investigations.slugify(data.get("name") or "scene")
    geo_text = json.dumps(fc, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    want_geo = fmt in ("geojson", "both", "pack")
    want_kml = fmt in ("kml", "both", "pack")
    out: dict = {"ok": True, "name": fc["name"], "format": fmt, "counts": counts,
                 "validated": {"geojson": "RFC 7946", "kml": "2.2"}}
    written = []
    geo_path = kml_path = None
    if want_geo:
        geo_path = _dest(slug, ".geojson")
        _write(geo_path, geo_text)
        out["geojson"] = str(geo_path)
        written.append("GeoJSON")
    if want_kml:
        kml_path = _dest(slug, ".kml")
        _write(kml_path, kml)
        out["kml"] = str(kml_path)
        written.append("KML")
    shots: list[Path] = []
    shot_omitted = 0
    if fmt == "pack":
        shots, shot_omitted = _collect_screenshots(data, slug)
        note_parts = []
        if shots:
            note_parts.append("Saved screenshots (not Google photoreal tiles): " + ", ".join(p.name for p in shots))
        if shot_omitted or _photoreal_scene(data):
            note_parts.append("Google photoreal tile imagery is viewing only and is not stored in this export.")
        if not note_parts:
            note_parts.append("No saved screenshot was packed. Google photoreal tile imagery is not embedded.")
        report = _sanitize_for_report(data, {"photoreal": 0, "inline_images": 0, "skipped": 0})
        md = investigations.render_markdown(report, screenshot_note=" ".join(note_parts))
        md_path = _dest(slug, ".md")
        _write(md_path, md)
        out["markdown"] = str(md_path)
        written.append("Markdown")
        pack_path = _dest(slug, ".zip")
        with zipfile.ZipFile(pack_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{slug}.geojson", geo_text)
            zf.writestr(f"{slug}.kml", kml)
            zf.writestr(f"{slug}.md", md)
            for shot in shots:
                zf.write(shot, arcname=f"screenshots/{shot.name}")
        out["pack"] = str(pack_path)
        out["screenshots"] = [str(p) for p in shots]
        written.append("pack")
    out["summary"] = _summary(fc["name"], counts, written, shots, shot_omitted)
    if shot_omitted:
        out["screenshots_omitted"] = shot_omitted
    return out


def export_investigation(name: str | None = None, *, scene_store=None, session_key: str | None = None,
                         fmt: str = "both", data: dict | None = None,
                         notes: str | None = None) -> dict:
    """Export a named investigation, or the current scene when ``name`` is omitted."""
    if data is None:
        if name:
            try:
                name = investigations.validate_name(name)
            except ValueError as exc:
                return {"ok": False, "error": "bad_args", "message": str(exc)}
            path = investigations.inv_dir() / f"{investigations.slugify(name)}.json"
            if not path.is_file():
                return {"ok": False, "error": "not_found", "message": f"No investigation named {name!r}."}
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                return {"ok": False, "error": "bad_args", "message": f"Investigation {name!r} is not valid JSON."}
        elif scene_store is not None:
            data = investigations.scene_snapshot(scene_store, session_key, notes=notes)
            data["name"] = "Current scene"
        else:
            return {"ok": False, "error": "bad_args", "message": "Give name or a live scene."}
    return export_data(data, fmt=fmt)
