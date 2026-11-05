"""Prepare God's Eye View's MCP Apps globe panel for the Hermes pane's sandboxed frame.

The scripts added here run only inside the opaque-origin SandboxedFrame (never in the Hermes
renderer): a <base> for the data: document, the opt-in GEV_EMBED_FULL_UI flag, Eye Of Hermes branding
(gev/panel_brand.js), a Cesium worker
shim (gev/panel_worker_shim.js), an opening_hours subset parser (gev/panel_oh.js), the grounded pin layer + info drawer + clusters/filters (gev/panel_pins.js), the context/draw/measure/compare
layer (gev/panel_context.js) and the live-globe agent (gev/panel_live_agent.js).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_HERE = Path(__file__).parent
BASE_TAG = '<base href="https://app.gods-eye-view.invalid/">'


def _script(body: str) -> str:
    return "<" + "script>" + body + "</" + "script>"


def _json_for_script(value) -> str:
    return json.dumps(value).replace("<", "\\u003c")


def embed_boot_script(perf: str | None, config: dict | None) -> str:
    """Runs before the app module. Queues gev:config / gev:visibility and, when
    asked, makes location.search include perf and waitForConfig. No key literals
    live in this file; ``config`` is inserted at serve time."""
    allowed = {"googleMapsApiKey", "cesiumIonToken", "photonUrl", "mapillaryToken"}
    perf_js = _json_for_script(perf if perf in {"embed", "default"} else None)
    cfg = {k: v for k, v in (config or {}).items() if k in allowed and v is not None}
    wait_js = "true" if perf == "embed" or cfg else "false"
    if perf in {"embed", "default"}:
        cfg["perf"] = perf
    cfg_js = _json_for_script(cfg)
    return f"""
(function () {{
  var perf = {perf_js};
  var wait = {wait_js};
  var cfg = {cfg_js};
  if (cfg && typeof cfg === 'object' && Object.keys(cfg).length) window.GEV_RUNTIME_CONFIG = cfg;
  if (wait) window.GEV_WAIT_FOR_CONFIG = true;
  if (perf) {{
    try {{
      var desc = Object.getOwnPropertyDescriptor(Location.prototype, 'search');
      if (desc && desc.configurable && typeof desc.get === 'function') {{
        var orig = desc.get;
        Object.defineProperty(Location.prototype, 'search', {{
          configurable: true,
          get: function () {{
            var value = orig.call(this) || '';
            var add = [];
            if (perf && !/(^|[?&])perf=/.test(value)) add.push('perf=' + encodeURIComponent(perf));
            if (wait && !/(^|[?&])waitForConfig=/.test(value)) add.push('waitForConfig=1');
            if (!add.length) return value;
            if (!value || value === '?') return '?' + add.join('&');
            return value + (value.charAt(0) === '?' ? '&' : '?') + add.join('&');
          }}
        }});
      }}
    }} catch (e) {{}}
  }}
  if (!Array.isArray(window.__GEV_MESSAGE_QUEUE__)) {{
    var queue = [];
    window.__GEV_MESSAGE_QUEUE__ = queue;
    window.__GEV_QUEUE_MESSAGES__ = true;
    window.addEventListener('message', function (event) {{
      if (!window.__GEV_QUEUE_MESSAGES__) return;
      var type = event && event.data && event.data.type;
      if (type === 'gev:config' || type === 'gev:visibility') queue.push(event);
    }});
  }}
}})();
"""


def prepare(html: str, full_ui: bool = True, perf: str | None = "embed", config: dict | None = None,
            gl_recover: bool | None = None) -> str:
    if gl_recover is None:
        from .gl_recover import enabled
        gl_recover = enabled()
    head = BASE_TAG
    head += _script(embed_boot_script(perf, config))
    if full_ui:
        head += _script("window.GEV_EMBED_FULL_UI = true")
    head += _script("window.__HERMES_GL_RECOVER = " + ("true" if gl_recover else "false"))
    head += _script((_HERE / "panel_brand.js").read_text())
    head += _script((_HERE / "panel_worker_shim.js").read_text())
    head += _script((_HERE / "panel_oh.js").read_text())
    head += _script((_HERE / "panel_pins.js").read_text())
    head += _script((_HERE / "panel_context.js").read_text())
    head += _script((_HERE / "gl_recover.js").read_text())
    head += _script((_HERE / "panel_live_agent.js").read_text())
    m = re.search(r"<head[^>]*>", html, re.I)
    return html[: m.end()] + head + html[m.end():] if m else head + html
