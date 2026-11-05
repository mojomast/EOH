// Eye Of Hermes time slider (runs inside the sandboxed frame).
//
// wayback: scrub Esri World Imagery Wayback releases over the current view, with a swipe
// or a fade between the two dates the playhead sits between. The layers are for viewing.
// Photorealistic 3D tiles are hidden while that imagery is up, same as the compare swipe,
// and are not sampled.
// events: USGS quakes and NASA FIRMS fires (plus stored watch alerts) as pins that appear
// when the playhead reaches their time. Clicking one opens the same drawer as any pin.
(() => {
  const g = () => window.__godsEyeView || {}
  const C = () => window.Cesium
  const TITLE = 'Eye Of Hermes (EOH)'
  let bar = null
  let mode = null
  let from = ''
  let to = ''
  let at = ''
  let playing = false
  let transition = 'swipe'
  let releases = []
  let events = []
  let index = 0
  let frac = 0
  let layers = []
  let hiddenTiles = null
  let timer = null
  let postTimer = null

  const day = (v) => String(v || '').slice(0, 10)
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n))

  const snapshot = () => ({
    mode, from, to, at, playing, transition, index,
    releases: releases.length, events: events.length
  })

  const visible = () => {
    if (mode === 'wayback') {
      const row = releases[index] || null
      const nxt = releases[index + 1] || null
      return {
        date: row?.date || at || null,
        release: row?.release ?? null,
        label: row?.label || null,
        next: nxt ? { date: nxt.date, release: nxt.release } : null,
        blend: Math.round(frac * 1000) / 1000
      }
    }
    const shown = events.filter((e) => day(e.time) >= from && day(e.time) <= (at || to))
    return {
      count: shown.length,
      events: shown.slice(0, 12).map((e) => ({
        id: e.id, feed: e.feed, title: e.title, lat: e.lat, lon: e.lon, time: e.time
      }))
    }
  }

  const reply = (extra = {}) => ({
    ok: true, action: 'hermes_timeline', active: Boolean(mode),
    timeline: mode ? { mode, from, to, at, playing, transition, index } : null,
    visible: mode ? visible() : null, ...extra
  })

  const tell = () => {
    if (!mode) return
    try {
      window.parent.postMessage({ type: 'hermes-gev:timeline', detail: { timeline: {
        mode, from, to, at, playing, transition, index
      } } }, '*')
    } catch {}
  }
  const tellSoon = () => {
    if (postTimer) clearTimeout(postTimer)
    postTimer = setTimeout(() => { postTimer = null; tell() }, 400)
  }

  function dropLayers() {
    const v = g().viewer
    for (const item of layers) {
      try { v?.imageryLayers?.remove(item.layer, true) } catch {}
    }
    layers = []
    try { if (v?.scene) v.scene.splitPosition = 0.5 } catch {}
  }
  function hideTiles(on) {
    const tiles = g().tileset
    if (!tiles) return
    if (on) {
      if (hiddenTiles == null && tiles.show) { hiddenTiles = true; tiles.show = false }
    } else if (hiddenTiles) {
      try { tiles.show = true } catch {}
      hiddenTiles = null
    }
  }
  function dropPins() {
    const P = window.__hermesPins
    if (!P?.state) return
    try {
      for (const p of P.state().pins || []) if (String(p.id).startsWith('tl:')) P.remove(p.id)
    } catch {}
  }
  function syncPins() {
    const P = window.__hermesPins
    if (!P?.add) return []
    if (mode !== 'events') { dropPins(); return [] }
    const cursor = at || from
    const keep = new Set()
    const shown = []
    for (const ev of events) {
      const d = day(ev.time)
      if (!d || d < from || d > cursor) continue
      keep.add(String(ev.id))
      shown.push(ev)
      try {
        P.add('tl:' + ev.id, Number(ev.lat), Number(ev.lon), {
          timeline: true,
          label: ev.title, title: ev.title,
          description_md: ev.description_md || '',
          links: ev.links || [],
          color: ev.color || (ev.feed === 'fires' ? 'red' : 'amber'),
          tags: { watch: 'timeline', category: ev.feed || 'earthquakes' }
        })
      } catch {}
    }
    try {
      for (const p of P.state().pins || []) {
        if (String(p.id).startsWith('tl:') && !keep.has(String(p.id).slice(3))) P.remove(p.id)
      }
    } catch {}
    return shown
  }

  function layerFor(rel) {
    const Ce = C(), v = g().viewer
    if (!Ce || !v || !rel?.url) return null
    const hit = layers.find((item) => item.release === rel.release)
    if (hit) return hit.layer
    const prov = new Ce.UrlTemplateImageryProvider({ url: rel.url, maximumLevel: 19, credit: 'Esri World Imagery Wayback' })
    const layer = v.imageryLayers.addImageryProvider(prov)
    layers.push({ release: rel.release, layer })
    while (layers.length > 2) {
      const old = layers.shift()
      try { v.imageryLayers.remove(old.layer, true) } catch {}
    }
    return layer
  }
  function paintImagery() {
    if (mode !== 'wayback') { dropLayers(); hideTiles(false); return }
    const Ce = C()
    if (!Ce || !g().viewer) return
    hideTiles(true)
    try { window.__hermesContext?.compare?.({ action: 'stop' }) } catch {}
    const older = releases[index]
    const newer = frac > 0 ? releases[Math.min(index + 1, releases.length - 1)] : older
    if (!older) { dropLayers(); return }
    const a = layerFor(older)
    const b = newer && newer.release !== older.release ? layerFor(newer) : null
    const left = Ce.SplitDirection?.LEFT ?? -1
    const right = Ce.SplitDirection?.RIGHT ?? 1
    const none = Ce.SplitDirection?.NONE ?? 0
    for (const item of layers) {
      const layer = item.layer
      const on = item.release === older.release || (b && item.release === newer.release)
      try { layer.show = on } catch {}
      if (!on) continue
      if (transition === 'fade' || !b) {
        try { layer.splitDirection = none; layer.alpha = item.release === older.release ? 1 : frac } catch {}
      } else if (item.release === older.release) {
        try { layer.splitDirection = left; layer.alpha = 1 } catch {}
      } else {
        try { layer.splitDirection = right; layer.alpha = 1 } catch {}
      }
    }
    try { g().viewer.scene.splitPosition = transition === 'swipe' && b ? 1 - frac : 0.5 } catch {}
    try { g().viewer.scene.requestRender?.() } catch {}
  }

  function applyHead() {
    if (mode === 'wayback' && releases.length) {
      index = clamp(index, 0, releases.length - 1)
      at = releases[index].date
    }
    paintImagery()
    syncPins()
    render()
  }

  function stepBy(delta, fromPlay) {
    if (!mode || !delta) return reply()
    if (!fromPlay) {
      playing = false
      stopTimer()
    }
    if (mode === 'wayback') {
      if (delta > 0 && frac > 0.001 && index < releases.length - 1) {
        index += 1
        frac = 0
      } else {
        index = clamp(index + delta, 0, Math.max(0, releases.length - 1))
        frac = 0
      }
      at = releases[index]?.date || at
    } else {
      const days = []
      for (const ev of events) {
        const d = day(ev.time)
        if (d && !days.includes(d)) days.push(d)
      }
      if (delta > 0) {
        const later = days.filter((d) => d > at)
        at = later.length ? later[Math.min(delta, later.length) - 1] : (to || at)
      } else {
        const earlier = days.filter((d) => d < at)
        const slot = earlier.length + delta
        at = earlier.length && slot >= 0 ? earlier[slot] : (from || at)
      }
      index = 0
      frac = 0
    }
    applyHead()
    tellSoon()
    return reply()
  }

  function seek(t) {
    if (mode !== 'wayback' || releases.length < 2) {
      frac = 0
      applyHead()
      return
    }
    const span = Math.max(1, releases.length - 1)
    const x = clamp(Number(t) || 0, 0, 1) * span
    index = clamp(Math.floor(x + 1e-6), 0, releases.length - 1)
    frac = index >= releases.length - 1 ? 0 : x - index
    at = releases[index].date
    applyHead()
  }
  function seekEvents(t) {
    if (!from || !to) return
    const a = Date.parse(from + 'T00:00:00Z')
    const b = Date.parse(to + 'T00:00:00Z')
    if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) { at = from; applyHead(); return }
    const ms = a + clamp(Number(t) || 0, 0, 1) * (b - a)
    at = new Date(ms).toISOString().slice(0, 10)
    applyHead()
  }

  function stopTimer() {
    if (timer) { clearInterval(timer); timer = null }
  }
  function play() {
    if (!mode) return reply({ ok: false, error: 'no timeline' })
    playing = true
    stopTimer()
    render()
    timer = setInterval(() => {
      if (!playing) return stopTimer()
      const before = at + ':' + index + ':' + frac
      if (mode === 'wayback' && index >= releases.length - 1) { playing = false; stopTimer(); render(); tell(); return }
      if (mode === 'events' && at >= to) { playing = false; stopTimer(); render(); tell(); return }
      stepBy(1, true)
      playing = true
      if (at + ':' + index + ':' + frac === before) { playing = false; stopTimer(); render(); tell() }
    }, 900)
    tellSoon()
    return reply()
  }
  function pause() {
    playing = false
    stopTimer()
    render()
    tell()
    return reply()
  }

  function stopAll() {
    pause()
    dropLayers()
    hideTiles(false)
    dropPins()
    mode = null
    if (bar) bar.style.display = 'none'
    try { window.parent.postMessage({ type: 'hermes-gev:timeline', detail: { timeline: { clear: true } } }, '*') } catch {}
    return { ok: true, action: 'hermes_timeline', active: false, timeline: null, visible: null }
  }

  function render() {
    if (!bar) return
    bar.style.display = mode ? 'flex' : 'none'
    const when = bar.querySelector('.when')
    const count = bar.querySelector('.count')
    const playBtn = bar.querySelector('[data-a="play"]')
    const range = bar.querySelector('input')
    const modeEl = bar.querySelector('.mode')
    if (modeEl) modeEl.textContent = mode === 'events' ? 'EVENTS' : 'WAYBACK'
    if (playBtn) playBtn.textContent = playing ? 'Pause' : 'Play'
    if (when) when.textContent = at || '—'
    if (count) {
      count.textContent = mode === 'wayback'
        ? `${releases.length} release${releases.length === 1 ? '' : 's'}`
        : `${(visible().count) || 0} shown`
    }
    if (range && mode === 'wayback' && releases.length > 1) {
      const span = releases.length - 1
      range.value = String((index + frac) / span)
    } else if (range && mode === 'events' && from && to) {
      const a = Date.parse(from + 'T00:00:00Z'), b = Date.parse(to + 'T00:00:00Z')
      const c = Date.parse((at || from) + 'T00:00:00Z')
      range.value = String(b > a && Number.isFinite(c) ? (c - a) / (b - a) : 0)
    } else if (range) range.value = '0'
  }

  function mount() {
    if (bar || !document.body) return
    bar = document.createElement('div')
    bar.id = 'hermes-gev-timeline'
    bar.setAttribute('role', 'group')
    bar.setAttribute('aria-label', TITLE + ' timeline')
    bar.title = TITLE
    bar.style.cssText = 'position:fixed;left:50%;bottom:16px;transform:translateX(-50%);z-index:2147483646;display:none;gap:6px;align-items:center;max-width:min(720px,96vw);padding:6px 8px;background:rgba(3,14,11,.92);color:#ffe9a0;border:1px solid rgba(255,228,90,.7);border-radius:6px;font:600 12px ui-monospace,Menlo,monospace;box-shadow:0 0 0 1px rgba(0,0,0,.4)'
    const btn = 'background:rgba(90,70,0,.35);color:#ffe9a0;border:1px solid rgba(255,228,90,.55);border-radius:4px;padding:4px 7px;cursor:pointer;font:inherit'
    bar.innerHTML = `<span class="eoh" title="${TITLE}" style="color:#ffe45a;letter-spacing:.04em">EOH</span>` +
      `<span class="brand" style="color:#fffbe0">Eye Of Hermes</span>` +
      `<span class="mode" style="opacity:.8">WAYBACK</span>` +
      `<button type="button" data-a="back" title="Step back" style="${btn}">◀</button>` +
      `<button type="button" data-a="play" title="Play or pause" style="${btn}">Play</button>` +
      `<button type="button" data-a="fwd" title="Step forward" style="${btn}">▶</button>` +
      `<input type="range" min="0" max="1" step="0.001" value="0" aria-label="Time" style="width:160px;accent-color:#ffe45a">` +
      `<span class="when" style="min-width:7.2em;color:#fff">—</span>` +
      `<span class="count" style="opacity:.75"></span>`
    bar.addEventListener('click', (event) => {
      const b = event.target?.closest?.('button')
      if (!b) return
      if (b.dataset.a === 'play') return playing ? pause() : play()
      if (b.dataset.a === 'back') return stepBy(-1)
      if (b.dataset.a === 'fwd') return stepBy(1)
    })
    const range = bar.querySelector('input')
    range?.addEventListener?.('input', () => {
      playing = false
      stopTimer()
      if (mode === 'events') seekEvents(range.value)
      else seek(range.value)
      render()
      tellSoon()
    })
    document.body.appendChild(bar)
  }

  function setState(args) {
    mode = args.mode === 'events' ? 'events' : 'wayback'
    transition = args.transition === 'fade' ? 'fade' : 'swipe'
    from = day(args.from)
    to = day(args.to)
    releases = Array.isArray(args.releases) ? args.releases.filter((r) => r && r.url && r.date) : []
    events = Array.isArray(args.events) ? args.events.filter((e) => e && e.time != null && e.lat != null) : []
    playing = false
    stopTimer()
    if (mode === 'wayback') {
      const want = day(args.at || args.date) || from
      index = 0
      releases.forEach((r, i) => { if (r.date <= want) index = i })
      frac = 0
      at = releases[index]?.date || want || from
      if (!from) from = releases[0]?.date || at
      if (!to) to = releases[releases.length - 1]?.date || at
    } else {
      at = day(args.at || args.date) || from
      if (at < from) at = from
      if (to && at > to) at = to
      index = 0
      frac = 0
    }
    mount()
    applyHead()
    tellSoon()
    return reply()
  }

  const command = (args = {}) => {
    const action = String(args.action || 'state')
    if (action === 'state') return reply()
    if (action === 'stop') return pause()
    if (action === 'clear') return stopAll()
    if (action === 'play') {
      if (!mode || args.releases || args.events || args.mode) setState(args)
      return play()
    }
    if (action === 'step') {
      const n = Number(args.by ?? 1)
      return stepBy(Number.isFinite(n) ? n : 1)
    }
    if (action === 'position') {
      playing = false
      stopTimer()
      if (mode === 'events') seekEvents(args.position)
      else seek(args.position)
      tellSoon()
      return reply()
    }
    if (!args.mode && !args.releases && !args.events && mode) {
      if (args.at || args.date) {
        if (mode === 'wayback') {
          const want = day(args.at || args.date)
          index = 0
          releases.forEach((r, i) => { if (r.date <= want) index = i })
          frac = 0
          at = releases[index]?.date || want
        } else at = day(args.at || args.date)
        playing = Boolean(args.playing)
        applyHead()
        tellSoon()
        return reply()
      }
    }
    return setState(args)
  }

  const detach = () => {
    stopTimer()
    layers = []
    hiddenTiles = null
  }

  const boot = setInterval(() => { if (g().viewer && document.body) mount() }, 1000)
  window.__hermesTimeline = { command, detach, state: snapshot, visible, _stop: () => clearInterval(boot) }
})()
