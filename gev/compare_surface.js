// Surface host for Hermes compare / wayback timeline on a photoreal stack.
// Photoreal keeps globe.show===false; draping on viewer.imageryLayers then looks black.
// Prefer GEV mapStackController.acquireImageryComparison({switchPolicy:'esri-if-photoreal'}).
(function (root, factory) {
  const api = factory()
  if (typeof module === 'object' && module.exports) module.exports = api
  root.__hermesCompareSurface = api
  try { if (typeof window !== 'undefined' && window) window.__hermesCompareSurface = api } catch {}
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const ESRI_XYZ =
    'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
  const ESRI_CREDIT = 'Esri, Maxar, Earthstar Geographics'

  function needsPrep(g) {
    if (!g || !g.viewer) return false
    try {
      if (g.tileset && g.tileset.show) return true
      if (g.viewer.scene && g.viewer.scene.globe && g.viewer.scene.globe.show === false) return true
    } catch {}
    return false
  }

  function surfaceVisible(g) {
    if (!g || !g.viewer) return false
    try {
      const globeOn = g.viewer.scene && g.viewer.scene.globe && g.viewer.scene.globe.show === true
      const tilesOn = Boolean(g.tileset && g.tileset.show)
      const n = Number(g.viewer.imageryLayers && g.viewer.imageryLayers.length) || 0
      if (tilesOn) return true
      if (globeOn && n > 0) return true
      if (globeOn) return true
    } catch {}
    return false
  }

  function sleep(ms, wait) {
    const fn = wait || ((ms, cb) => setTimeout(cb, ms))
    return new Promise((resolve) => fn(ms, resolve))
  }

  async function waitGlobeShown(g, { timeoutMs = 4000, wait } = {}) {
    const start = Date.now()
    while (Date.now() - start < timeoutMs) {
      try {
        if (g.viewer && g.viewer.scene && g.viewer.scene.globe && g.viewer.scene.globe.show === true) {
          return true
        }
      } catch {}
      await sleep(40, wait)
    }
    try {
      return Boolean(g.viewer && g.viewer.scene && g.viewer.scene.globe && g.viewer.scene.globe.show === true)
    } catch {
      return false
    }
  }

  function addEsriBase(g, Cesium) {
    const Ce = Cesium || (typeof window !== 'undefined' ? window.Cesium : null)
    const v = g && g.viewer
    if (!Ce || !v || !v.imageryLayers) return null
    try {
      const prov = new Ce.UrlTemplateImageryProvider({
        url: ESRI_XYZ,
        maximumLevel: 19,
        credit: ESRI_CREDIT,
      })
      const layer = v.imageryLayers.addImageryProvider(prov)
      try { v.imageryLayers.lowerToBottom(layer) } catch {}
      return layer
    } catch {
      return null
    }
  }

  function fallbackAcquire(g, Cesium) {
    const v = g.viewer
    const prevGlobe = (() => { try { return v.scene.globe.show } catch { return true } })()
    const prevTiles = (() => { try { return g.tileset ? g.tileset.show : null } catch { return null } })()
    try { if (g.tileset) g.tileset.show = false } catch {}
    try { v.scene.globe.show = true } catch {}
    const base = addEsriBase(g, Cesium)
    let released = false
    const release = () => {
      if (released) return
      released = true
      try { if (base && v.imageryLayers) v.imageryLayers.remove(base, true) } catch {}
      try { if (prevTiles != null && g.tileset) g.tileset.show = prevTiles } catch {}
      try { v.scene.globe.show = prevGlobe } catch {}
      try { v.scene.requestRender?.() } catch {}
    }
    return { mode: 'fallback', release, base }
  }

  /**
   * Prepare a visible globe surface for draped XYZ swipe layers.
   * @returns {Promise<{ok:boolean, mode?:string, release?:Function, error?:string, fix?:string}>}
   */
  async function acquire(g, {
    owner = 'hermes-compare',
    Cesium,
    wait,
    timeoutMs = 4000,
  } = {}) {
    if (!g || !g.viewer) {
      return {
        ok: false,
        error: 'viewer not ready',
        fix: 'Open the Eye Of Hermes pane and retry.',
      }
    }
    if (!needsPrep(g)) {
      return { ok: true, mode: 'noop', release: () => {} }
    }
    const ctrl = g.mapStackController
    if (ctrl && typeof ctrl.acquireImageryComparison === 'function') {
      try {
        const lease = ctrl.acquireImageryComparison({
          owner,
          switchPolicy: 'esri-if-photoreal',
        })
        const ready = await Promise.resolve(lease.ready)
        const shown = await waitGlobeShown(g, { timeoutMs, wait })
        const status = ready && ready.status
        if ((status === 'ready' || status === 'superseded') && shown) {
          let released = false
          const release = () => {
            if (released) return
            released = true
            try {
              const out = lease.release && lease.release()
              return Promise.resolve(out)
            } catch {}
          }
          return { ok: true, mode: 'lease', release, ready }
        }
        try { lease.release && lease.release() } catch {}
      } catch (e) {
        // fall through to manual fallback
      }
    }
    const fb = fallbackAcquire(g, Cesium)
    if (!surfaceVisible(g)) {
      try { fb.release() } catch {}
      return {
        ok: false,
        error: 'no_visible_surface',
        fix: 'Switch the map stack to Esri imagery (or OSM), then retry compare. Photoreal must yield a visible globe before swipe layers can drape.',
      }
    }
    return { ok: true, ...fb }
  }

  function noSurfaceResult(action) {
    return {
      ok: false,
      error: 'no_visible_surface',
      code: 'no_visible_surface',
      message: 'Compare/timeline would leave a black globe (photoreal hidden and no globe imagery host).',
      fix: 'Stop compare, switch to an Esri/OSM globe stack, or retry after the surface-host fix. Do not treat the swipe as applied.',
      action,
    }
  }

  return {
    ESRI_XYZ,
    ESRI_CREDIT,
    needsPrep,
    surfaceVisible,
    waitGlobeShown,
    addEsriBase,
    fallbackAcquire,
    acquire,
    noSurfaceResult,
  }
})
