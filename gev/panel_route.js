/* Route dolly and Street View hops for the Eye Of Hermes pane.
   CCTV autohop is the same idea: enable the layer, open the nearest view,
   dwell, then the next one. Here the server owns the dwell and sends one hop.
   This file only moves the viewer and keeps the main thread free to poll. */
(() => {
  const CHUNK = 300
  const STEP_M = 40
  const yieldNow = () => new Promise((resolve) => setTimeout(resolve, 0))

  const num = (value) => {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }

  function asPoint(raw) {
    if (!raw) return null
    if (Array.isArray(raw) && raw.length >= 2) {
      const lat = num(raw[0]); const lon = num(raw[1])
      return lat == null || lon == null ? null : { lat, lon }
    }
    const lat = num(raw.lat ?? raw.latitude)
    const lon = num(raw.lon ?? raw.lng ?? raw.longitude)
    return lat == null || lon == null ? null : { lat, lon }
  }

  function asPoints(raw) {
    const out = []
    for (const item of raw || []) {
      if (item && Array.isArray(item.points)) out.push(...asPoints(item.points))
      else {
        const point = asPoint(item)
        if (point) out.push(point)
      }
    }
    return out
  }

  function metres(a, b) {
    const R = 6371000
    const p1 = a.lat * Math.PI / 180
    const p2 = b.lat * Math.PI / 180
    const dphi = (b.lat - a.lat) * Math.PI / 180
    const dl = (b.lon - a.lon) * Math.PI / 180
    const h = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
  }

  function thin(points, step) {
    if (points.length < 3) return points.slice()
    let sum = 0
    for (let i = 1; i < points.length; i++) sum += metres(points[i - 1], points[i])
    if (sum / (points.length - 1) >= step * 0.75) return points.slice()
    const out = [points[0]]
    let since = 0
    for (let i = 1; i < points.length; i++) {
      since += metres(points[i - 1], points[i])
      if (since >= step || i === points.length - 1) {
        out.push(points[i])
        since = 0
      }
    }
    return out
  }

  function chunked(points, size) {
    const cap = Math.max(2, size)
    if (points.length <= cap) return points.length ? [points] : []
    const out = []
    let index = 0
    while (index < points.length) {
      const end = Math.min(points.length, index + cap)
      out.push(points.slice(index, end))
      if (end >= points.length || end <= index + 1) break
      index = end - 1
    }
    return out
  }

  function routeSpec(label) {
    const g = window.__godsEyeView || {}
    let items = []
    try {
      const board = g.annotations
      items = board?.list?.() || board?.items || []
    } catch { items = [] }
    const routes = []
    for (const item of items) {
      const spec = item?.spec && item.spec.type ? item.spec : item
      if (String(spec?.type || '').toLowerCase() === 'route') routes.push(spec)
    }
    if (!routes.length) return null
    if (label) {
      const want = String(label).toLowerCase()
      const hit = [...routes].reverse().find((spec) => String(spec.label || '').toLowerCase().includes(want))
      if (hit) return hit
    }
    return routes[routes.length - 1]
  }

  let saved = null

  function light(on) {
    const viewer = window.__godsEyeView?.viewer
    const scene = viewer?.scene
    const globe = scene?.globe
    if (on) {
      if (!saved) {
        saved = {
          sse: globe?.maximumScreenSpaceError,
          ancestors: globe?.preloadAncestors,
          siblings: globe?.preloadSiblings,
          render: scene?.requestRenderMode,
          tiles: []
        }
        const prims = scene?.primitives
        const count = prims && typeof prims.length === 'number' ? prims.length : 0
        for (let i = 0; i < count; i++) {
          const tile = typeof prims.get === 'function' ? prims.get(i) : prims[i]
          if (tile && typeof tile.maximumScreenSpaceError === 'number') {
            saved.tiles.push([tile, tile.maximumScreenSpaceError])
          }
        }
      }
      if (globe) {
        globe.maximumScreenSpaceError = Math.max(Number(globe.maximumScreenSpaceError) || 2, 16)
        globe.preloadAncestors = false
        globe.preloadSiblings = false
      }
      if (scene) scene.requestRenderMode = true
      for (const [tile, sse] of saved.tiles) tile.maximumScreenSpaceError = Math.max(sse, 16)
      const apply = window.__godsEyeView?.applyHostConfig
      if (typeof apply === 'function') {
        try { apply({ perf: 'embed' }) } catch { /* older builds ignore perf */ }
      }
      return { ok: true, on: true, coarsened: Boolean(globe) }
    }
    if (globe && saved) {
      if (typeof saved.sse === 'number') globe.maximumScreenSpaceError = saved.sse
      if (typeof saved.ancestors === 'boolean') globe.preloadAncestors = saved.ancestors
      if (typeof saved.siblings === 'boolean') globe.preloadSiblings = saved.siblings
    }
    if (scene && saved && typeof saved.render === 'boolean') scene.requestRenderMode = saved.render
    if (saved) for (const [tile, sse] of saved.tiles) tile.maximumScreenSpaceError = sse
    saved = null
    return { ok: true, on: false }
  }

  async function flyPoints(points) {
    const cam = window.__godsEyeView?.viewer?.camera
    const C3 = cam?.positionWC?.constructor
    let yielded = 0
    for (let i = 0; i < points.length; i++) {
      if (cam && typeof C3?.fromDegrees === 'function') {
        try {
          cam.cancelFlight?.()
          cam.setView?.({ destination: C3.fromDegrees(points[i].lon, points[i].lat, 80) })
        } catch { /* a missing camera still yields */ }
      }
      if (i % 8 === 7) {
        await yieldNow()
        yielded += 1
      }
    }
    await yieldNow()
    return { ok: true, points: points.length, yielded: yielded + 1 }
  }

  async function flyRoute(args) {
    const spec = routeSpec(args?.label)
    const raw = asPoints(args?.points || spec?.points)
    const dense = raw.length > 80
    if (!dense) {
      const run = window.__godsEyeView?.voiceCommands?.runner
      if (typeof run === 'function' && !args?.points) {
        const result = await run('fly_route', { label: args?.label, speed: args?.speed })
        return { ok: result?.ok !== false, result, local: false, pointsIn: raw.length, pointsOut: raw.length }
      }
    }
    const thinned = dense ? thin(raw, STEP_M) : raw
    const parts = chunked(thinned, CHUNK)
    let yielded = 0
    for (const part of parts) {
      const step = await flyPoints(part)
      yielded += step.yielded || 0
      await yieldNow()
      yielded += 1
    }
    return {
      ok: thinned.length >= 2, action: 'fly_route', local: true,
      pointsIn: raw.length, pointsOut: thinned.length, chunks: parts.length, yielded
    }
  }

  async function streetHop(args) {
    const sl = window.__godsEyeView?.streetLevel
    const heading = num(args?.heading)
    const body = {
      lat: args?.lat, lon: args?.lon, heading,
      radiusM: args?.radiusM || 50,
      provider: args?.provider, panoId: args?.panoId
    }
    let opened = null
    if (sl && typeof sl.openNearest === 'function') {
      opened = await sl.openNearest(body)
    } else if (sl && typeof sl.show === 'function') {
      opened = await sl.show(body)
    }
    if (sl && typeof sl.setHeading === 'function' && heading != null) {
      try { sl.setHeading(heading) } catch { /* heading is optional */ }
    }
    const run = window.__godsEyeView?.voiceCommands?.runner
    if (typeof run === 'function') {
      try {
        await run('show_street_level', {
          latitude: args?.lat, longitude: args?.lon, provider: args?.provider,
          openNearest: true, radiusM: body.radiusM, heading
        })
        await run('set_layer_visibility', { layerId: 'street-level', enabled: true })
      } catch { /* the embedded viewer already moved when openNearest exists */ }
    }
    const pano = (opened && opened.panoId) || args?.panoId || null
    return {
      ok: true, panoId: pano, provider: (opened && opened.provider) || args?.provider || null,
      heading, fallback: args?.fallback || null
    }
  }

  async function command(name, args) {
    if (name === 'hermes_route_light') return light(args?.on !== false)
    if (name === 'hermes_fly_chunk') {
      const points = asPoints(args?.points)
      const step = await flyPoints(points)
      return { ok: true, action: 'hermes_fly_chunk', points: points.length, index: args?.index, chunks: args?.chunks, yielded: step.yielded }
    }
    if (name === 'hermes_street_hop') return streetHop(args || {})
    if (name === 'fly_route') return flyRoute(args || {})
    return { ok: false, error: 'unknown route command' }
  }

  window.__hermesRoute = { command, light }
})()
