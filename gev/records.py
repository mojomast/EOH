"""Turn a JSONL, CSV, or JSON file, or inline records, into pins or tour stops.

Row schema: lat, lon, label, description_md, images[], links[], tags[], category, id.
``latitude`` / ``longitude`` / ``lng``, ``name`` / ``title``, and ``description`` are accepted.
``id`` is kept as ``clientId`` so a later replace can find the same row.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

MAX_ROWS = 200
MAX_BYTES = 2_000_000
_LAT = ("lat", "latitude")
_LON = ("lon", "lng", "longitude")


def _num(value) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not -90 <= number <= 90 and False:
        return None
    return number


def _lat(row: dict) -> float | None:
    for key in _LAT:
        if key in row:
            number = _num(row.get(key))
            if number is not None and -90 <= number <= 90:
                return number
    return None


def _lon(row: dict) -> float | None:
    for key in _LON:
        if key in row:
            number = _num(row.get(key))
            if number is not None and -180 <= number <= 180:
                return number
    return None


def _list(value, limit: int) -> list:
    if isinstance(value, str) and value.strip().startswith("["):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if isinstance(value, str) and value.strip():
        return [value.strip()][:limit]
    if not isinstance(value, list):
        return []
    return value[:limit]


def _tags(row: dict) -> dict:
    tags = row.get("tags")
    if isinstance(tags, str) and tags.strip().startswith("{"):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = None
    out = dict(tags) if isinstance(tags, dict) else {}
    category = row.get("category")
    if category and "category" not in out:
        out["category"] = str(category)[:80]
    return {str(k)[:40]: (v if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)[:80])
            for k, v in list(out.items())[:16]}


def row_to_pin(row: dict) -> dict | None:
    if not isinstance(row, dict):
        return None
    lat, lon = _lat(row), _lon(row)
    if lat is None or lon is None:
        return None
    label = row.get("label") or row.get("name") or row.get("title") or ""
    pin = {
        "type": "pin",
        "latitude": lat,
        "longitude": lon,
        "label": str(label)[:120],
    }
    title = row.get("title") or row.get("name")
    if title:
        pin["title"] = str(title)[:120]
    text = row.get("description_md")
    if text is None:
        text = row.get("description")
    if isinstance(text, str) and text.strip():
        pin["description_md"] = text[:4000]
    images = _list(row.get("images"), 6)
    if images:
        pin["images"] = images
    links = _list(row.get("links"), 12)
    if links:
        pin["links"] = links
    tags = _tags(row)
    if tags:
        pin["tags"] = tags
    client = row.get("id") or row.get("clientId")
    if client:
        pin["clientId"] = str(client)[:80]
    return pin


def row_to_stop(row: dict) -> dict | None:
    pin = row_to_pin(row)
    if not pin:
        return None
    stop = {"lat": pin["latitude"], "lon": pin["longitude"]}
    if pin.get("label"):
        stop["name"] = pin["label"]
    if pin.get("description_md"):
        stop["caption"] = pin["description_md"][:160]
    return stop


def _from_json(text: str) -> list:
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("records"), list):
            return data["records"]
        if isinstance(data.get("features"), list):
            rows = []
            for feature in data["features"]:
                if not isinstance(feature, dict):
                    continue
                props = dict(feature.get("properties") or {})
                geom = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
                coords = geom.get("coordinates") if isinstance(geom.get("coordinates"), list) else None
                if coords and len(coords) >= 2 and "lon" not in props and "lat" not in props:
                    props["lon"], props["lat"] = coords[0], coords[1]
                if feature.get("id") and "id" not in props:
                    props["id"] = feature["id"]
                rows.append(props)
            return rows
    raise ValueError("JSON must be a list, {records: [...]}, or a GeoJSON FeatureCollection.")


def _from_csv(text: str) -> list:
    rows = list(csv.DictReader(text.splitlines()))
    if not rows:
        raise ValueError("CSV needs a header row and at least one record.")
    return rows


def load_text(text: str, suffix: str) -> list[dict]:
    if len(text.encode()) > MAX_BYTES:
        raise ValueError("The dataset is larger than 2 MB.")
    kind = suffix.lower()
    if kind == ".jsonl":
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    elif kind == ".csv":
        rows = _from_csv(text)
    elif kind == ".json":
        rows = _from_json(text)
    else:
        raise ValueError("Dataset files must end in .jsonl, .csv, or .json.")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"A dataset can have at most {MAX_ROWS} rows.")
    return rows


def load_path(path: str) -> tuple[list[dict], str]:
    raw = str(path or "").strip()
    if not raw or "://" in raw:
        raise ValueError("source must be a file path, not a URL.")
    from .security import import_file
    file = import_file(raw)
    if file.name.startswith(".env") or file.suffix == ".env":
        raise ValueError("Refusing to read an env file as a dataset.")
    if not file.is_file():
        raise ValueError("Dataset file was not found.")
    suffix = file.suffix.lower()
    if suffix not in (".jsonl", ".csv", ".json"):
        raise ValueError("Dataset files must end in .jsonl, .csv, or .json.")
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("Dataset file could not be read.") from exc
    return load_text(text, suffix), suffix


def pins_from(args: dict | None) -> tuple[list[dict], list[str]]:
    """Pins from ``records`` and/or ``source``. Returns ``(pins, warnings)``."""
    args = args or {}
    warnings = []
    rows: list = []
    inline = args.get("records")
    if inline is not None:
        if not isinstance(inline, list):
            raise ValueError("records must be a list of objects.")
        if len(inline) > MAX_ROWS:
            raise ValueError(f"A dataset can have at most {MAX_ROWS} rows.")
        rows.extend(inline)
    source = args.get("source")
    if source:
        loaded, _suffix = load_path(str(source))
        rows.extend(loaded)
    pins = []
    skipped = 0
    for row in rows:
        pin = row_to_pin(row)
        if pin:
            pins.append(pin)
        else:
            skipped += 1
    if skipped:
        warnings.append(f"{skipped} row(s) skipped (each row needs lat and lon).")
    if not pins and rows:
        raise ValueError("No rows had lat and lon.")
    return pins, warnings


def stops_from(args: dict | None) -> tuple[list[dict], list[str]]:
    pins, warnings = pins_from(args)
    stops = []
    for pin in pins:
        stop = {"lat": pin["latitude"], "lon": pin["longitude"]}
        if pin.get("label"):
            stop["name"] = pin["label"]
        if pin.get("description_md"):
            stop["caption"] = str(pin["description_md"])[:160]
        stops.append(stop)
    return stops, warnings
