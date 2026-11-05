(() => {
  const ready = async () => {
    for (let i = 0; i < 600; i++) {
      const run = window.__godsEyeView?.voiceCommands?.runner
      if (typeof run === 'function') return run
      await new Promise((r) => setTimeout(r, 200))
    }
    throw new Error('God\'s Eye View is still loading')
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
    const hit = cam.pickEllipsoid({ x: c.clientWidth / 2, y: c.clientHeight / 2 }, v.scene.globe.ellipsoid)
    if (!hit) return { ok: false, error: 'nothing under the screen centre' }
    const p = cam.positionWC
    const range = Number(args?.rangeM) || Math.hypot(p.x - hit.x, p.y - hit.y, p.z - hit.z)
    const rad = (d) => d * Math.PI / 180
    const heading = typeof args?.headingDeg === 'number' ? rad(args.headingDeg) : cam.heading
    cam.lookAt(hit, { heading, pitch: rad(Number(args?.pitchDeg ?? -15)), range })
    cam.lookAtTransform(cam.transform.constructor.IDENTITY)  // release the lock so orbit/fly still work
    window.__godsEyeView.requestRender?.()
    return { ok: true, action: 'hermes_frame', camera: pose() }
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
      if (m.name === 'hermes_caption' || m.name === 'hermes_scene_info' || m.name === 'hermes_frame') {
        payload = m.name === 'hermes_caption' ? caption(m.args?.text) : m.name === 'hermes_frame' ? frame(m.args) : sceneInfo()
        return window.parent.postMessage({ type: 'hermes-gev:result', id: m.id, payload }, '*')
      }
      const run = await ready()
      const ctl = new AbortController()
      const timer = setTimeout(() => ctl.abort(), 45000)
      try {
        const result = safe(await run(String(m.name), m.args || {}, { signal: ctl.signal }))
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
