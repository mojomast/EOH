// Overlay sidebar button. The pin-category strip that used to sit here was retired.
(() => {
  const MIGRATION = 'The bottom pin-category strip was retired. Category, rating, and open-now filters are in the Overlays sidebar. godseye_annotate op=filter still sets them.'

  function apply(items) {
    const list = Array.isArray(items) ? items : (items && Array.isArray(items.items) ? items.items : [])
    let applied = 0
    let needsRedraw = false
    const off = new Set()
    const on = new Set()
    for (const item of list) {
      if (!item || typeof item !== 'object') continue
      const shown = item.visible !== false && item.shown !== false
      const paneId = item.paneId || item.storeId
      if (item.source === 'shape' || String(item.id || '').startsWith('shape:')) {
        const id = item.storeId || String(item.id || '').replace(/^shape:/, '')
        if (window.__hermesContext?.setVisible?.(id, shown)) applied++
        else needsRedraw = true
        continue
      }
      if (item.source !== 'annotation' && !String(item.id || '').startsWith('ann:')) continue
      if (item.kind === 'routes' || item.kind === 'areas') {
        const hide = window.__godsEyeView?.annotations?.setHidden
        if (typeof hide === 'function' && paneId) {
          try { hide(paneId, !shown); applied++ } catch { needsRedraw = true }
        } else needsRedraw = true
      } else if (window.__hermesPins?.setOverlay?.(paneId, shown)) {
        applied++
      } else needsRedraw = true
      if (paneId) (shown ? on : off).add(String(paneId))
    }
    const layer = typeof document !== 'undefined' ? document.getElementById?.('hermes-gev-cards') : null
    if (layer) {
      for (const el of layer.children || []) {
        const key = el?.dataset?.cardKey
        if (!key) continue
        if (off.has(key)) el.dataset.overlayOff = '1'
        else if (on.has(key)) delete el.dataset.overlayOff
      }
    }
    return { ok: true, action: 'hermes_overlay', applied, needsRedraw, migration: MIGRATION }
  }

  function mount() {
    if (typeof document === 'undefined' || !document.body) return
    if (document.getElementById?.('hermes-gev-overlays-btn')) return
    const btn = document.createElement('button')
    btn.id = 'hermes-gev-overlays-btn'
    btn.type = 'button'
    btn.textContent = 'Overlays'
    btn.title = MIGRATION
    btn.setAttribute('aria-label', 'Overlays')
    btn.style.cssText = 'position:fixed;left:50%;bottom:84px;transform:translateX(-50%);z-index:2147483646;background:rgba(3,14,11,.9);color:#bfffe6;border:1px solid rgba(120,255,200,.45);border-radius:8px;padding:6px 14px;cursor:pointer;font:600 12px ui-monospace,Menlo,monospace'
    btn.addEventListener('click', () => {
      try { window.parent.postMessage({ type: 'hermes-gev:overlays', detail: { toggle: true } }, '*') } catch {}
    })
    document.body.appendChild(btn)
  }

  if (typeof document !== 'undefined') {
    const timer = setInterval(() => { if (document.body) mount() }, 1000)
    if (timer && typeof timer.unref === 'function') timer.unref()
  }
  window.__hermesOverlays = { apply, mount, migration: MIGRATION }
})()
