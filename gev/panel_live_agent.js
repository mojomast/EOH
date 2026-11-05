(() => {
  const ready = async () => {
    for (let i = 0; i < 150; i++) {
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
  addEventListener('message', async (event) => {
    const m = event.data
    if (event.source === window.parent && m?.type === 'hermes-gev:dispose') return dispose()
    if (event.source !== window.parent || !m || m.type !== 'hermes-gev:run') return
    let payload
    try {
      if (m.name === 'hermes_caption') {
        payload = caption(m.args?.text)
        return window.parent.postMessage({ type: 'hermes-gev:result', id: m.id, payload }, '*')
      }
      const run = await ready()
      const ctl = new AbortController()
      const timer = setTimeout(() => ctl.abort(), 45000)
      try { payload = { ok: true, result: safe(await run(String(m.name), m.args || {}, { signal: ctl.signal })) } }
      finally { clearTimeout(timer) }
    } catch (e) { payload = { ok: false, error: String(e?.message || e) } }
    window.parent.postMessage({ type: 'hermes-gev:result', id: m.id, payload }, '*')
  })
})()
