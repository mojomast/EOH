(() => {
  const ready = async () => {
    for (let i = 0; i < 600; i++) {
      const run = window.__godsEyeView?.voiceCommands?.runner
      if (typeof run === 'function') return run
      await new Promise((r) => setTimeout(r, 200))
    }
    throw new Error('Eye Of Hermes (EOH) is still loading')
  }
  // Hermes-only caption line over the globe (godseye_brief_and_show / HUD summary line).
  const caption = (text) => {
    let el = document.getElementById('hermes-gev-caption')
    if (!el) {
      el = document.createElement('div')
      el.id = 'hermes-gev-caption'
      el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:2147483647;max-width:80%;' +
        'padding:6px 14px;border:1px solid rgba(120,255,200,.55);border-radius:4px;background:rgba(0,12,8,.78);color:#8fffd0;' +
        'font:600 13px/1.35 ui-monospace,Menlo,monospace;letter-spacing:.04em;pointer-events:none;text-shadow:0 0 6px rgba(0,255,170,.5)'
      document.body.appendChild(el)
    }
    el.textContent = String(text || '').slice(0, 160)
    el.style.display = text ? 'block' : 'none'
    return { ok: true, action: 'hermes_caption', text: el.textContent }
  }
  const deg = (r) => (typeof r === 'number' ? Math.round((r * 180 / Math.PI) * 10) / 10 : null)
  // Camera pose the upstream view state leaves out (pitch/heading), in degrees.
  const pose = () => {
    const cam = window.__godsEyeView?.viewer?.camera
    if (!cam) return null
    const h = window.__godsEyeView.viewer.scene?.globe?.ellipsoid?.cartesianToCartographic?.(cam.positionWC)?.height
    const out = { pitchDeg: deg(cam.pitch), headingDeg: deg(cam.heading), heightM: typeof h === 'number' ? Math.round(h) : null }
    try {  // what the camera looks at (screen centre on the globe) and how far away it is
      const v = window.__godsEyeView.viewer
      const c = v.canvas
      const hit = cam.pickEllipsoid({ x: c.clientWidth / 2, y: c.clientHeight / 2 }, v.scene.globe.ellipsoid)
      if (hit) {
        const carto = v.scene.globe.ellipsoid.cartesianToCartographic(hit)
        const p = cam.positionWC
        out.targetLat = Math.round(carto.latitude * 180 / Math.PI * 1e6) / 1e6
        out.targetLon = Math.round(carto.longitude * 180 / Math.PI * 1e6) / 1e6
        out.rangeM = Math.round(Math.hypot(p.x - hit.x, p.y - hit.y, p.z - hit.z))
      }
    } catch {}
    return out
  }
  // Read-only facts for the doctor: active map stack and why it fell back, annotation count, pose.
  const sceneInfo = () => {
    const g = window.__godsEyeView || {}
    let stack = null
    try {
      const st = g.mapStackController?.getState?.()
      if (st) stack = { activeId: st.activeId, status: st.status, lastError: st.lastError || null, switchOrigin: st.switchOrigin || null,
        stacks: (st.stacks || []).map((x) => ({ id: x.id, available: x.available !== false })) }
    } catch {}
    let annotations = null
    try { const a = g.annotations; annotations = a?.list?.()?.length ?? a?.items?.length ?? a?.size ?? null } catch {}
    // Compact feedProvenance for the doctor (overall + per-layer feedState only; no payloads).
    let feedProvenance = null
    try {
      const snap = g.layerSnapshot?.getAll?.() || g.dataLayers?.getAll?.() || g.layers?.getAll?.() || null
      const layers = Array.isArray(snap) ? snap.map((x) => ({
        id: x.id || x.layerId, name: x.name || x.label || x.id,
        feedState: x.feedState || x.state || (x.enabled === false ? 'off' : null),
        source: x.source || x.provider || undefined
      })).filter((x) => x.id || x.name) : []
      if (layers.length) {
        const rank = { unavailable: 0, loading: 1, degraded: 2, stale: 3, fallback: 4, partial: 5, nominal: 6, off: 7 }
        const active = layers.filter((l) => l.feedState && l.feedState !== 'off')
        let overall = null, best = 99
        for (const l of active) {
          const r = rank[l.feedState] ?? 50
          if (r < best) { best = r; overall = l.feedState }
        }
        feedProvenance = { overall: overall || (layers.length ? 'off' : null), layers }
      }
    } catch {}
    return { ok: true, action: 'hermes_scene_info', ready: Boolean(g.viewer), mapStack: stack, annotations, camera: pose(), feedProvenance }
  }
  // Exact framing: orbit the look-at point to an explicit pitch/heading/range (no eased nudge).
  const frame = (args) => {
    const v = window.__godsEyeView?.viewer
    if (!v) return { ok: false, error: 'viewer not ready' }
    const cam = v.camera, c = v.canvas
    try { cam.cancelFlight?.() } catch {}  // an unfinished flyTo would overwrite the pose
    const hit = cam.pickEllipsoid({ x: c.clientWidth / 2, y: c.clientHeight / 2 }, v.scene.globe.ellipsoid)
    if (!hit) return { ok: false, error: 'nothing under the screen centre' }
    const p = cam.positionWC
    const range = Number(args?.rangeM) || Math.hypot(p.x - hit.x, p.y - hit.y, p.z - hit.z)
    const rad = (d) => d * Math.PI / 180
    const heading = typeof args?.headingDeg === 'number' ? rad(args.headingDeg) : cam.heading
    cam.lookAt(hit, { heading, pitch: rad(Number(args?.pitchDeg ?? -15)), range })
    cam.lookAtTransform(cam.transform.constructor.IDENTITY)  // release the lock so orbit/fly still work
    try { v.scene.requestRender?.() } catch {}
    return { ok: true, action: 'hermes_frame', camera: pose() }
  }


  // PNG data URL of the Cesium canvas (may fail when tiles taint the WebGL canvas).
  const capture = () => {
    const canvas = window.__godsEyeView?.viewer?.scene?.canvas
    if (!canvas) return { ok: false, action: 'hermes_capture', error: 'no canvas', note: 'viewer not ready' }
    try {
      const dataUrl = canvas.toDataURL('image/png')
      if (!dataUrl || dataUrl.length < 32) return { ok: false, action: 'hermes_capture', error: 'empty capture' }
      return { ok: true, action: 'hermes_capture', mime: 'image/png', dataUrl,
        width: canvas.width, height: canvas.height }
    } catch (e) {
      return { ok: false, action: 'hermes_capture', error: String(e && e.message || e),
        note: 'Canvas may be tainted by cross-origin imagery; screenshot unavailable.' }
    }
  }

  // ---- framing presets (fly_to_location framing) ----------------------------------------
  const FRAMING = { street: [150, -15], rooftop: [450, -30], district: [2500, -45] }
  const MIN_RANGE_M = 100
  // ---- cards / multi-line labels: HTML callouts anchored to a resolved point ---------------
  const cards = new Map()   // key -> { el, lat, lon }
  let cardLayer = null, cardHook = null
  const esc = (t) => String(t ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  const placeCards = () => {
    const v = window.__godsEyeView?.viewer
    if (!v || !cardLayer) return
    const C3 = v.camera.positionWC.constructor
    const cam = v.camera.positionWC
    for (const c of cards.values()) {
      const gh = window.__hermesPins?.heightFor?.(c.lat, c.lon)
      if (Number.isFinite(gh)) c.h = gh
      const pos = C3.fromDegrees(c.lon, c.lat, c.h ?? 0)
      const xy = v.scene.cartesianToCanvasCoordinates?.(pos)
      // hide when behind the globe: camera->point must not pass the horizon (dot test on the normal)
      const n = { x: pos.x, y: pos.y, z: pos.z }, d = { x: pos.x - cam.x, y: pos.y - cam.y, z: pos.z - cam.z }
      const facing = n.x * d.x + n.y * d.y + n.z * d.z < 0
      if (!xy || !facing) { c.el.style.display = 'none'; continue }
      c.el.style.display = 'block'
      c.el.style.transform = `translate(${Math.round(xy.x)}px, ${Math.round(xy.y)}px) translate(-50%, calc(-100% - ${c.pinned ? 50 : 14}px))`
    }
  }
  const ensureCardLayer = () => {
    const v = window.__godsEyeView?.viewer
    if (!cardLayer) {
      cardLayer = document.createElement('div')
      cardLayer.id = 'hermes-gev-cards'
      cardLayer.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483646;pointer-events:none'
      document.body.appendChild(cardLayer)
    }
    if (v && !cardHook) cardHook = v.scene.postRender.addEventListener(placeCards)
  }
  const addCard = (key, lat, lon, spec) => {
    ensureCardLayer()
    removeCard(key)
    const el = document.createElement('div')
    el.style.cssText = 'position:absolute;left:0;top:0;min-width:120px;max-width:260px;padding:6px 9px;border-radius:5px;' +
      `border:1px solid ${spec.color || 'rgba(120,255,200,.6)'};background:rgba(0,12,8,.84);color:#d8fff0;` +
      'font:12px/1.35 ui-monospace,Menlo,monospace;box-shadow:0 2px 10px rgba(0,0,0,.5);white-space:nowrap'
    const lines = spec.lines || []
    const rows = spec.rows || []
    el.innerHTML = (spec.title ? `<div style="font-weight:700;color:#8fffd0;margin-bottom:${rows.length || lines.length ? 3 : 0}px">${esc(spec.title)}</div>` : '') +
      lines.map((l) => `<div>${esc(l)}</div>`).join('') +
      rows.map((r) => `<div style="display:flex;gap:12px;justify-content:space-between"><span>${esc(r.text)}</span>` +
        `<span style="color:#ffd27a">${esc(r.price ?? '')}</span></div>`).join('')
    cardLayer.appendChild(el)
    cards.set(key, { el, lat, lon, pinned: Boolean(spec.pinned) })
    placeCards()
    try { window.__godsEyeView?.viewer?.scene?.requestRender?.() } catch {}
  }
  function removeCard(key) { const c = cards.get(key); if (c) { c.el.remove(); cards.delete(key) } }
  const clearCards = () => { for (const k of [...cards.keys()]) removeCard(k) }
  const splitLines = (label) => (Array.isArray(label) ? label : String(label ?? '').split(/\r?\n/))
    .map((x) => String(x).trim()).filter(Boolean).slice(0, 3)
  const ptName = (pt) => pt?.target ?? pt?.name ?? null
  const hasCoords = (pt) => Number.isFinite(pt?.latitude) && Number.isFinite(pt?.longitude)
  // Rewrite Hermes-only annotation features into upstream specs; remember what to add afterwards.
  const DRAWER_KEYS = ['description_md', 'images', 'links']
  const PIN_TYPES = new Set(['pin', 'marker', 'point', 'highlight', 'card'])
  const prepAnnotate = (args) => {
    const extras = []
    const metas = []
    const anns = (args.annotations || []).map((a0, i) => {
      if (!a0 || typeof a0 !== 'object') return a0
      // drawer content stays Hermes-side (upstream never sees it)
      const meta = {}
      for (const k of DRAWER_KEYS) if (a0[k] != null) meta[k] = a0[k]
      if (a0.title != null) meta.title = a0.title
      metas[i] = meta
      const a = { ...a0 }
      for (const k of DRAWER_KEYS) delete a[k]
      if (String(a.type || 'pin').toLowerCase() !== 'card') delete a.title
      const t = String(a.type || 'pin').toLowerCase()
      if (t === 'card') {
        const rows = (Array.isArray(a.rows) ? a.rows : []).slice(0, 8).map((r) => ({ text: String(r?.text ?? '').slice(0, 60), price: r?.price == null ? '' : String(r.price).slice(0, 16) }))
        const title = String(a.title ?? a.label ?? ptName(a) ?? '').slice(0, 60)
        extras[i] = { kind: 'card', spec: { title, rows, color: a.color } }
        const { rows: _r, title: _t, ...rest } = a
        return { ...rest, type: 'pin', label: title || undefined }
      }
      if (a.label != null && (Array.isArray(a.label) || /\n/.test(String(a.label)))) {
        const lines = splitLines(a.label)
        if (lines.length > 1) {
          extras[i] = { kind: 'card', spec: { title: lines[0], lines: lines.slice(1), color: a.color } }
          return { ...a, label: lines[0] }
        }
        return { ...a, label: lines[0] || undefined }
      }
      return a
    })
    const flyAll = args.flyTo === 'all'
    return { args: { ...args, annotations: anns, flyTo: flyAll ? false : args.flyTo }, extras, flyAll, metas, orig: args.annotations || [] }
  }
  const finishAnnotate = async (run, args, prep, result, signal) => {
    const items = Array.isArray(result?.items) ? result.items : []
    const anchors = []
    for (let i = 0; i < items.length; i++) {
      const it = items[i], spec = args.annotations?.[i] || {}, extra = prep.extras[i]
      if (it?.ok && Number.isFinite(it.latitude)) anchors.push([it.latitude, it.longitude])
      // grounded Hermes pin (tip on the exact spot) + click-to-open drawer
      const type = String(prep.orig[i]?.type || spec.type || 'pin').toLowerCase()
      if (it?.ok && it.id && PIN_TYPES.has(type) && Number.isFinite(it.latitude) && window.__hermesPins) {
        const label = extra?.kind === 'card' ? extra.spec.title : (it.label ?? spec.label)
        if (window.__hermesPins.add(it.id, it.latitude, it.longitude, { ...spec, ...(prep.metas[i] || {}), label })) {
          it.grounded = true
          it.drawer = true
          if (extra?.kind === 'card') extra.spec.pinned = true
        }
      }
      if (it?.ok && extra?.kind === 'card' && Number.isFinite(it.latitude)) {
        addCard(it.id || `card-${i}`, it.latitude, it.longitude, extra.spec)
        it.card = true
      }
      // Partial route: draw the contiguous resolvable runs, name what is missing.
      if (it && !it.ok && String(spec.type).toLowerCase() === 'route' && Array.isArray(spec.points)) {
        const missing = new Set(it.failedTargets || [])
        const runs = [[]]
        for (const pt of spec.points) {
          if (!hasCoords(pt) && missing.has(ptName(pt) || 'a waypoint')) runs.push([])
          else runs[runs.length - 1].push(pt)
        }
        const legs = runs.filter((r) => r.length >= 2)
        let drawn = 0
        for (const pts of legs) {
          try {
            const r = await run('annotate_map', { annotations: [{ ...spec, points: pts, label: spec.label }], persist: args.persist !== false }, { signal })
            const li = r?.items?.[0]
            if (li?.ok) { drawn++; if (Number.isFinite(li.latitude)) anchors.push([li.latitude, li.longitude]) }
          } catch {}
        }
        for (const pt of spec.points) if (hasCoords(pt)) anchors.push([pt.latitude, pt.longitude])
        it.missingWaypoints = [...missing]
        it.legsDrawn = drawn
        it.legsPossible = legs.length
        if (drawn) {
          it.ok = true
          it.partial = true
          result.drawn = (result.drawn || 0) + 1
          result.failed = Math.max(0, (result.failed || 0) - 1)
          result.partial = true
          result.ok = true
        }
        result.missingWaypoints = [...(result.missingWaypoints || []), ...missing]
      }
    }
    if (prep.flyAll && anchors.length) {
      const lat0 = anchors.reduce((s, a) => s + a[0], 0) / anchors.length
      const lon0 = anchors.reduce((s, a) => s + a[1], 0) / anchors.length
      const R = 6371000, rad = (d) => d * Math.PI / 180
      let far = 0
      for (const [la, lo] of anchors) {
        const x = rad(lo - lon0) * Math.cos(rad((la + lat0) / 2)), y = rad(la - lat0)
        far = Math.max(far, Math.hypot(x, y) * R)
      }
      const rangeM = Math.max(800, Math.min(20_000_000, Math.round(far * 2.6 + 400)))
      try {
        await run('fly_to_location', { latitude: lat0, longitude: lon0, rangeM, waitForArrival: true }, { signal })
        frame({ pitchDeg: -55, rangeM })
        result.framedAll = { count: anchors.length, rangeM }
      } catch (e) { result.framedAll = { error: String(e?.message || e) } }
    }
    return result
  }
  const safe = (v) => { try { return JSON.parse(JSON.stringify(v ?? null)) } catch { return String(v) } }
  // WebGL lifecycle. Browsers cap live WebGL contexts (~16) and free a removed frame's context
  // only on GC, so a pane that remounts leaks contexts until Cesium can no longer create one.
  // Release ours explicitly when the frame goes away, and report loss instead of dying silently.
  const tell = (type, detail) => { try { window.parent.postMessage({ type, detail }, '*') } catch {} }
  let disposed = false
  const dispose = () => {
    if (disposed) return
    disposed = true
    const viewer = window.__godsEyeView?.viewer
    try { viewer?.scene?.context?._gl?.getExtension('WEBGL_lose_context')?.loseContext() } catch {}
    try { if (viewer && !viewer.isDestroyed?.()) viewer.destroy() } catch {}
  }
  addEventListener('pagehide', dispose)
  addEventListener('webglcontextlost', (e) => { if (!disposed) { e.preventDefault(); tell('hermes-gev:gl-lost') } }, true)
  addEventListener('webglcontextrestored', () => tell('hermes-gev:gl-restored'), true)
  // Cesium's "Error constructing CesiumWidget" dialog: hide it and let the pane offer a clean reload.
  new MutationObserver(() => {
    const panel = document.querySelector('.cesium-widget-errorPanel')
    if (panel && panel.style.display !== 'none') {
      tell('hermes-gev:gl-failed', String(panel.innerText || '').slice(0, 200))
      panel.style.display = 'none'
    }
  }).observe(document.documentElement, { childList: true, subtree: true })
  // Tell the pane when the app can run commands (it does not take relay commands before this).
  let isReady = false
  ready().then(() => { isReady = true; tell('hermes-gev:ready') }).catch(() => {})
  addEventListener('message', async (event) => {
    const m = event.data
    if (event.source === window.parent && m?.type === 'hermes-gev:dispose') return dispose()
    if (event.source === window.parent && m?.type === 'hermes-gev:ping') return isReady && tell('hermes-gev:ready')
    if (event.source !== window.parent || !m || m.type !== 'hermes-gev:run') return
    let payload
    try {
      if (m.name === 'hermes_pin_drawer') {
        await ready()
        payload = window.__hermesPins ? window.__hermesPins.command(m.args || {}) : { ok: false, error: 'pin layer not loaded' }
        return window.parent.postMessage({ type: 'hermes-gev:result', id: m.id, payload }, '*')
      }
      if (m.name === 'hermes_caption' || m.name === 'hermes_scene_info' || m.name === 'hermes_frame' || m.name === 'hermes_capture') {
        payload = m.name === 'hermes_caption' ? caption(m.args?.text) : m.name === 'hermes_frame' ? frame(m.args) : m.name === 'hermes_capture' ? capture() : sceneInfo()
        return window.parent.postMessage({ type: 'hermes-gev:result', id: m.id, payload }, '*')
      }
      const run = await ready()
      const ctl = new AbortController()
      const timer = setTimeout(() => ctl.abort(), 45000)
      try {
        let args = m.args || {}, prep = null, fr = null
        if (m.name === 'annotate_map') { prep = prepAnnotate(args); args = prep.args }
        if (m.name === 'clear_annotations') { clearCards(); try { window.__hermesPins?.clear() } catch {} }
        if (m.name === 'fly_to_location' && FRAMING[args.framing]) {
          fr = FRAMING[args.framing]
          const { framing, ...rest } = args
          args = { ...rest, rangeM: Math.max(MIN_RANGE_M, Number(rest.rangeM) || fr[0]), waitForArrival: true }
        }
        let result = safe(await run(String(m.name), args, { signal: ctl.signal }))
        if (prep) result = await finishAnnotate(run, args, prep, result, ctl.signal)
        if (fr && result && result.ok !== false) {
          const f = frame({ pitchDeg: fr[1], rangeM: args.rangeM })
          result.framing = { name: m.args.framing, pitchDeg: fr[1], rangeM: args.rangeM, ok: f.ok }
        }
        if (result && typeof result === 'object' && (m.name === 'get_current_view_state' || m.name === 'fly_to_location' || m.name === 'move_camera')) {
          const p = pose()
          if (p) result.camera = { ...(result.camera || {}), ...p, ...(result.camera?.heightM != null ? { heightM: result.camera.heightM } : {}) }
        }
        payload = { ok: true, result }
      }
      finally { clearTimeout(timer) }
    } catch (e) { payload = { ok: false, error: String(e?.message || e) } }
    window.parent.postMessage({ type: 'hermes-gev:result', id: m.id, payload }, '*')
  })
})()
