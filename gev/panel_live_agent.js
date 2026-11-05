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
  addEventListener('message', async (event) => {
    const m = event.data
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
