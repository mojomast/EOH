// Hermes pin layer + info drawer for the God's Eye View panel (runs inside the sandboxed frame).
//
// Why: upstream draws pins as a screen-space SVG overlay anchored at a ground height it samples
// ONCE with scene.clampToHeight and then caches as "settled" for good. Pins are usually dropped
// while the camera is zoomed out over a region ("ramen in Montreal"), so that height comes from a
// coarse tile LOD (or a roof) and is never re-sampled. Zoom in to street level and the reticle is
// projected from that stale height: it hovers above (or off) the real spot. The SVG is also
// centred on the point (rings + dot), with no tip, so it cannot mark an exact spot.
//
// Here pins are Cesium entities instead: a billboard anchored at its BOTTOM (the tip is the spot),
// a small ground dot at the same point, depth test disabled so buildings do not swallow them, and
// the ground height re-sampled whenever the camera's level of detail changes. Upstream keeps the
// annotation (ids, list, clear, referents); only its floating SVG mark is hidden (setHidden).
(() => {
  const g = () => window.__godsEyeView || {}
  const C = () => window.Cesium
  const pins = new Map()       // id -> { id, lat, lon, label, color, meta, h, ents }
  let ds = null, handler = null, offChange = null, timer = null, lastBucket = null
  let drawer = null, openId = null
  const COLORS = { primary: '#38f2c8', amber: '#ffb347', red: '#ff5a5a', green: '#5dff8a', blue: '#5ab4ff',
    yellow: '#ffe45a', purple: '#c08bff', white: '#ffffff', cyan: '#38f2e8', orange: '#ff9b3d' }
  const colorOf = (c) => (typeof c === 'string' && /^#[0-9a-f]{3,8}$/i.test(c) ? c : COLORS[String(c || 'primary').toLowerCase()] || COLORS.primary)
  const pinSvg = (col) => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="30" height="42" viewBox="0 0 30 42">` +
    `<path d="M15 41 C15 41 2 24.5 2 15 A13 13 0 0 1 28 15 C28 24.5 15 41 15 41 Z" fill="${col}" stroke="#06121c" stroke-width="2.2"/>` +
    `<circle cx="15" cy="15" r="5.2" fill="#06121c"/></svg>`)

  // ---- ground height: re-sampled per camera LOD bucket, never "settled" forever ------------
  const groundAt = (lon, lat) => {
    const Ce = C(), v = g().viewer
    if (!Ce || !v) return null
    const s = v.scene, carto = Ce.Cartographic.fromDegrees(lon, lat)
    const exclude = ds ? ds.entities.values : []
    const tilesOn = (() => { try { return Boolean(g().tileset?.show) || !s.globe?.show } catch { return false } })()
    if (tilesOn && s.sampleHeightSupported) {
      try { const h = s.sampleHeight(carto, exclude); if (Number.isFinite(h) && h > -500 && h < 9000) return h } catch {}
    }
    try { const h = s.globe?.show ? s.globe.getHeight(carto) : undefined; if (Number.isFinite(h)) return h } catch {}
    return null
  }
  const camBucket = () => {
    try { return Math.round(Math.log2(Math.max(10, g().viewer.camera.positionCartographic.height))) } catch { return 0 }
  }
  const place = (p) => {
    const Ce = C()
    const h = groundAt(p.lon, p.lat)
    if (h != null) p.h = h
    const pos = Ce.Cartesian3.fromDegrees(p.lon, p.lat, p.h ?? 0)
    for (const e of p.ents) e.position = pos
  }
  const resampleAll = (force) => {
    const b = camBucket()
    if (!force && b === lastBucket) {  // same LOD: only retry pins whose height is still unknown
      for (const p of pins.values()) if (p.h == null) place(p)
      return
    }
    lastBucket = b
    for (const p of pins.values()) place(p)
    try { g().viewer.scene.requestRender?.() } catch {}
  }

  const ensure = () => {
    const Ce = C(), v = g().viewer
    if (!Ce || !v) return false
    if (!ds) {
      ds = new Ce.CustomDataSource('hermes-pins')
      v.dataSources.add(ds)
      handler = new Ce.ScreenSpaceEventHandler(v.scene.canvas)
      handler.setInputAction((ev) => {
        let picked = null
        try { picked = v.scene.drillPick(ev.position, 5) } catch {}
        const hit = (picked || []).map((x) => x?.id?._hermesPin).find(Boolean)
        if (hit) open(hit, { flyTo: true, source: 'click' })
      }, Ce.ScreenSpaceEventType.LEFT_CLICK)
      handler.setInputAction((ev) => {
        let hit = null
        try { hit = v.scene.pick(ev.endPosition)?.id?._hermesPin } catch {}
        v.scene.canvas.style.cursor = hit ? 'pointer' : ''
      }, Ce.ScreenSpaceEventType.MOUSE_MOVE)
      timer = setInterval(() => resampleAll(false), 700)
      v.camera.moveEnd?.addEventListener?.(() => resampleAll(true))
      try {  // keep in step with upstream's board (clear, remove, expiry)
        offChange = g().annotations?.onChange?.(() => syncWithBoard())
      } catch {}
    }
    return true
  }
  const syncWithBoard = () => {
    let list = null
    try { list = g().annotations?.list?.() } catch {}
    if (!Array.isArray(list)) return
    const live = new Set(list.map((a) => a.id))
    for (const id of [...pins.keys()]) if (!live.has(id)) remove(id)
  }

  const add = (id, lat, lon, spec = {}) => {
    if (!ensure() || !id || !Number.isFinite(lat) || !Number.isFinite(lon)) return false
    remove(id)
    const Ce = C(), col = colorOf(spec.color)
    const label = String(spec.label ?? spec.title ?? '').split(/\r?\n/)[0].slice(0, 80)
    const NEVER_DEPTH = Number.POSITIVE_INFINITY  // label names fade out at region zoom; pins stay
    const common = { _hermesPin: id }
    const pin = ds.entities.add({ ...common,
      billboard: { image: pinSvg(col), width: 30, height: 42, verticalOrigin: Ce.VerticalOrigin.BOTTOM,
        horizontalOrigin: Ce.HorizontalOrigin.CENTER, disableDepthTestDistance: NEVER_DEPTH,
        scaleByDistance: new Ce.NearFarScalar(300, 1.0, 2.0e6, 0.55) },
      label: label ? { text: label, font: '600 13px ui-monospace, Menlo, monospace', fillColor: Ce.Color.fromCssColorString('#e8fff6'),
        outlineColor: Ce.Color.fromCssColorString('#020a08'), outlineWidth: 3, style: Ce.LabelStyle.FILL_AND_OUTLINE,
        showBackground: true, backgroundColor: Ce.Color.fromCssColorString('rgba(0,14,10,0.78)'),
        verticalOrigin: Ce.VerticalOrigin.BOTTOM, pixelOffset: new Ce.Cartesian2(0, -46), disableDepthTestDistance: NEVER_DEPTH,
        scaleByDistance: new Ce.NearFarScalar(300, 1.0, 2.0e6, 0.7),
        translucencyByDistance: new Ce.NearFarScalar(1.2e4, 1.0, 6.0e4, 0.0) } : undefined })
    // the exact spot: a small dot ON the ground at the billboard tip
    const dot = ds.entities.add({ ...common,
      point: { pixelSize: 7, color: Ce.Color.fromCssColorString(col), outlineColor: Ce.Color.fromCssColorString('#06121c'),
        outlineWidth: 2, disableDepthTestDistance: NEVER_DEPTH } })
    pin._hermesPin = id; dot._hermesPin = id
    const p = { id, lat, lon, label, color: col, meta: drawerMeta(spec, label), h: null, ents: [pin, dot] }
    pins.set(id, p)
    place(p)
    try { g().annotations?.setHidden?.(id, true) } catch {}  // hide upstream's floating SVG reticle
    try { g().viewer.scene.requestRender?.() } catch {}
    return true
  }
  function remove(id) {
    const p = pins.get(id)
    if (!p) return
    for (const e of p.ents) try { ds.entities.remove(e) } catch {}
    pins.delete(id)
    if (openId === id) close({ silent: true })
  }
  const clear = () => { for (const id of [...pins.keys()]) remove(id); close({ silent: true }) }

  // ---- drawer content: sanitize everything ------------------------------------------------
  const httpUrl = (u) => { try { const x = new URL(String(u || '')); return (x.protocol === 'https:' || x.protocol === 'http:') ? x.href : null } catch { return null } }
  const drawerMeta = (spec, label) => ({
    title: String(spec.title ?? label ?? '').slice(0, 120),
    md: typeof spec.description_md === 'string' ? spec.description_md.slice(0, 4000) : '',
    images: (Array.isArray(spec.images) ? spec.images : []).slice(0, 6)
      .map((i) => (typeof i === 'string' ? { url: i } : i || {}))
      .map((i) => ({ url: httpUrl(i.url), alt: String(i.alt ?? i.caption ?? spec.title ?? label ?? 'image').slice(0, 160), caption: i.caption ? String(i.caption).slice(0, 200) : '' }))
      .filter((i) => i.url),
    links: (Array.isArray(spec.links) ? spec.links : []).slice(0, 12)
      .map((l) => (typeof l === 'string' ? { url: l } : l || {}))
      .map((l) => ({ url: httpUrl(l.url), label: String(l.label ?? l.title ?? '').slice(0, 120) }))
      .filter((l) => l.url)
  })
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
  // Minimal markdown -> HTML. Input is HTML-escaped FIRST, so raw HTML/script shows as text; the
  // only tags emitted are the ones below, and links are http(s) only.
  const inline = (s) => {
    const codes = []
    s = s.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000` })
    s = s.replace(/\[([^\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)/g, (m, text, url) => {
      const u = httpUrl(url.replace(/&amp;/g, '&'))
      return u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer" data-hermes-link="1">${text}</a>` : text
    })
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (m, pre, url) => {
      const u = httpUrl(url.replace(/&amp;/g, '&'))
      return u ? `${pre}<a href="${esc(u)}" target="_blank" rel="noopener noreferrer" data-hermes-link="1">${url}</a>` : m
    })
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*\w])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
      .replace(/(^|[^_\w])_([^_\s][^_]*)_(?!\w)/g, '$1<em>$2</em>')
    s = s.replace(/\\([\\`*_\[\]#|<>])/g, '$1')
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`)
  }
  const renderMarkdown = (md) => {
    const lines = esc(String(md || '')).replace(/\r\n?/g, '\n').split('\n')
    const out = []
    let list = null, para = []
    const flushP = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = [] } }
    const flushL = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`); list = null } }
    for (const raw of lines) {
      const line = raw.trimEnd()
      let m
      if (!line.trim()) { flushP(); flushL(); continue }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flushP(); flushL(); const n = Math.min(6, m[1].length + 2); out.push(`<h${n}>${inline(m[2])}</h${n}>`); continue }
      if ((m = line.match(/^\s*[-*+]\s+(.*)$/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
        flushP()
        const tag = /^\s*\d/.test(line) ? 'ol' : 'ul'
        if (!list || list.tag !== tag) { flushL(); list = { tag, items: [] } }
        list.items.push(m[1]); continue
      }
      if (/^(-{3,}|\*{3,})$/.test(line.trim())) { flushP(); flushL(); out.push('<hr>'); continue }
      flushL(); para.push(line.trim())
    }
    flushP(); flushL()
    return out.join('')
  }

  // ---- links: the frame is sandboxed with an opaque (null) origin and no allow-popups, so a
  // target=_blank click is silently blocked. Ask the host (MCP Apps ui/open-link -> Hermes'
  // openExternal, http(s) only) and fall back to window.open when not embedded.
  let rpc = 9e6
  const openLink = (url) => {
    const u = httpUrl(url)
    if (!u) return false
    if (window.parent && window.parent !== window) {
      try { window.parent.postMessage({ jsonrpc: '2.0', id: ++rpc, method: 'ui/open-link', params: { url: u } }, '*'); return true } catch {}
    }
    try { window.open(u, '_blank', 'noopener,noreferrer'); return true } catch { return false }
  }
  const tellHost = (pin) => { try { window.parent.postMessage({ type: 'hermes-gev:drawer', detail: { pin } }, '*') } catch {} }

  const ensureDrawer = () => {
    if (drawer) return drawer
    const st = document.createElement('style')
    st.textContent = `#hermes-gev-drawer{position:fixed;top:0;right:0;height:100%;width:min(380px,88vw);z-index:2147483647;box-sizing:border-box;
      background:rgba(3,14,11,.95);border-left:1px solid rgba(120,255,200,.45);color:#d8fff0;font:13px/1.5 ui-sans-serif,system-ui,sans-serif;
      transform:translateX(102%);transition:transform .22s ease;display:flex;flex-direction:column;box-shadow:-6px 0 22px rgba(0,0,0,.55)}
      #hermes-gev-drawer.open{transform:none}
      #hermes-gev-drawer header{display:flex;align-items:flex-start;gap:8px;padding:14px 14px 10px;border-bottom:1px solid rgba(120,255,200,.2)}
      #hermes-gev-drawer h2{flex:1;margin:0;font:700 16px/1.3 ui-monospace,Menlo,monospace;color:#8fffd0;word-break:break-word}
      #hermes-gev-drawer .x{background:none;border:1px solid rgba(120,255,200,.45);color:#8fffd0;border-radius:4px;width:28px;height:28px;cursor:pointer;font:16px/1 sans-serif}
      #hermes-gev-drawer .body{overflow:auto;padding:12px 14px 20px}
      #hermes-gev-drawer .coords{font:11px ui-monospace,Menlo,monospace;color:#7fbfa8;margin:0 0 8px}
      #hermes-gev-drawer a{color:#7fd8ff}
      #hermes-gev-drawer code{background:rgba(120,255,200,.1);padding:0 4px;border-radius:3px;font-size:12px}
      #hermes-gev-drawer h3,#hermes-gev-drawer h4,#hermes-gev-drawer h5,#hermes-gev-drawer h6{margin:12px 0 4px;color:#b9ffe4}
      #hermes-gev-drawer p{margin:0 0 8px} #hermes-gev-drawer ul,#hermes-gev-drawer ol{margin:0 0 8px;padding-left:20px}
      #hermes-gev-drawer figure{margin:0 0 10px} #hermes-gev-drawer img{display:block;max-width:100%;border-radius:4px;border:1px solid rgba(120,255,200,.25);background:#06121c;min-height:40px}
      #hermes-gev-drawer figcaption{font-size:11px;color:#9fd9c3;margin-top:3px}
      #hermes-gev-drawer .broken{padding:10px;border:1px dashed rgba(255,200,120,.5);border-radius:4px;color:#ffd27a;font-size:12px}
      #hermes-gev-drawer .links{list-style:none;padding:0;margin:10px 0 0;display:flex;flex-direction:column;gap:6px}
      #hermes-gev-drawer .links a{display:block;padding:6px 9px;border:1px solid rgba(127,216,255,.35);border-radius:4px;text-decoration:none;word-break:break-all}`
    document.head.appendChild(st)
    drawer = document.createElement('aside')
    drawer.id = 'hermes-gev-drawer'
    drawer.setAttribute('role', 'dialog')
    drawer.setAttribute('aria-hidden', 'true')
    drawer.innerHTML = '<header><h2></h2><button class="x" type="button" aria-label="Close" title="Close (Esc)">✕</button></header><div class="body"></div>'
    drawer.querySelector('.x').addEventListener('click', () => close())
    drawer.addEventListener('click', (e) => {
      const a = e.target?.closest?.('a[href]')
      if (!a) return
      e.preventDefault()
      openLink(a.getAttribute('href'))
    })
    const onKey = (e) => { if (e.key === 'Escape' && openId) { e.stopPropagation(); close() } }
    document.addEventListener('keydown', onKey, true)
    document.body.appendChild(drawer)
    return drawer
  }
  const fillDrawer = (p) => {
    const d = ensureDrawer(), m = p.meta
    d.querySelector('h2').textContent = m.title || p.label || 'Pin'
    const body = d.querySelector('.body')
    body.textContent = ''
    const co = document.createElement('p'); co.className = 'coords'
    co.textContent = `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`
    body.appendChild(co)
    for (const im of m.images) {
      const fig = document.createElement('figure')
      const img = document.createElement('img')
      img.loading = 'lazy'; img.decoding = 'async'; img.alt = im.alt; img.referrerPolicy = 'no-referrer'
      img.addEventListener('error', () => {
        const b = document.createElement('div'); b.className = 'broken'
        b.textContent = `Image unavailable: ${im.alt}`
        img.replaceWith(b)
      }, { once: true })
      img.src = im.url
      fig.appendChild(img)
      if (im.caption) { const fc = document.createElement('figcaption'); fc.textContent = im.caption; fig.appendChild(fc) }
      body.appendChild(fig)
    }
    if (m.md) { const md = document.createElement('div'); md.className = 'md'; md.innerHTML = renderMarkdown(m.md); body.appendChild(md) }
    if (!m.md && !m.images.length && !m.links.length) { const e = document.createElement('p'); e.textContent = 'No details for this pin.'; e.style.opacity = '.7'; body.appendChild(e) }
    if (m.links.length) {
      const ul = document.createElement('ul'); ul.className = 'links'
      for (const l of m.links) {
        const li = document.createElement('li'), a = document.createElement('a')
        a.href = l.url; a.target = '_blank'; a.rel = 'noopener noreferrer'
        a.textContent = l.label || l.url.replace(/^https?:\/\//, '')
        li.appendChild(a); ul.appendChild(li)
      }
      body.appendChild(ul)
    }
  }
  const pinInfo = (p) => p && { id: p.id, label: p.label || p.meta.title, latitude: p.lat, longitude: p.lon }
  const find = (q) => {
    if (!q) return null
    if (q.id && pins.has(String(q.id))) return pins.get(String(q.id))
    const all = [...pins.values()]
    if (Number.isFinite(q.latitude) && Number.isFinite(q.longitude)) {
      let best = null, bd = Infinity
      for (const p of all) { const d = Math.hypot(p.lat - q.latitude, (p.lon - q.longitude) * Math.cos(p.lat * Math.PI / 180)); if (d < bd) { bd = d; best = p } }
      if (best && bd < 0.002) return best  // ~200 m
    }
    if (q.label) {
      const l = String(q.label).toLowerCase()
      return all.find((p) => (p.label || '').toLowerCase() === l || (p.meta.title || '').toLowerCase() === l) ||
        all.find((p) => (p.label || p.meta.title || '').toLowerCase().includes(l)) || null
    }
    return null
  }
  function open(idOrQuery, opts = {}) {
    const p = typeof idOrQuery === 'string' ? pins.get(idOrQuery) : find(idOrQuery)
    if (!p) return { ok: false, open: Boolean(openId), error: 'pin not found', pins: [...pins.values()].slice(0, 30).map(pinInfo) }
    fillDrawer(p)
    openId = p.id
    drawer.classList.add('open'); drawer.setAttribute('aria-hidden', 'false')
    if (opts.source === 'click') try { drawer.querySelector('.x').focus({ preventScroll: true }) } catch {}  // Esc works right away
    if (opts.flyTo) {
      try {
        const Ce = C(), v = g().viewer
        const cur = v.camera.positionCartographic.height
        const range = Math.max(250, Math.min(cur, 1500))
        v.camera.flyToBoundingSphere(new Ce.BoundingSphere(Ce.Cartesian3.fromDegrees(p.lon, p.lat, p.h ?? 0), 1),
          { offset: new Ce.HeadingPitchRange(v.camera.heading, Ce.Math.toRadians(-35), range), duration: 1.2 })
      } catch {}
    }
    if (opts.source === 'click') tellHost(pinInfo(p))
    return { ok: true, open: true, pin: pinInfo(p), flew: Boolean(opts.flyTo) }
  }
  function close(opts = {}) {
    const was = openId
    openId = null
    if (drawer) { drawer.classList.remove('open'); drawer.setAttribute('aria-hidden', 'true') }
    if (was && !opts.silent && !opts.fromAgent) tellHost(null)
    return { ok: true, open: false, closed: was }
  }
  const command = (args = {}) => {
    const action = String(args.action || 'open')
    if (action === 'close') return { action: 'hermes_pin_drawer', ...close({ fromAgent: true }) }
    if (action === 'state') return { ok: true, action: 'hermes_pin_drawer', open: Boolean(openId), pin: pinInfo(pins.get(openId)), pins: [...pins.values()].slice(0, 50).map(pinInfo) }
    return { action: 'hermes_pin_drawer', ...open({ id: args.id, label: args.label, latitude: args.latitude, longitude: args.longitude }, { flyTo: Boolean(args.flyTo) }) }
  }
  // Ground point a pin (or any mark) should sit on, for other Hermes overlays (cards).
  const heightFor = (lat, lon) => groundAt(lon, lat)
  window.__hermesPins = { add, remove, clear, open, close, command, renderMarkdown, heightFor, syncWithBoard,
    state: () => ({ open: openId, pins: [...pins.values()].map((p) => ({ ...pinInfo(p), h: p.h })) }) }
})()
