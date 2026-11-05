(() => {
  const ready = async () => {
    for (let i = 0; i < 150; i++) {
      const run = window.__godsEyeView?.voiceCommands?.runner
      if (typeof run === 'function') return run
      await new Promise((r) => setTimeout(r, 200))
    }
    throw new Error('God\'s Eye View is still loading')
  }
  const safe = (v) => { try { return JSON.parse(JSON.stringify(v ?? null)) } catch { return String(v) } }
  addEventListener('message', async (event) => {
    const m = event.data
    if (event.source !== window.parent || !m || m.type !== 'hermes-gev:run') return
    let payload
    try {
      const run = await ready()
      const ctl = new AbortController()
      const timer = setTimeout(() => ctl.abort(), 45000)
      try { payload = { ok: true, result: safe(await run(String(m.name), m.args || {}, { signal: ctl.signal })) } }
      finally { clearTimeout(timer) }
    } catch (e) { payload = { ok: false, error: String(e?.message || e) } }
    window.parent.postMessage({ type: 'hermes-gev:result', id: m.id, payload }, '*')
  })
})()
