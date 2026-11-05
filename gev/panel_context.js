// Hermes context + measure + compare layer for the God's Eye View panel (runs inside the sandboxed frame).
//
// - context(): what the user is looking at: camera centre/heading/pitch/range, visible bbox, the
//   selected (drawer-open) pin, the last clicked ground point and every shape the user drew.
// - a small draw toolbar (box / polygon / line, labels with distance or area, clear) and an
//   "Ask Hermes" button that hands a prompt to the host (clipboard + composer focus; plugin.js).
// - draw(): Hermes-drawn lines/polygons (measurements, isochrones) as Cesium entities.
// - compare(): split-screen swipe between two imagery layers (e.g. Esri Wayback dates) using
//   Cesium's ImageryLayer.splitDirection + scene.splitPosition, with a draggable divider.
(() => {
  const g = () => window.__godsEyeView || {}
  const C = () => window.Cesium
  const R = 6371008.8
  const rad = (d) => d * Math.PI / 180
  const r6 = (x) => Math.round(x * 1e6) / 1e6
  // ---- geometry (mirrors gev/measure.py) ---------------------------------------------------
  const hav = (a, b) => {
    const p1 = rad(a.lat), p2 = rad(b.lat), dp = p2 - p1, dl = rad(b.lon - a.lon)
    const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
  }
  const pathLen = (pts) => pts.slice(1).reduce((s, p, i) => s + hav(pts[i], p), 0)
  const area = (pts) => {
    if (pts.length < 3) return 0
    let t = 0
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length]
      let dl = rad(b.lon - a.lon); dl = ((dl + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI
      t += dl * (2 + Math.sin(rad(a.lat)) + Math.sin(rad(b.lat)))
    }
    return Math.abs(t * R * R / 2)
  }
  const fmtD = (m) => (m < 1000 ? `${m.toFixed(0)} m` : m < 1e5 ? `${(m / 1000).toFixed(2)} km` : `${(m / 1000).toFixed(0)} km`)
  const fmtA = (m2) => (m2 < 1e4 ? `${m2.toFixed(0)} m²` : m2 < 1e6 ? `${(m2 / 1e4).toFixed(2)} ha` : `${(m2 / 1e6).toFixed(2)} km²`)
  const mid = (pts) => ({ lat: pts.reduce((s, p) => s + p.lat, 0) / pts.length, lon: pts.reduce((s, p) => s + p.lon, 0) / pts.length })
  const boxPts = (a, b) => [{ lat: a.lat, lon: a.lon }, { lat: a.lat, lon: b.lon }, { lat: b.lat, lon: b.lon }, { lat: b.lat, lon: a.lon }]
  const measureOf = (kind, pts) => {
    if (kind === 'line') { const m = pathLen(pts); return { meters: Math.round(m * 10) / 10, label: fmtD(m) } }
    const a = area(pts), per = pathLen([...pts, pts[0]])
    return { areaM2: Math.round(a * 10) / 10, perimeterM: Math.round(per * 10) / 10, label: fmtA(a) }
  }

  // ---- scene state -------------------------------------------------------------------------
  const shapes = new Map()  // id -> { id, kind, points, label, measure, by, ents }
  let ds = null, handler = null, seq = 0, mode = null, draft = [], draftEnts = [], lastClick = null, bar = null
  const detach = () => {
    try { handler?.destroy?.() } catch {}
    handler = null
    ds = null
    for (const s of shapes.values()) s.ents = []
    draftEnts = []
  }
  const ensure = () => {
    const Ce = C(), v = g().viewer
    if (!Ce || !v) return false
    if (!ds) {
      ds = new Ce.CustomDataSource('hermes-shapes')
      v.dataSources.add(ds)
      handler = new Ce.ScreenSpaceEventHandler(v.scene.canvas)
      handler.setInputAction((ev) => onClick(ev.position), Ce.ScreenSpaceEventType.LEFT_CLICK)
      handler.setInputAction(() => { if (mode === 'polygon' || mode === 'line') finish() }, Ce.ScreenSpaceEventType.LEFT_DOUBLE_CLICK)
      try { document.addEventListener('keydown', (e) => { if (!mode) return; if (e.key === 'Escape') cancel(); if (e.key === 'Enter') finish() }, true) } catch {}
    }
    return true
  }
  const ground = (pos) => {
    const Ce = C(), v = g().viewer
    if (!pos || !v) return null
    let cart = null
    try { if (v.scene.pickPositionSupported) cart = v.scene.pickPosition(pos) } catch {}
    try { if (!cart) cart = v.camera.pickEllipsoid(pos, v.scene.globe.ellipsoid) } catch {}
    if (!cart) return null
    const c = Ce.Cartographic.fromCartesian(cart)
    return { lat: r6(c.latitude * 180 / Math.PI), lon: r6(c.longitude * 180 / Math.PI), h: Number.isFinite(c.height) ? Math.round(c.height) : null }
  }
  function onClick(pos) {
    const p = ground(pos)
    if (!p) return
    if (!mode) { lastClick = { ...p, at: new Date().toISOString() }; return }
    draft.push({ lat: p.lat, lon: p.lon })
    if (mode === 'box' && draft.length === 2) return finish()
    drawDraft()
  }
  const col = (c, a = 1) => { const Ce = C(); try { return Ce.Color.fromCssColorString(c || '#ffe45a').withAlpha(a) } catch { return Ce.Color.YELLOW } }
  const ents = (kind, pts, label, color, labelAt) => {
    const Ce = C(), out = []
    const ring = kind === 'line' ? pts : [...pts, pts[0]]
    const cart = ring.map((p) => Ce.Cartesian3.fromDegrees(p.lon, p.lat))
    if (kind !== 'line' && pts.length >= 3)
      out.push(ds.entities.add({ polygon: { hierarchy: new Ce.PolygonHierarchy(pts.map((p) => Ce.Cartesian3.fromDegrees(p.lon, p.lat))),
        material: col(color, 0.22), classificationType: Ce.ClassificationType?.BOTH } }))
    if (cart.length >= 2)
      out.push(ds.entities.add({ polyline: { positions: cart, width: 3, clampToGround: true, material: col(color, 0.95) } }))
    for (const p of pts) out.push(ds.entities.add({ position: Ce.Cartesian3.fromDegrees(p.lon, p.lat), point: { pixelSize: 6, color: col(color), disableDepthTestDistance: Number.POSITIVE_INFINITY } }))
    if (label) {
      const at = kind === 'line' ? pts[pts.length - 1] : labelAt === 'top' ? pts.reduce((a, b) => (b.lat > a.lat ? b : a)) : mid(pts)
      out.push(ds.entities.add({ position: Ce.Cartesian3.fromDegrees(at.lon, at.lat), label: { text: String(label).slice(0, 80),
        font: '600 13px ui-monospace, Menlo, monospace', fillColor: Ce.Color.fromCssColorString('#fffbe0'), showBackground: true,
        backgroundColor: Ce.Color.fromCssColorString('rgba(20,16,0,0.82)'), pixelOffset: new Ce.Cartesian2(0, -14),
        disableDepthTestDistance: Number.POSITIVE_INFINITY, heightReference: Ce.HeightReference?.CLAMP_TO_GROUND } }))
    }
    try { g().viewer.scene.requestRender?.() } catch {}
    return out
  }
  const clearDraft = () => { for (const e of draftEnts) try { ds.entities.remove(e) } catch {}; draftEnts = [] }
  const drawDraft = () => { clearDraft(); if (draft.length) draftEnts = ents(mode === 'line' ? 'line' : 'polygon', draft, null, '#ffffff') }
  const add = (kind, points, opts = {}) => {
    if (!ensure()) return { ok: false, error: 'viewer not ready' }
    const k = kind === 'box' ? 'polygon' : kind
    let pts = (points || []).map((p) => ({ lat: Number(p.lat ?? p.latitude ?? p[0]), lon: Number(p.lon ?? p.longitude ?? p[1]) }))
      .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lon))
    if (kind === 'box' && pts.length === 2) pts = boxPts(pts[0], pts[1])
    if ((k === 'line' && pts.length < 2) || (k === 'polygon' && pts.length < 3)) return { ok: false, error: `${kind} needs more points` }
    const measure = measureOf(k, pts)
    const id = String(opts.id || `shape-${++seq}`)
    remove(id)
    const label = opts.label === undefined ? measure.label : opts.label
    const s = { id, kind: kind === 'box' ? 'box' : k, points: pts, label, measure, by: opts.by || 'hermes', ents: ents(k, pts, label, opts.color, opts.labelAt) }
    shapes.set(id, s)
    if (s.by === 'user') updateBar()
    return { ok: true, shape: info(s) }
  }
  function remove(id) { const s = shapes.get(id); if (!s) return false; for (const e of s.ents) try { ds.entities.remove(e) } catch {}; shapes.delete(id); return true }
  const info = (s) => ({ id: s.id, kind: s.kind, by: s.by, label: s.label, points: s.points.slice(0, 200), measure: s.measure })
  const start = (m) => { if (!ensure()) return { ok: false, error: 'viewer not ready' }; cancel(); mode = m; draft = []; updateBar(); return { ok: true, mode } }
  function finish() {
    const m = mode, pts = draft
    mode = null; draft = []; clearDraft()
    updateBar()
    if (!m) return null
    // a double-click also fired two clicks at the same point: drop the duplicate tail
    const dedup = pts.filter((p, i) => i === 0 || Math.abs(p.lat - pts[i - 1].lat) + Math.abs(p.lon - pts[i - 1].lon) > 1e-7)
    const r = add(m, dedup, { by: 'user' })
    updateBar()
    return r
  }
  function cancel() { mode = null; draft = []; if (ds) clearDraft(); updateBar() }

  // ---- context -----------------------------------------------------------------------------
  const camera = () => {
    const Ce = C(), v = g().viewer
    if (!Ce || !v) return null
    const cam = v.camera, c = v.canvas
    const deg = (r) => Math.round(r * 1800 / Math.PI) / 10
    const out = { headingDeg: deg(cam.heading), pitchDeg: deg(cam.pitch) }
    if (typeof cam.roll === 'number') out.rollDeg = deg(cam.roll)
    try { const pc = cam.positionCartographic; out.position = { lat: r6(pc.latitude * 180 / Math.PI), lon: r6(pc.longitude * 180 / Math.PI), heightM: Math.round(pc.height) } } catch {}
    try {
      const hit = cam.pickEllipsoid({ x: c.clientWidth / 2, y: c.clientHeight / 2 }, v.scene.globe.ellipsoid)
      if (hit) {
        const k = Ce.Cartographic.fromCartesian(hit), p = cam.positionWC
        out.center = { lat: r6(k.latitude * 180 / Math.PI), lon: r6(k.longitude * 180 / Math.PI) }
        out.rangeM = Math.round(Math.hypot(p.x - hit.x, p.y - hit.y, p.z - hit.z))
      }
    } catch {}
    try {
      const rect = cam.computeViewRectangle(v.scene.globe.ellipsoid)
      if (rect) out.bbox = { south: r6(rect.south * 180 / Math.PI), west: r6(rect.west * 180 / Math.PI), north: r6(rect.north * 180 / Math.PI), east: r6(rect.east * 180 / Math.PI) }
    } catch {}
    if (!out.center && out.bbox) out.center = { lat: r6((out.bbox.south + out.bbox.north) / 2), lon: r6((out.bbox.west + out.bbox.east) / 2) }
    return out
  }
  const selectedPin = () => {
    try { const st = window.__hermesPins?.state?.(); if (st?.open) { const p = st.pins.find((x) => x.id === st.open); if (p) return p } } catch {}
    return null
  }
  const context = () => {
    const user = [...shapes.values()].filter((s) => s.by === 'user')
    const out = { ok: true, action: 'hermes_context', camera: camera(), selectedPin: selectedPin(), lastClick,
      drawn: user.map(info), hermesShapes: [...shapes.values()].filter((s) => s.by !== 'user').map((s) => ({ id: s.id, kind: s.kind, label: s.label })),
      drawing: mode, compare: cmp ? { left: cmp.left?.label, right: cmp.right?.label, position: cmp.pos } : null,
      timeline: (() => { try { return window.__hermesTimeline?.state?.() || null } catch { return null } })() }
    try { out.pins = (window.__hermesPins?.state?.().pins || []).slice(0, 30).map((p) => ({ id: p.id, label: p.label, latitude: p.latitude, longitude: p.longitude })) } catch {}
    // focus: what "this" most likely means: open pin > newest drawn shape > last click > screen centre
    const last = user[user.length - 1]
    out.focus = out.selectedPin ? { kind: 'pin', lat: out.selectedPin.latitude, lon: out.selectedPin.longitude, label: out.selectedPin.label }
      : last ? { kind: last.kind, shapeId: last.id, ...mid(last.points), label: last.label }
      : lastClick ? { kind: 'click', lat: lastClick.lat, lon: lastClick.lon }
      : out.camera?.center ? { kind: 'view', ...out.camera.center } : null
    return out
  }

  // ---- compare (swipe) ---------------------------------------------------------------------
  let cmp = null
  const mkLayer = (side, which) => {
    const Ce = C(), v = g().viewer
    if (!side || side.kind === 'current') return null
    if (side.kind !== 'xyz' || !side.url) throw new Error(`cannot show ${side.label || side.kind} side by side (only imagery tile layers can be split)`)
    const prov = new Ce.UrlTemplateImageryProvider({ url: side.url, maximumLevel: side.maxZoom || 19, credit: side.credit || '' })
    const layer = v.imageryLayers.addImageryProvider(prov)
    layer.splitDirection = which === 'left' ? Ce.SplitDirection.LEFT : Ce.SplitDirection.RIGHT
    return layer
  }
  const stopCompare = () => {
    if (!cmp) return { ok: true, action: 'hermes_compare', active: false }
    const v = g().viewer
    for (const l of [cmp.l, cmp.r]) if (l) try { v.imageryLayers.remove(l, true) } catch {}
    try { if (cmp.tiles != null && g().tileset) g().tileset.show = cmp.tiles } catch {}
    try { v.scene.splitPosition = 0.5 } catch {}
    try { cmp.el.remove() } catch {}
    cmp = null
    try { v.scene.requestRender?.() } catch {}
    return { ok: true, action: 'hermes_compare', active: false }
  }
  const setPos = (x) => {
    if (!cmp) return
    cmp.pos = Math.max(0, Math.min(1, x))
    try { g().viewer.scene.splitPosition = cmp.pos } catch {}
    cmp.el.style.left = `${cmp.pos * 100}%`
    try { g().viewer.scene.requestRender?.() } catch {}
  }
  const compare = (args = {}) => {
    const action = String(args.action || 'start')
    if (action === 'stop') return stopCompare()
    if (action === 'state') return { ok: true, action: 'hermes_compare', active: Boolean(cmp), ...(cmp ? { left: cmp.left.label, right: cmp.right.label, position: cmp.pos } : {}) }
    if (action === 'position' || action === 'toggle') {
      if (!cmp) return { ok: false, error: 'no compare active' }
      setPos(action === 'toggle' ? (cmp.pos > 0.5 ? 0 : 1) : Number(args.position ?? 0.5))
      return { ok: true, action: 'hermes_compare', active: true, position: cmp.pos }
    }
    const Ce = C(), v = g().viewer
    if (!Ce || !v) return { ok: false, error: 'viewer not ready' }
    if (!Ce.SplitDirection) return { ok: false, error: 'this Cesium build has no imagery split support' }
    stopCompare()
    const left = args.left || { kind: 'current', label: 'Current view' }, right = args.right || { kind: 'current', label: 'Current view' }
    let l = null, r = null, tiles = null
    try {
      // photoreal 3D tiles sit on top of imagery and would hide both sides: hide them while comparing
      if (g().tileset && g().tileset.show) { tiles = true; g().tileset.show = false }
      l = mkLayer(left, 'left'); r = mkLayer(right, 'right')
    } catch (e) {
      for (const x of [l, r]) if (x) try { v.imageryLayers.remove(x, true) } catch {}
      if (tiles) try { g().tileset.show = true } catch {}
      return { ok: false, error: String(e?.message || e) }
    }
    const el = document.createElement('div')
    el.id = 'hermes-gev-swipe'
    el.style.cssText = 'position:fixed;top:0;bottom:0;left:50%;width:4px;margin-left:-2px;background:#ffe45a;z-index:2147483646;cursor:ew-resize;box-shadow:0 0 8px rgba(0,0,0,.7)'
    el.innerHTML = '<span style="position:absolute;top:8px;right:8px;white-space:nowrap;font:600 11px ui-monospace,monospace;color:#fffbe0;background:rgba(20,16,0,.85);padding:3px 6px;border-radius:3px"></span>' +
      '<span style="position:absolute;top:8px;left:8px;white-space:nowrap;font:600 11px ui-monospace,monospace;color:#fffbe0;background:rgba(20,16,0,.85);padding:3px 6px;border-radius:3px"></span>'
    try { el.children[0].textContent = `◀ ${left.label}`; el.children[1].textContent = `${right.label} ▶` } catch {}
    let drag = false
    el.addEventListener('pointerdown', (e) => { drag = true; try { el.setPointerCapture(e.pointerId) } catch {} })
    el.addEventListener('pointermove', (e) => { if (drag) setPos(e.clientX / (window.innerWidth || 1)) })
    el.addEventListener('pointerup', () => { drag = false })
    document.body.appendChild(el)
    cmp = { l, r, left, right, el, tiles, pos: 0.5 }
    setPos(Number(args.position ?? 0.5))
    return { ok: true, action: 'hermes_compare', active: true, left: left.label, right: right.label, position: cmp.pos, hidPhotorealTiles: Boolean(tiles) }
  }

  // ---- toolbar + Ask Hermes ----------------------------------------------------------------
  const ask = (prompt) => { try { window.parent.postMessage({ type: 'hermes-gev:ask', prompt: String(prompt).slice(0, 2000) }, '*') } catch {} }
  const askPrompt = () => {
    const c = context(), f = c.focus
    if (!f) return 'What am I looking at in the EOH globe?'
    if (f.kind === 'pin') return `What is "${f.label || 'this pin'}" (${f.lat.toFixed(5)}, ${f.lon.toFixed(5)}) on my EOH globe? Tell me about it.`
    if (f.kind === 'box' || f.kind === 'polygon') return `What is in the area I drew on the EOH globe (${f.label}, around ${f.lat.toFixed(5)}, ${f.lon.toFixed(5)})?`
    if (f.kind === 'line') return `Tell me about the line I drew on the EOH globe (${f.label}).`
    return `What is here (${f.lat.toFixed(5)}, ${f.lon.toFixed(5)}) on my EOH globe?`
  }
  function updateBar() {
    if (!bar) return
    for (const b of bar.querySelectorAll('button[data-m]')) b.style.background = b.dataset.m === mode ? 'rgba(90,70,0,.92)' : 'rgba(3,14,11,.88)'
    const hint = bar.querySelector('.hint')
    if (hint) hint.textContent = mode === 'box' ? 'click 2 corners' : mode ? 'click points, double-click/Enter to finish, Esc cancels' : ''
  }
  const mountBar = () => {
    if (bar || !document.body) return
    bar = document.createElement('div')
    bar.id = 'hermes-gev-tools'
    bar.style.cssText = 'position:fixed;left:10px;bottom:110px;z-index:2147483646;display:flex;gap:4px;align-items:center;font:600 12px ui-monospace,Menlo,monospace'
    const btn = 'background:rgba(3,14,11,.88);color:#ffe9a0;border:1px solid rgba(255,228,90,.55);border-radius:4px;padding:4px 7px;cursor:pointer;font:inherit'
    bar.innerHTML = `<button data-m="box" title="Draw a box" style="${btn}">▭ Box</button><button data-m="polygon" title="Draw a polygon (area)" style="${btn}">⬠ Area</button>` +
      `<button data-m="line" title="Draw a line (distance)" style="${btn}">／ Line</button><button data-a="clear" title="Clear drawings" style="${btn}">✕</button>` +
      `<button data-a="ask" title="Copy a question about this to the Hermes chat" style="${btn}">Ask Hermes</button><span class="hint" style="color:#fffbe0;text-shadow:0 0 4px #000"></span>`
    bar.addEventListener('click', (e) => {
      const b = e.target?.closest?.('button')
      if (!b) return
      if (b.dataset.m) return mode === b.dataset.m ? cancel() : start(b.dataset.m)
      if (b.dataset.a === 'clear') { for (const s of [...shapes.values()]) if (s.by === 'user') remove(s.id); return }
      if (b.dataset.a === 'ask') ask(askPrompt())
    })
    document.body.appendChild(bar)
  }
  // "Ask Hermes" inside the pin drawer header
  const drawerAsk = () => {
    const d = document.getElementById('hermes-gev-drawer')
    if (!d || d.querySelector('.hermes-ask')) return
    const b = document.createElement('button')
    b.className = 'hermes-ask'; b.type = 'button'; b.textContent = 'Ask Hermes'
    b.style.cssText = 'background:none;border:1px solid rgba(120,255,200,.45);color:#8fffd0;border-radius:4px;height:28px;padding:0 8px;cursor:pointer;font:600 11px ui-monospace,monospace'
    b.addEventListener('click', () => ask(askPrompt()))
    const h = d.querySelector('header'), x = d.querySelector('.x')
    try { h.insertBefore(b, x) } catch { try { h.appendChild(b) } catch {} }
  }
  try {
    addEventListener('message', (e) => {
      if (e.source !== window.parent || e.data?.type !== 'hermes-gev:ask-result') return
      const hint = bar?.querySelector?.('.hint')
      if (hint) { hint.textContent = e.data.copied ? 'Question copied: paste it in the Hermes chat (Ctrl/Cmd+V)' : 'Could not copy the question'; setTimeout(() => updateBar(), 6000) }
    })
  } catch {}
  const boot = setInterval(() => { if (g().viewer && C()) { ensure(); mountBar() } drawerAsk() }, 1000)

  const command = (name, args = {}) => {
    if (name === 'hermes_context') return context()
    if (name === 'hermes_compare') return compare(args)
    const action = String(args.action || 'add')
    if (action === 'start') return start(['box', 'polygon', 'line'].includes(args.kind) ? args.kind : 'polygon')
    if (action === 'cancel') { cancel(); return { ok: true, mode: null } }
    if (action === 'list') return { ok: true, shapes: [...shapes.values()].map(info) }
    if (action === 'remove') return { ok: remove(String(args.id || '')), removed: args.id }
    if (action === 'clear') {
      const who = args.who || 'all'
      for (const s of [...shapes.values()]) if (who === 'all' || s.by === who) remove(s.id)
      return { ok: true, remaining: shapes.size }
    }
    return add(args.kind || 'line', args.points, { id: args.id, label: args.label, color: args.color, labelAt: args.labelAt, by: 'hermes' })
  }
  window.__hermesContext = { command, context, compare, add, remove, detach, start, finish, cancel, onClick, askPrompt,
    _geo: { hav, pathLen, area, fmtD, fmtA }, _stop: () => clearInterval(boot) }
})()
