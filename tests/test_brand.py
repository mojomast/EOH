"""Eye Of Hermes (EOH) branding inside the pane (gev/panel_brand.js) and user-facing strings."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gev import live, panel_inject

ROOT = Path(__file__).parents[1]
NODE = shutil.which("node") or str(Path.home() / ".local/node24/bin/node")

# Minimal DOM: the upstream header / loader / panel status + open button, with a MutationObserver
# stand-in that lets the test re-render the header and re-run the observer.
DOM = r"""
let observers = []
const mk = (tag, cls, text, kids = []) => { const el = { tagName: tag.toUpperCase(), className: cls || '', dataset: {}, attrs: {}, title: '',
  children: kids, parentElement: null, _text: text,
  get textContent() { return this._text != null ? this._text : this.children.map((c) => c.textContent).join('') },
  set textContent(v) { this._text = v; this.children = [] },
  appendChild(c) { if (this._text != null) { this.children = [{ textContent: this._text, children: [] }]; this._text = null } c.parentElement = this; this.children.push(c); return c },
  setAttribute(k, v) { this.attrs[k] = v },
  querySelector(q) { return find(this, (e) => q === '.title-accent' && e.className === 'title-accent') } }
  for (const k of kids) k.parentElement = el; return el }
const find = (root, pred) => { for (const c of root.children || []) { if (c.tagName && pred(c)) return c; const r = c.children && find(c, pred); if (r) return r } return null }
const all = (root, pred, out = []) => { for (const c of root.children || []) { if (c.tagName && pred(c)) out.push(c); if (c.children) all(c, pred, out) } return out }
const header = () => mk('h1', '', null, [mk('span', 'title-logo', ''), mk('span', '', null, [mk('span', '', "GOD'S EYE "), mk('span', 'title-accent', 'VIEW')])])
const body = mk('body', '', null, [header(), mk('p', 'subtitle', 'NO PLACE LEFT BEHIND'), mk('h2', '', null, [mk('span', '', "GOD'S EYE "), mk('span', 'title-accent', 'VIEW')]),
  Object.assign(mk('div', '', "Loading God's Eye View…"), { id: 'status' }), Object.assign(mk('button', '', "Open in God's Eye View"), { id: 'open' }),
  mk('p', 'credit', "Data: God's Eye View contributors")])
global.document = { title: "God's Eye View", readyState: 'complete', documentElement: body,
  querySelectorAll: (q) => q === 'h1, h2' ? all(body, (e) => e.tagName === 'H1' || e.tagName === 'H2') : q === '.subtitle' ? all(body, (e) => e.className === 'subtitle') : [],
  getElementById: (id) => find(body, (e) => e.id === id), createElement: (t) => mk(t, '', ''), addEventListener() {} }
global.window = { requestAnimationFrame: (f) => f() }
global.MutationObserver = function (cb) { this.observe = () => observers.push(cb) }
"""


def run(body: str):
    src = (ROOT / "gev" / "panel_brand.js").read_text()
    out = subprocess.run([NODE, "-e", DOM + "\n;" + src + "\n;" + body], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not Path(NODE).exists(), reason="node not installed")
def test_brand_swaps_header_title_status_and_survives_rerender():
    r = run(r"""
      const h1 = () => body.children[0]
      const first = { title: document.title, h1: h1().textContent, tip: h1().title, sub: body.children[1].textContent,
        h2: body.children[2].textContent, status: document.getElementById('status').textContent,
        open: document.getElementById('open').textContent, credit: body.children[5].textContent }
      body.children[0] = header(); document.title = "God's Eye View"     // the app re-renders its header
      for (const cb of observers) cb([])
      console.log(JSON.stringify({ first, again: h1().textContent, title2: document.title, obs: observers.length }))""")
    f = r["first"]
    assert f["title"] == "Eye Of Hermes (EOH)" and f["h1"].strip() == "EOH" and f["h2"] == "EOH"
    assert "built on God's Eye View" in f["tip"] and f["sub"] == "EYE OF HERMES"
    assert f["status"] == "Loading Eye Of Hermes (EOH)…" and f["open"] == "Open full app"
    assert f["credit"] == "Data: God's Eye View contributors"            # credits are left alone
    assert r["again"].strip() == "EOH" and r["title2"] == "Eye Of Hermes (EOH)" and r["obs"] == 1


def test_brand_script_injected_first():
    html = panel_inject.prepare("<html><head><title>God's Eye View</title></head><body></body></html>")
    assert html.index("__hermesBrand") < html.index("__hermesPins = {") < html.index("const prepAnnotate")


def test_user_facing_strings_use_eoh():
    assert "Eye Of Hermes (EOH) pane" in live.NO_PANE["message"] and "God's Eye View" not in live.NO_PANE["message"]
    assert "Eye Of Hermes (EOH)" in live.PANE_BUSY["message"]
    assert json.loads((ROOT / "dashboard" / "manifest.json").read_text())["label"] == "Eye Of Hermes (EOH)"
    js = (ROOT / "desktop" / "plugin.js").read_text()
    assert "name: \"Eye Of Hermes (EOH)\"" in js and "title: \"God's Eye View\"" not in js
