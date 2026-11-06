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
        if (args.action === 'clear' || args.clear) { target.timeline = null; continue }
        const mode = args.mode === 'events' ? 'events' : (args.mode === 'wayback' ? 'wayback' : null)
        if (!mode) continue
        const tl = { mode, action: 'set', playing: false }
        for (const key of ['from', 'to', 'at', 'date', 'transition', 'index', 'feeds']) {
          if (args[key] != null && args[key] !== '') tl[key] = args[key]
        }
        if (!tl.at && tl.date) tl.at = tl.date
        tl.releases = Array.isArray(args.releases) ? args.releases.map((row) => Object.assign({}, row)) : []
        tl.events = Array.isArray(args.events) ? args.events.map((row) => Object.assign({}, row)) : []
        target.timeline = tl
      } else if (name === 'set_layer_visibility') {
        const id = typeof args.layerId === 'string' ? args.layerId : (typeof args.id === 'string' ? args.id : '')
        if (!id) continue
        target.layers = target.layers.filter((layer) => layer !== id)
        if (args.enabled !== false) target.layers.push(id)
      }
    }
    const timeline = target.timeline ? Object.assign({}, target.timeline, {
      releases: Array.isArray(target.timeline.releases) ? target.timeline.releases.slice() : [],
      events: Array.isArray(target.timeline.events) ? target.timeline.events.slice() : []
    }) : null
    return {
      camera: Object.assign({}, target.camera),
      pins: target.pins.map((pin) => Object.assign({}, pin)),
      drawer: target.drawer ? Object.assign({}, target.drawer) : null,
      filters: target.filters ? Object.assign({}, target.filters) : null,
      layers: target.layers.slice(),
      shapes: target.shapes.slice(),
      caption: target.caption ?? null,
      timeline
    }
  }

  function timelineRestore(commands) {
    const list = Array.isArray(commands) ? commands : []
    let found = null
    for (const cmd of list) {
      const [name, args] = pair(cmd)
      if (name === 'hermes_timeline') found = args
    }
    if (!found) return null
    return applyRestoreCommands({}, [['hermes_timeline', found]]).timeline
  }

  // The pane applies the slider itself first. This is the one recovery re-apply:
  // if that first call raced the reboot (Cape Town), retry once on the fresh viewer.
  async function reapplyTimeline(timeline, opts) {
    opts = opts || {}
    if (!timeline || typeof timeline !== 'object' || !timeline.mode) {
      return { ok: false, skipped: true, attempts: 0, reason: 'no timeline' }
    }
    const fixed = typeof opts.apply === 'function' ? opts.apply : null
    const mounted = () => {
      try {
        const mod = typeof globalThis !== 'undefined' ? globalThis.__hermesTimeline : null
        return mod && typeof mod.command === 'function' ? (args) => mod.command(args) : null
      } catch { return null }
    }
    const args = Object.assign({}, timeline, { action: 'set', playing: false })
    delete args.emitted
    const wait = typeof opts.delay === 'function' ? opts.delay : ((ms) => new Promise((resolve) => setTimeout(resolve, ms)))
    const retryMs = Number(opts.retryMs) >= 0 ? Number(opts.retryMs) : 150
    let last = null
    let tried = false
    for (let attempt = 1; attempt <= 2; attempt++) {
      const apply = fixed || mounted()
      if (apply) {
        tried = true
        try {
          last = await apply(args)
          if (last && last.ok !== false && last.error == null) {
            return { ok: true, attempts: attempt, mode: args.mode, at: args.at || null }
          }
        } catch (error) {
          last = { ok: false, error: String((error && error.message) || error) }
        }
      }
      if (attempt < 2) { try { await wait(retryMs) } catch { /* keep going */ } }
    }
    if (!tried) return { ok: false, skipped: true, attempts: 2, reason: 'timeline not mounted' }
    return {
      ok: false, attempts: 2, mode: args.mode, at: args.at || null,
      error: last && last.error ? String(last.error) : 'timeline did not resume'
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
    let inFlight = null
    let listening = false
    const cleaned = new WeakSet()

    function sameCanvas(event, viewer) {
      const canvas = canvasOf(viewer)
      if (!event || !event.target || !canvas) return true
      return event.target === canvas
    }

    // One viewer, one context. Release the GPU slot before destroy and never
    // call either twice for the same object.
    function cleanup(viewer, clear) {
      if (!viewer || cleaned.has(viewer)) return
      cleaned.add(viewer)
      releasing = true
      try { opts.releaseContext && opts.releaseContext(viewer) } catch { /* already lost */ }
      try { opts.destroyViewer && opts.destroyViewer(viewer) } catch { /* already gone */ }
      releasing = false
      if (clear !== false) { try { opts.setViewer && opts.setViewer(null) } catch { /* tests */ } }
    }

    async function sceneCommands() {
      let last = null
      const tries = opts.sceneRetries === 0 ? 1 : 2
      for (let i = 0; i < tries; i++) {
        try { return await opts.getScene() } catch (error) {
          last = error
          if (i + 1 < tries) {
            try { await delay(Number(opts.sceneRetryMs) >= 0 ? Number(opts.sceneRetryMs) : 100) } catch { /* keep going */ }
          }
        }
      }
      throw last
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
      const factory = prev && prev.constructor
      let next = null
      if (typeof opts.recreate === 'function') {
        try {
          next = await opts.recreate(prev, container)
        } catch (error) {
          cleanup(prev)
          recovering = false
          if (disposed || gen !== generation) return
          return fail(error, gen)
        }
        // A fork hook may hand back a fresh viewer; the lost one still goes.
        if (next && next !== prev) cleanup(prev, false)
      }
      if (!next) {
        cleanup(prev)
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
      if (disposed || gen !== generation) { cleanup(next); recovering = false; return }
      try { opts.setViewer && opts.setViewer(next) } catch { /* tests */ }
      let commands = []
      try {
        commands = await sceneCommands()
      } catch (error) {
        cleanup(next)
        recovering = false
        if (disposed || gen !== generation) return
        return fail(error, gen)
      }
      if (disposed || gen !== generation) { cleanup(next); recovering = false; return }
      let applied = null
      try {
        applied = await opts.applyScene(next, commands || [])
      } catch (error) {
        cleanup(next)
        recovering = false
        if (disposed || gen !== generation) return
        return fail(error, gen)
      }
      if (disposed || gen !== generation) { cleanup(next); recovering = false; return }
      if (applied && applied.timeline) {
        applied.timelineResume = await reapplyTimeline(applied.timeline, {
          apply: opts.timelineApply,
          delay: opts.timelineDelay || delay,
          retryMs: opts.timelineRetryMs
        })
      }
      recovering = false
      if (disposed || gen !== generation) { cleanup(next); return }
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
      try { event && event.preventDefault && event.preventDefault() } catch { /* already prevented */ }
      // A second event during the same rebuild must reuse the one in flight,
      // otherwise two viewers race for the same container.
      if (recovering && inFlight) return inFlight
      const viewer = typeof opts.getViewer === 'function' ? opts.getViewer() : null
      if (!viewer || !sameCanvas(event, viewer)) return
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
      const run = attempt(tick.count, generation, true)
      inFlight = run.finally(() => { inFlight = null })
      return inFlight
    }

    function dispose() {
      if (disposed) return
      disposed = true
      generation += 1
      recovering = false
      inFlight = null
      if (listening) {
        try { host && host.removeEventListener && host.removeEventListener('webglcontextlost', listener, true) } catch { /* host gone */ }
        listening = false
      }
      const viewer = typeof opts.getViewer === 'function' ? opts.getViewer() : null
      cleanup(viewer)
    }

    const host = opts.host || (typeof globalThis !== 'undefined' ? globalThis : null)
    const listener = onContextLost
    if (opts.listen !== false && host && typeof host.addEventListener === 'function') {
      host.addEventListener('webglcontextlost', listener, true)
      listening = true
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
    timelineRestore,
    reapplyTimeline,
    attach
  }
})
