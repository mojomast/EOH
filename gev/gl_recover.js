// WebGL context-loss recovery for the Eye Of Hermes pane.
//
// Cesium's maintainers say not to restore GPU state: lose the context, destroy the
// Viewer, and create a new one in the same container. This file is the policy
// (flag, backoff, failure budget) and the scene applier. The pane wires it to
// the live viewer. The harness loads the same file.
//
// Keep MAX_FAILURES and WINDOW_MS in step with gev/gl_recover.py.
(function (root, factory) {
  const api = factory()
  if (typeof module === 'object' && module.exports) module.exports = api
  root.__hermesGlRecover = api
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const MAX_FAILURES = 3
  const WINDOW_MS = 20000
  const OFF = { '0': 1, false: 1, off: 1, no: 1 }

  function recoverEnabled(value) {
    if (value === undefined) value = rootValue()
    if (value === undefined || value === null || value === '') return true
    if (typeof value === 'boolean') return value
    return !Object.prototype.hasOwnProperty.call(OFF, String(value).trim().toLowerCase())
  }

  function rootValue() {
    try { return globalThis.__HERMES_GL_RECOVER } catch { return undefined }
  }

  // attempt 1 is almost immediate so a single loss does not wait on the user.
  // Later attempts in the same window wait longer, then the budget stops them.
  function backoffMs(attempt) {
    const n = Math.max(1, Number(attempt) || 1)
    if (n <= 1) return 50
    return Math.min(2000, 100 * (2 ** (n - 1)))
  }

  function createFailureBudget(opts) {
    const max = Math.max(1, Number(opts && opts.max) || MAX_FAILURES)
    const windowMs = Math.max(1, Number(opts && opts.windowMs) || WINDOW_MS)
    const now = (opts && opts.now) || (() => Date.now())
    const stamps = []
    return {
      record(t) {
        const time = t == null ? now() : t
        const cut = time - windowMs
        while (stamps.length && stamps[0] < cut) stamps.shift()
        stamps.push(time)
        return { count: stamps.length, exhausted: stamps.length >= max }
      },
      reset() { stamps.length = 0 }
    }
  }

  function pair(cmd) {
    if (Array.isArray(cmd)) return [cmd[0], cmd[1] && typeof cmd[1] === 'object' ? cmd[1] : {}]
    if (cmd && typeof cmd === 'object' && cmd.name) return [cmd.name, cmd.args || {}]
    return ['', {}]
  }

  function num(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null
  }

  // Replay the command list SceneStore.restore_commands already builds.
  // A fresh viewer has no God's Eye View voice runner, so this is the apply step.
  function applyRestoreCommands(state, commands) {
    const target = state && typeof state === 'object' ? state : {}
    if (!target.camera || typeof target.camera !== 'object') target.camera = {}
    if (!Array.isArray(target.pins)) target.pins = []
    if (!Array.isArray(target.layers)) target.layers = []
    if (!Array.isArray(target.shapes)) target.shapes = []
    const list = Array.isArray(commands) ? commands : []
    for (const cmd of list) {
      const [name, args] = pair(cmd)
      if (name === 'fly_to_location') {
        const lat = num(args.latitude)
        const lon = num(args.longitude)
        if (lat != null) target.camera.lat = lat
        if (lon != null) target.camera.lon = lon
        if (num(args.rangeM) != null) target.camera.rangeM = args.rangeM
      } else if (name === 'hermes_frame') {
        for (const key of ['pitchDeg', 'headingDeg', 'rollDeg', 'rangeM']) {
          if (num(args[key]) != null) target.camera[key] = args[key]
        }
      } else if (name === 'clear_annotations') {
        target.pins = []
        target.annotations = []
      } else if (name === 'annotate_map') {
        const items = Array.isArray(args.annotations) ? args.annotations : []
        for (let i = 0; i < items.length; i++) {
          const spec = items[i] || {}
          const lat = num(spec.latitude)
          const lon = num(spec.longitude)
          if (lat == null || lon == null) continue
          target.pins.push({
            id: spec.id ? String(spec.id) : 'pin-' + (target.pins.length + 1),
            latitude: lat,
            longitude: lon,
            label: spec.label != null ? String(spec.label) : (spec.title != null ? String(spec.title) : ''),
            type: spec.type || 'pin',
            description_md: spec.description_md,
            images: spec.images,
            links: spec.links,
            tags: spec.tags
          })
        }
      } else if (name === 'hermes_pin_filter') {
        if (args.action === 'state' || args.action === 'expand') continue
        const filters = {}
        for (const key of ['categories', 'min_rating', 'open_now', 'include_unknown', 'cluster']) {
          if (key in args) filters[key] = args[key]
        }
        target.filters = filters
      } else if (name === 'hermes_pin_drawer') {
        if (args.action === 'close') target.drawer = null
        else if (num(args.latitude) != null && num(args.longitude) != null) {
          target.drawer = { latitude: args.latitude, longitude: args.longitude, label: args.label ?? null, open: true }
        }
      } else if (name === 'hermes_draw') {
        target.shapes.push(args)
      } else if (name === 'hermes_caption') {
        target.caption = args.text ?? null
      } else if (name === 'hermes_timeline') {
        const tl = {}
        for (const key of ['mode', 'from', 'to', 'at', 'playing', 'transition', 'index']) {
          if (args[key] != null && args[key] !== '') tl[key] = args[key]
        }
        tl.playing = false
        target.timeline = tl
      } else if (name === 'set_layer_visibility') {
        const id = typeof args.layerId === 'string' ? args.layerId : (typeof args.id === 'string' ? args.id : '')
        if (!id) continue
        target.layers = target.layers.filter((layer) => layer !== id)
        if (args.enabled !== false) target.layers.push(id)
      }
    }
    return {
      camera: Object.assign({}, target.camera),
      pins: target.pins.map((pin) => Object.assign({}, pin)),
      drawer: target.drawer ? Object.assign({}, target.drawer) : null,
      filters: target.filters ? Object.assign({}, target.filters) : null,
      layers: target.layers.slice(),
      shapes: target.shapes.slice(),
      caption: target.caption ?? null,
      timeline: target.timeline || null
    }
  }

  function canvasOf(viewer) {
    if (!viewer) return null
    return viewer.canvas || (viewer.scene && viewer.scene.canvas) || null
  }

  // onContextLost is called for a real webglcontextlost (the pane listens;
  // tests may call it directly). dispose() is the intentional teardown path:
  // loseContext, then destroy, and further losses are ignored.
  function attach(opts) {
    opts = opts || {}
    const budget = opts.budget || createFailureBudget({
      max: opts.maxFailures, windowMs: opts.windowMs, now: opts.now
    })
    const delay = opts.delay || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)))
    const enabled = typeof opts.enabled === 'function' ? opts.enabled : () => recoverEnabled(opts.enabled)
    let disposed = false
    let releasing = false
    let recovering = false
    let generation = 0

    function sameCanvas(event, viewer) {
      const canvas = canvasOf(viewer)
      if (!event || !event.target || !canvas) return true
      return event.target === canvas
    }

    async function attempt(count, gen, report) {
      if (disposed || gen !== generation) return
      recovering = true
      if (report) {
        try { opts.onLost && opts.onLost({ recovering: true }) } catch { /* host reports */ }
      }
      try { await delay(backoffMs(count)) } catch { /* keep going */ }
      if (disposed || gen !== generation) { recovering = false; return }
      const prev = typeof opts.getViewer === 'function' ? opts.getViewer() : null
      const container = typeof opts.containerOf === 'function' ? opts.containerOf(prev) : null
      let next = null
      try {
        if (typeof opts.recreate === 'function') next = await opts.recreate(prev, container)
      } catch (error) {
        recovering = false
        if (disposed || gen !== generation) return
        return fail(error, gen)
      }
      if (!next) {
        const factory = prev && prev.constructor
        releasing = true
        try {
          try { opts.releaseContext && opts.releaseContext(prev) } catch { /* already lost */ }
          try { opts.destroyViewer && opts.destroyViewer(prev) } catch { /* already gone */ }
          try { opts.setViewer && opts.setViewer(null) } catch { /* tests */ }
        } finally {
          releasing = false
        }
        if (disposed || gen !== generation) { recovering = false; return }
        try {
          next = await opts.createViewer(container, { factory, previous: prev })
        } catch (error) {
          recovering = false
          if (disposed || gen !== generation) return
          return fail(error, gen)
        }
      }
      if (!next) {
        recovering = false
        if (disposed || gen !== generation) return
        return fail(new Error('viewer was not recreated'), gen)
      }
      try { opts.setViewer && opts.setViewer(next) } catch { /* tests */ }
      let commands = []
      try { commands = await opts.getScene() } catch (error) {
        recovering = false
        if (disposed || gen !== generation) return
        return fail(error, gen)
      }
      if (disposed || gen !== generation) { recovering = false; return }
      let applied = null
      try { applied = await opts.applyScene(next, commands || []) } catch (error) {
        recovering = false
        if (disposed || gen !== generation) return
        return fail(error, gen)
      }
      if (disposed || gen !== generation) { recovering = false; return }
      recovering = false
      try { opts.onRecovered && opts.onRecovered(applied || {}) } catch { /* host reports */ }
    }

    function fail(error, gen) {
      if (disposed || gen !== generation) return
      const tick = budget.record()
      if (tick.exhausted) {
        recovering = false
        try { opts.onGiveUp && opts.onGiveUp({ exhausted: true, auto: true, error: String(error && error.message || error) }) } catch { /* host */ }
        return
      }
      generation += 1
      return attempt(tick.count, generation, false)
    }

    function onContextLost(event) {
      if (disposed || releasing) return
      const viewer = typeof opts.getViewer === 'function' ? opts.getViewer() : null
      if (!viewer || !sameCanvas(event, viewer)) return
      try { event && event.preventDefault && event.preventDefault() } catch { /* already prevented */ }
      if (!enabled()) {
        try { opts.onGiveUp && opts.onGiveUp({ exhausted: false, auto: false }) } catch { /* host shows the card */ }
        return
      }
      const tick = budget.record()
      if (tick.exhausted) {
        generation += 1
        recovering = false
        try { opts.onGiveUp && opts.onGiveUp({ exhausted: true, auto: true }) } catch { /* card */ }
        return
      }
      generation += 1
      return attempt(tick.count, generation, true)
    }

    function dispose() {
      if (disposed) return
      disposed = true
      generation += 1
      recovering = false
      const viewer = typeof opts.getViewer === 'function' ? opts.getViewer() : null
      releasing = true
      try { opts.releaseContext && opts.releaseContext(viewer) } catch { /* already lost */ }
      try { opts.destroyViewer && opts.destroyViewer(viewer) } catch { /* already gone */ }
      releasing = false
    }

    const host = typeof globalThis !== 'undefined' ? globalThis : null
    if (opts.listen !== false && host && typeof host.addEventListener === 'function') {
      host.addEventListener('webglcontextlost', onContextLost, true)
    }

    return {
      onContextLost,
      dispose,
      isRecovering: () => recovering,
      isDisposed: () => disposed
    }
  }

  return {
    MAX_FAILURES,
    WINDOW_MS,
    recoverEnabled,
    backoffMs,
    createFailureBudget,
    applyRestoreCommands,
    attach
  }
})
