"""Named investigations: save/load/list/delete whole scenes, and export HTML/Markdown reports."""
from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,80}$")


def data_dir() -> Path:
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    return home / "plugin-data" / "godseye"


def inv_dir() -> Path:
    p = data_dir() / "investigations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def reports_dir() -> Path:
    p = data_dir() / "reports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def slugify(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "-", str(name or "").strip(), flags=re.U)
    s = re.sub(r"-{2,}", "-", s).strip("-._") or "investigation"
    return s[:80].lower()


def _path(name: str) -> Path:
    return inv_dir() / f"{slugify(name)}.json"


def validate_name(name: str) -> str:
    name = str(name or "").strip()
    if not name or not SAFE_NAME.match(name):
        raise ValueError("name must be 1–81 chars: letters, digits, space, _ . -")
    return name


def scene_snapshot(scene_store, session_key: str | None, *, notes: str | None = None,
                   views: list | None = None, meta: dict | None = None) -> dict:
    with scene_store.lock:
        sc = dict(scene_store.scene(session_key))
        anns = [{"id": a["id"], "paneId": a.get("paneId"), "spec": dict(a.get("spec") or {})}
                for a in sc.get("annotations") or []]
        raw_shapes = sc.get("shapes") or {}
        shapes = [dict(v) for v in raw_shapes.values() if isinstance(v, dict)] if isinstance(raw_shapes, dict) else []
    pins, routes, cards = [], [], []
    for a in anns:
        t = str((a["spec"] or {}).get("type") or "pin").lower()
        if t == "route":
            routes.append(a)
        elif t == "card":
            cards.append(a)
        elif t in ("pin", "marker", "point", "highlight"):
            pins.append(a)
        else:
            pins.append(a)
    return {
        "version": 1,
        "scene_key": session_key,
        "saved_at": time.time(),
        "camera": sc.get("camera"),
        "caption": sc.get("caption"),
        "drawer": sc.get("drawer"),
        "annotations": anns,
        "pins": pins,
        "routes": routes,
        "cards": cards,
        "notes": notes,
        "views": views or [],
        "meta": meta or {},
        "shapes": shapes,
        "timeline": sc.get("timeline") if isinstance(sc.get("timeline"), dict) else None,
    }


def save(scene_store, session_key: str | None, name: str, *, notes: str | None = None,
         views: list | None = None, meta: dict | None = None) -> dict:
    name = validate_name(name)
    snap = scene_snapshot(scene_store, session_key, notes=notes, views=views, meta=meta)
    snap["name"] = name
    path = _path(name)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, indent=2))
    tmp.replace(path)
    return {"ok": True, "name": name, "path": str(path), "pins": len(snap["pins"]),
            "routes": len(snap["routes"]), "cards": len(snap["cards"]),
            "annotations": len(snap["annotations"]), "saved_at": snap["saved_at"]}


def list_all() -> dict:
    items = []
    for p in sorted(inv_dir().glob("*.json")):
        try:
            data = json.loads(p.read_text())
            items.append({"name": data.get("name") or p.stem, "path": str(p),
                          "saved_at": data.get("saved_at"),
                          "pins": len(data.get("pins") or []),
                          "routes": len(data.get("routes") or []),
                          "cards": len(data.get("cards") or []),
                          "annotations": len(data.get("annotations") or []),
                          "notes": (data.get("notes") or "")[:160] or None})
        except Exception:
            items.append({"name": p.stem, "path": str(p), "error": "unreadable"})
    return {"ok": True, "investigations": items, "count": len(items), "dir": str(inv_dir())}


def load(scene_store, session_key: str | None, name: str, *, live=None, restore: bool = True) -> dict:
    name = validate_name(name)
    path = _path(name)
    if not path.is_file():
        # try exact stem match
        alt = inv_dir() / f"{slugify(name)}.json"
        if not alt.is_file():
            return {"ok": False, "error": "not_found", "message": f"No investigation named {name!r}.",
                    **list_all()}
        path = alt
    data = json.loads(path.read_text())
    scene_store.load_into(session_key, data)
    pane = None
    if restore and live is not None:
        try:
            pane = live  # caller may pass a restore callable
            if callable(live):
                # live is live_call-like: restore via clear + annotate
                from .scene import SceneStore
                cmds = scene_store.restore_commands(session_key, clear=True)
                steps = []
                for n, a in cmds:
                    steps.append({"name": n, "ok": bool((live(n, a) or {}).get("ok"))})
                pane = {"ok": all(s["ok"] for s in steps), "steps": steps}
        except Exception as exc:
            pane = {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "name": data.get("name") or name, "path": str(path),
            "pins": len(data.get("pins") or []), "routes": len(data.get("routes") or []),
            "cards": len(data.get("cards") or []), "notes": data.get("notes"),
            "camera": data.get("camera"), "pane": pane}


def delete(name: str, *, confirm: bool = False) -> dict:
    if not confirm:
        return {"ok": False, "error": "confirm_required",
                "message": "Refusing to delete without confirm=true."}
    name = validate_name(name)
    path = _path(name)
    if not path.is_file():
        return {"ok": False, "error": "not_found", "message": f"No investigation named {name!r}."}
    path.unlink()
    return {"ok": True, "deleted": name, "path": str(path)}


def _sources_from_pins(pins: list) -> list[dict]:
    seen, out = set(), []
    for a in pins:
        spec = a.get("spec") or {}
        md = str(spec.get("description_md") or "")
        for link in spec.get("links") or []:
            if isinstance(link, dict) and link.get("url") and link["url"] not in seen:
                seen.add(link["url"])
                out.append({"label": link.get("label") or link["url"], "url": link["url"],
                            "pin": spec.get("label") or a.get("id")})
        for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)]+)\)", md):
            if m.group(2) not in seen:
                seen.add(m.group(2))
                out.append({"label": m.group(1), "url": m.group(2),
                            "pin": spec.get("label") or a.get("id")})
    return out


def render_markdown(data: dict, *, screenshot_note: str | None = None) -> str:
    name = data.get("name") or "Investigation"
    lines = [f"# {name}", "", f"_Saved {_fmt_time(data.get('saved_at'))}_", ""]
    if data.get("notes"):
        lines += ["## Notes", "", str(data["notes"]), ""]
    cam = data.get("camera") or {}
    if cam:
        lines += ["## Map view", "",
                  f"- Camera: `{cam.get('lat')}, {cam.get('lon')}` height `{cam.get('heightM')}` "
                  f"pitch `{cam.get('pitchDeg')}` heading `{cam.get('headingDeg')}`", ""]
    if data.get("caption"):
        lines += [f"**Caption:** {data['caption']}", ""]
    tl = data.get("timeline") if isinstance(data.get("timeline"), dict) else None
    if tl and tl.get("mode"):
        lines += ["## Timeline", "",
                  f"- Mode: `{tl.get('mode')}`",
                  f"- Window: `{tl.get('from')}` to `{tl.get('to')}`",
                  f"- Playhead: `{tl.get('at')}`", ""]
    if screenshot_note:
        lines += ["## Map screenshot", "", screenshot_note, ""]
    pins = data.get("pins") or [a for a in data.get("annotations") or []
                                if str((a.get("spec") or {}).get("type") or "pin").lower() != "route"]
    lines += ["## Pins", "",
              "| # | Label | Lat | Lon | Drawer |",
              "|---|-------|-----|-----|--------|"]
    for i, a in enumerate(pins, 1):
        s = a.get("spec") or {}
        has = "yes" if (s.get("description_md") or s.get("images") or s.get("links")) else "empty"
        lines.append(f"| {i} | {s.get('label') or s.get('title') or a.get('id')} | "
                     f"{s.get('latitude')} | {s.get('longitude')} | {has} |")
    lines.append("")
    for a in pins:
        s = a.get("spec") or {}
        title = s.get("title") or s.get("label") or a.get("id")
        lines += [f"### {title}", ""]
        if s.get("description_md"):
            lines += [s["description_md"], ""]
        for im in s.get("images") or []:
            if isinstance(im, dict) and im.get("url"):
                lines.append(f"![{im.get('alt') or title}]({im['url']})")
                if im.get("caption"):
                    lines.append(f"*{im['caption']}*")
                lines.append("")
        for lk in s.get("links") or []:
            if isinstance(lk, dict) and lk.get("url"):
                lines.append(f"- [{lk.get('label') or lk['url']}]({lk['url']})")
        lines.append("")
    routes = data.get("routes") or []
    if routes:
        lines += ["## Routes", ""]
        for a in routes:
            s = a.get("spec") or {}
            pts = s.get("points") or []
            lines.append(f"- **{s.get('label') or a.get('id')}**: {len(pts)} waypoints")
        lines.append("")
    cards = data.get("cards") or []
    if cards:
        lines += ["## Cards", ""]
        for a in cards:
            s = a.get("spec") or {}
            lines.append(f"- **{s.get('title') or s.get('label') or a.get('id')}**")
        lines.append("")
    srcs = _sources_from_pins(pins)
    lines += ["## Sources", ""]
    if srcs:
        for s in srcs:
            lines.append(f"- [{s['label']}]({s['url']}) _(pin: {s['pin']})_")
    else:
        lines.append("_No sources recorded._")
    lines.append("")
    return "\n".join(lines)


def render_html(data: dict, *, screenshot_data_url: str | None = None,
                screenshot_note: str | None = None) -> str:
    md = render_markdown(data, screenshot_note=None)  # we'll embed image ourselves
    name = html.escape(str(data.get("name") or "Investigation"))
    pins = data.get("pins") or []
    rows = []
    for i, a in enumerate(pins, 1):
        s = a.get("spec") or {}
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                i, html.escape(str(s.get("label") or s.get("title") or a.get("id"))),
                html.escape(str(s.get("latitude"))), html.escape(str(s.get("longitude"))),
                "yes" if (s.get("description_md") or s.get("images") or s.get("links")) else "empty"))
    pin_sections = []
    for a in pins:
        s = a.get("spec") or {}
        title = html.escape(str(s.get("title") or s.get("label") or a.get("id")))
        body = html.escape(str(s.get("description_md") or "_No details._")).replace("\n", "<br>\n")
        imgs = "".join(
            f'<figure><img src="{html.escape(im["url"])}" alt="{html.escape(str(im.get("alt") or ""))}" loading="lazy"/>'
            f'<figcaption>{html.escape(str(im.get("caption") or ""))}</figcaption></figure>'
            for im in (s.get("images") or []) if isinstance(im, dict) and im.get("url"))
        links = "".join(
            f'<li><a href="{html.escape(lk["url"])}" rel="noopener noreferrer">{html.escape(str(lk.get("label") or lk["url"]))}</a></li>'
            for lk in (s.get("links") or []) if isinstance(lk, dict) and lk.get("url"))
        pin_sections.append(f"<section class='pin'><h3>{title}</h3><div class='md'>{body}</div>{imgs}"
                            + (f"<ul class='links'>{links}</ul>" if links else "") + "</section>")
    srcs = _sources_from_pins(pins)
    src_html = "".join(f'<li><a href="{html.escape(s["url"])}">{html.escape(s["label"])}</a> '
                       f'(pin: {html.escape(str(s["pin"]))})</li>' for s in srcs) or "<li><em>No sources recorded.</em></li>"
    shot = ""
    if screenshot_data_url and screenshot_data_url.startswith("data:image/"):
        shot = f'<section><h2>Map screenshot</h2><img class="shot" src="{screenshot_data_url}" alt="Map screenshot"/></section>'
    elif screenshot_note:
        shot = f"<section><h2>Map screenshot</h2><p><em>{html.escape(screenshot_note)}</em></p></section>"
    cam = data.get("camera") or {}
    notes = html.escape(str(data.get("notes") or "")) 
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><title>{name} — Eye Of Hermes report</title>
<style>
body{{font:14px/1.45 system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#102018;background:#f7fffb}}
h1,h2,h3{{color:#0a3d2c}} table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}
th,td{{border:1px solid #9fd9c3;padding:6px 8px;text-align:left}} th{{background:#d8fff0}}
.pin{{border:1px solid #9fd9c3;border-radius:8px;padding:12px 14px;margin:12px 0;background:#fff}}
img{{max-width:100%;border-radius:4px}} .shot{{border:1px solid #9fd9c3}}
.links{{padding-left:18px}} footer{{margin-top:32px;color:#567;font-size:12px}}
</style></head><body>
<h1>{name}</h1>
<p><em>Saved {_fmt_time(data.get('saved_at'))}</em></p>
{f'<section><h2>Notes</h2><p>{notes}</p></section>' if data.get('notes') else ''}
<section><h2>Map view</h2>
<p>Camera: <code>{html.escape(str(cam.get('lat')))}, {html.escape(str(cam.get('lon')))}</code>
height <code>{html.escape(str(cam.get('heightM')))}</code></p>
{f'<p><strong>Caption:</strong> {html.escape(str(data.get("caption")))}</p>' if data.get('caption') else ''}
</section>
{shot}
<section><h2>Pin table</h2>
<table><thead><tr><th>#</th><th>Label</th><th>Lat</th><th>Lon</th><th>Drawer</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="5"><em>No pins</em></td></tr>'}</tbody></table>
</section>
<section><h2>Pin details</h2>{''.join(pin_sections) or '<p><em>No pins</em></p>'}</section>
<section><h2>Sources</h2><ul>{src_html}</ul></section>
<footer>Eye Of Hermes (EOH) investigation report · self-contained · generated locally</footer>
<!-- markdown mirror available alongside this file -->
</body></html>"""


def _fmt_time(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "unknown time"
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(ts))


def export_report(name: str | None = None, *, data: dict | None = None,
                  scene_store=None, session_key: str | None = None,
                  screenshot_data_url: str | None = None,
                  screenshot_note: str | None = None,
                  share: dict | None = None) -> dict:
    if data is None:
        if name:
            path = _path(validate_name(name))
            if not path.is_file():
                return {"ok": False, "error": "not_found", "message": f"No investigation named {name!r}."}
            data = json.loads(path.read_text())
        elif scene_store is not None:
            data = scene_snapshot(scene_store, session_key)
            data["name"] = name or f"scene-{int(time.time())}"
        else:
            return {"ok": False, "error": "bad_args", "message": "Give name or a live scene."}
    title = slugify(data.get("name") or name or "report")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    base = reports_dir() / f"{title}-{stamp}"
    note = screenshot_note
    if not screenshot_data_url and not note:
        note = ("Map screenshot unavailable: the pane canvas could not be captured "
                "(WebGL canvases with cross-origin imagery often raise SecurityError on "
                "toDataURL). Open the investigation and screenshot the pane manually, or "
                "retry when the basemap tiles are same-origin.")
    md = render_markdown(data, screenshot_note=note if not screenshot_data_url else None)
    html_doc = render_html(data, screenshot_data_url=screenshot_data_url, screenshot_note=note)
    md_path = base.with_suffix(".md")
    html_path = base.with_suffix(".html")
    md_path.write_text(md)
    html_path.write_text(html_doc)
    out = {"ok": True, "name": data.get("name"), "markdown": str(md_path), "html": str(html_path),
           "dir": str(reports_dir()), "screenshot": bool(screenshot_data_url),
           "screenshot_note": note if not screenshot_data_url else None}
    if share:
        out["share"] = share
    return out


def capture_map(live) -> dict:
    """Ask the pane for a canvas PNG data URL via hermes_capture. Fail soft."""
    if live is None:
        return {"ok": False, "unavailable": True, "note": "no live callable"}
    try:
        res = live("hermes_capture", {})
    except Exception as exc:
        return {"ok": False, "unavailable": True, "note": str(exc)[:200]}
    result = (res or {}).get("result") if isinstance(res, dict) else None
    result = result if isinstance(result, dict) else (res if isinstance(res, dict) else {})
    data_url = result.get("dataUrl") or result.get("data_url")
    if result.get("ok") and data_url and str(data_url).startswith("data:image/"):
        # Cap enormous payloads (~2 MB text)
        if len(data_url) > 2_500_000:
            return {"ok": False, "unavailable": True,
                    "note": "Screenshot too large to embed; open the HTML and capture manually."}
        return {"ok": True, "dataUrl": data_url, "width": result.get("width"), "height": result.get("height")}
    return {"ok": False, "unavailable": True,
            "note": result.get("error") or result.get("note")
            or "Canvas capture failed (likely cross-origin WebGL taint)."}
