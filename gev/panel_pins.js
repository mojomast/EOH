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
        const cl = (picked || []).map((x) => x?.id?._hermesCluster).find(Boolean)
        if (cl) return expandCluster(cl)
        const hit = (picked || []).map((x) => x?.id?._hermesPin).find(Boolean)
        if (hit) open(hit, { flyTo: true, source: 'click' })
      }, Ce.ScreenSpaceEventType.LEFT_CLICK)
      handler.setInputAction((ev) => {
        let hit = null
        try { const id = v.scene.pick(ev.endPosition)?.id; hit = id?._hermesPin || id?._hermesCluster } catch {}
        v.scene.canvas.style.cursor = hit ? 'pointer' : ''
      }, Ce.ScreenSpaceEventType.MOUSE_MOVE)
      timer = setInterval(() => { resampleAll(false); refreshView(false) }, 700)
      v.camera.moveEnd?.addEventListener?.(() => { resampleAll(true); refreshView(true) })
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
    for (const id of [...pins.keys()]) if (!pins.get(id)?.timeline && !live.has(id)) remove(id)
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
    const p = { id, lat, lon, label, color: col, meta: drawerMeta(spec, label), tags: pinTags(spec),
      timeline: spec.timeline === true, h: null, ents: [pin, dot] }
    pins.set(id, p)
    place(p)
    stackLabels()
    refreshView(true)
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
    stackLabels()
    refreshView(true)
  }
  // Two labels on the same spot used to draw on top of each other. Stack them upward and hide the ones past the fourth.
  function labelOffsets(rows) {
    const groups = []
    for (const row of rows || []) {
      let group = null
      for (const existing of groups) {
        const origin = existing[0]
        const dlat = (row.lat - origin.lat) * 111320
        const dlon = (row.lon - origin.lon) * 111320 * Math.cos((origin.lat * Math.PI) / 180)
        if (Math.hypot(dlat, dlon) <= 30) { group = existing; break }
      }
      if (!group) { group = []; groups.push(group) }
      group.push(row)
    }
    const out = {}
    for (const group of groups) {
      group.sort((a, b) => String(a.id).localeCompare(String(b.id)))
      group.forEach((row, index) => {
        out[row.id] = { y: -46 - index * 22, show: index < 4 }
      })
    }
    return out
  }
  function stackLabels() {
    const Ce = C()
    if (!Ce) return
    const rows = [...pins.values()].filter((p) => p.label).map((p) => ({ id: p.id, lat: p.lat, lon: p.lon }))
    const layout = labelOffsets(rows)
    for (const p of pins.values()) {
      const label = p.ents && p.ents[0] && p.ents[0].label
      const slot = layout[p.id]
      if (!label || !slot) continue
      try {
        label.pixelOffset = new Ce.Cartesian2(0, slot.y)
        label.show = slot.show
      } catch {}
    }
  }
  const clear = (opts) => {
    const dropTimeline = Boolean(opts && opts.timeline)
    for (const id of [...pins.keys()]) {
      if (!dropTimeline && pins.get(id)?.timeline) continue
      remove(id)
    }
  }
  // Drop the data source bound to a destroyed viewer so the next add() uses the new one.
  const detach = () => {
    try { handler?.destroy?.() } catch {}
    handler = null
    try { if (typeof offChange === 'function') offChange() } catch {}
    offChange = null
    if (timer) { clearInterval(timer); timer = null }
    ds = null
    lastBucket = null
  }

  // ---- drawer content: sanitize everything ------------------------------------------------
  const httpUrl = (u) => { try { const x = new URL(String(u || '')); return (x.protocol === 'https:' || x.protocol === 'http:') ? x.href : null } catch { return null } }
  const YT_ID = /(?:youtube\.com\/(?:watch\?(?:[^#]*&)?v=|embed\/|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{6,16})/i
  const X_ID = /(?:twitter\.com|x\.com)\/(?:[^/?#]+\/status|i\/status)\/(\d+)/i
  // Rebuild the embed from the public URL. A stored embed host is never trusted.
  const clipOf = (item) => {
    const raw = typeof item === 'string' ? item : item && item.url
    const url = httpUrl(raw)
    if (!url || !url.startsWith('https:')) return null
    const yt = url.match(YT_ID)
    const status = url.match(X_ID)
    if (!yt && !status) return null
    const title = String((item && item.title) || (yt ? 'YouTube' : 'X')).slice(0, 120)
    if (yt) {
      const given = item && item.start_sec
      const start = Number.isInteger(given) && given > 0 && given <= 86400 ? given : null
      return {
        provider: 'youtube', url: `https://www.youtube.com/watch?v=${yt[1]}`,
        embed: `https://www.youtube-nocookie.com/embed/${yt[1]}${start ? `?start=${start}` : ''}`,
        title, ...(start ? { start_sec: start } : {}),
      }
    }
    return {
      provider: 'x', url: `https://x.com/i/status/${status[1]}`,
      embed: `https://platform.twitter.com/embed/Tweet.html?id=${status[1]}`, title,
    }
  }
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
      .filter((l) => l.url),
    clips: (Array.isArray(spec.clips) ? spec.clips : []).slice(0, 4).map(clipOf).filter(Boolean),
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
      #hermes-gev-drawer .links a{display:block;padding:6px 9px;border:1px solid rgba(127,216,255,.35);border-radius:4px;text-decoration:none;word-break:break-all}
      #hermes-gev-drawer .clip{margin:0 0 12px}
      #hermes-gev-drawer .clip iframe{display:block;width:100%;aspect-ratio:16/9;border:0;border-radius:4px;background:#06121c}
      #hermes-gev-drawer .clip a{display:inline-block;margin-top:4px}`
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
    if (p.tags.hours || p.tags.rating != null) {
      const st = document.createElement('p'); st.className = 'coords'
      const o = p.tags.hours ? openState(p) : undefined
      st.textContent = [p.tags.rating != null ? `Rating ${p.tags.rating}` : '', o === true ? 'Open now' : o === false ? 'Closed now' : p.tags.hours ? 'Hours unknown' : ''].filter(Boolean).join(' · ')
      body.appendChild(st)
    }
    for (const im of m.images) {
      const fig = document.createElement('figure')
      const img = document.createElement('img')
      img.loading = 'lazy'; img.decoding = 'async'; img.alt = im.alt; img.referrerPolicy = 'no-referrer'
      const noteStatus = (status) => { im.status = status; if (p.meta && Array.isArray(p.meta.images)) {
        const hit = p.meta.images.find((row) => row.url === im.url); if (hit) hit.status = status
      } }
      img.addEventListener('load', () => noteStatus('loaded'), { once: true })
      img.addEventListener('error', () => {
        let status = 'error'
        try {
          const rows = performance.getEntriesByName(im.url)
          const last = rows[rows.length - 1]
          const code = last && last.responseStatus
          if (code === 404) status = '404'
          else if (code === 0) status = 'csp-blocked'
        } catch {}
        noteStatus(status)
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
    for (const clip of m.clips || []) {
      const fig = document.createElement('figure'); fig.className = 'clip'
      if (clip.embed) {
        const frame = document.createElement('iframe')
        frame.title = clip.title || 'Clip'
        frame.loading = 'lazy'
        frame.referrerPolicy = 'no-referrer'
        frame.setAttribute('sandbox', 'allow-scripts allow-presentation')
        frame.setAttribute('allow', 'fullscreen; picture-in-picture')
        frame.setAttribute('allowfullscreen', '')
        frame.src = clip.embed
        fig.appendChild(frame)
      }
      const a = document.createElement('a')
      a.href = clip.url; a.target = '_blank'; a.rel = 'noopener noreferrer'
      a.textContent = clip.title || clip.url
      fig.appendChild(a)
      body.appendChild(fig)
    }
    if (!m.md && !m.images.length && !m.links.length && !(m.clips || []).length) { const e = document.createElement('p'); e.textContent = 'No details for this pin.'; e.style.opacity = '.7'; body.appendChild(e) }
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
  const pinInfo = (p) => {
    if (!p) return p
    const info = {
      id: p.id, label: p.label || p.meta.title, latitude: p.lat, longitude: p.lon,
      title: p.meta.title, description_md: p.meta.md, images: p.meta.images, links: p.meta.links, tags: p.tags,
    }
    if (p.meta.clips && p.meta.clips.length) info.clips = p.meta.clips
    return info
  }
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
    if (action === 'update') {
      const p = find(args)
      if (!p) return { ok: false, action: 'hermes_pin_update', error: 'not_found' }
      if (args.label) p.label = String(args.label).slice(0, 80)
      p.meta = drawerMeta({
        ...p.meta, title: args.title || p.meta.title, description_md: args.description_md ?? p.meta.md,
        images: args.images || p.meta.images, links: args.links || p.meta.links,
        clips: Array.isArray(args.clips) ? args.clips : p.meta.clips, label: p.label,
      }, p.label)
      if (args.tags && typeof args.tags === 'object') p.tags = pinTags({ tags: args.tags })
      const label = p.ents && p.ents[0] && p.ents[0].label
      if (label && p.label) label.text = p.label
      if (openId === p.id) fillDrawer(p)
      stackLabels()
      return { ok: true, action: 'hermes_pin_update', pin: pinInfo(p), redraw: false }
    }
    if (action === 'state') return { ok: true, action: 'hermes_pin_drawer', open: Boolean(openId), pin: pinInfo(pins.get(openId)), pins: [...pins.values()].slice(0, 50).map(pinInfo) }
    return { action: 'hermes_pin_drawer', ...open({ id: args.id, label: args.label, latitude: args.latitude, longitude: args.longitude }, { flyTo: Boolean(args.flyTo) }) }
  }
  // Ground point a pin (or any mark) should sit on, for other Hermes overlays (cards).
  const heightFor = (lat, lon) => groundAt(lon, lat)

  // ---- facets, filters and clustering ------------------------------------------------------
  // Facets come from POI tags passed with the pin (annotate_map item `tags`): category values
  // (amenity, shop, tourism, cuisine, category), rating (rating/stars) and opening_hours.
  const CAT_KEYS = ['category', 'amenity', 'shop', 'tourism', 'leisure', 'cuisine']
  function pinTags(spec) {
    const t = spec && typeof spec.tags === 'object' && spec.tags ? spec.tags : {}
    const cats = new Set()
    for (const k of CAT_KEYS) for (const c of String(t[k] ?? '').split(/[;,]/)) { const x = c.trim().toLowerCase().replace(/\s+/g, '_'); if (x && x.length < 40) cats.add(x) }
    const r = Number.parseFloat(t.rating ?? t.stars)
    return { cats: [...cats].slice(0, 8), rating: Number.isFinite(r) ? r : null,
      hours: typeof t.opening_hours === 'string' ? t.opening_hours.slice(0, 400) : null,
      alert: Boolean(t.watch) }  // area-watch alert pins are never filtered out
  }
  let filters = { categories: [], min_rating: null, open_now: false, include_unknown: true, cluster: true }
  let nowOverride = null
  const OH = () => window.__hermesOpeningHours
  function openState(p) { if (!p.tags.hours || !OH()) return null; return OH().isOpen(p.tags.hours, nowOverride ? new Date(nowOverride) : new Date()) }
  const passes = (p) => {
    const f = filters
    if (p.tags.alert) return true
    if (f.categories.length && !p.tags.cats.some((c) => f.categories.includes(c))) return false
    if (f.min_rating != null) { if (p.tags.rating == null) { if (!f.include_unknown) return false } else if (p.tags.rating < f.min_rating) return false }
    if (f.open_now) { const o = openState(p); if (o === false) return false; if (o == null && !f.include_unknown) return false }
    return true
  }
  const CLUSTER_PX = 56, CLUSTER_MAX_HEIGHT_M = 2500   // no clustering at street level
  let clusterEnts = [], clusters = new Map(), lastSig = null, cseq = 0
  const bubbleSvg = (n) => {
    const r = n < 10 ? 15 : n < 100 ? 18 : 21, d = r * 2 + 4
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${d}" height="${d}">` +
      `<circle cx="${d / 2}" cy="${d / 2}" r="${r}" fill="rgba(56,242,200,.88)" stroke="#06121c" stroke-width="2.5"/>` +
      `<text x="50%" y="50%" dy=".35em" text-anchor="middle" font-family="ui-monospace,Menlo,monospace" font-weight="700" font-size="${n < 100 ? 14 : 12}" fill="#06121c">${n}</text></svg>`)
  }
  const toWin = (p) => {
    const Ce = C(), v = g().viewer, ST = Ce?.SceneTransforms
    const f = ST && (ST.worldToWindowCoordinates || ST.wgs84ToWindowCoordinates)
    if (!f) return null
    try { const w = f(v.scene, Ce.Cartesian3.fromDegrees(p.lon, p.lat, p.h ?? 0)); return w && Number.isFinite(w.x) ? w : null } catch { return null }
  }
  const camSig = () => { try { const c = g().viewer.camera, pc = c.positionCartographic; return [pc.longitude, pc.latitude, pc.height, c.heading, c.pitch].map((x) => Math.round(x * 1e4)).join(',') } catch { return '' } }
  function refreshView(force) {
    if (!ds) return
    const sig = camSig() + '|' + pins.size + '|' + JSON.stringify(filters)
    if (!force && sig === lastSig) return
    lastSig = sig
    const Ce = C()
    for (const e of clusterEnts) try { ds.entities.remove(e) } catch {}
    clusterEnts = []; clusters = new Map()
    const visible = []
    for (const p of pins.values()) { p.visible = passes(p); if (p.visible) visible.push(p) }
    let height = 0
    try { height = g().viewer.camera.positionCartographic.height } catch {}
    const groups = []
    if (filters.cluster !== false && height > CLUSTER_MAX_HEIGHT_M && visible.length > 1) {
      for (const p of visible) {
        const w = toWin(p)
        if (!w) { groups.push({ members: [p] }); continue }
        const gr = groups.find((x) => x.w && Math.abs(x.w.x - w.x) < CLUSTER_PX && Math.abs(x.w.y - w.y) < CLUSTER_PX)
        if (gr) gr.members.push(p); else groups.push({ w: { x: w.x, y: w.y }, members: [p] })
      }
    } else for (const p of visible) groups.push({ members: [p] })
    for (const p of pins.values()) p.clustered = false
    for (const gr of groups) {
      if (gr.members.length < 2) continue
      const id = `cl-${++cseq}`
      const lat = gr.members.reduce((s, p) => s + p.lat, 0) / gr.members.length
      const lon = gr.members.reduce((s, p) => s + p.lon, 0) / gr.members.length
      const h = Math.max(...gr.members.map((p) => p.h ?? 0))
      const e = ds.entities.add({ _hermesCluster: id, position: Ce.Cartesian3.fromDegrees(lon, lat, h),
        billboard: { image: bubbleSvg(gr.members.length), verticalOrigin: Ce.VerticalOrigin.CENTER ?? Ce.VerticalOrigin.BOTTOM,
          disableDepthTestDistance: Number.POSITIVE_INFINITY } })
      e._hermesCluster = id
      clusterEnts.push(e)
      clusters.set(id, { id, lat, lon, ids: gr.members.map((p) => p.id) })
      for (const p of gr.members) p.clustered = true
    }
    for (const p of pins.values()) for (const e of p.ents) e.show = Boolean(p.visible && !p.clustered)
    renderBar()
    try { g().viewer.scene.requestRender?.() } catch {}
  }
  function expandCluster(id) {
    const c = clusters.get(id)
    if (!c) return { ok: false, error: 'cluster not found' }
    const ms = c.ids.map((i) => pins.get(i)).filter(Boolean)
    const Ce = C(), v = g().viewer
    let s = Math.min(...ms.map((p) => p.lat)), n = Math.max(...ms.map((p) => p.lat))
    let w = Math.min(...ms.map((p) => p.lon)), e = Math.max(...ms.map((p) => p.lon))
    const pad = Math.max(0.0015, (n - s) * 0.25, (e - w) * 0.25)
    try { v.camera.flyTo({ destination: Ce.Rectangle.fromDegrees(w - pad, s - pad, e + pad, n + pad), duration: 1.0 }) } catch {}
    return { ok: true, expanded: id, count: ms.length, bbox: [s, w, n, e] }
  }
  // ---- filter bar (chips) ------------------------------------------------------------------
  let bar = null
  const tellFilters = () => { try { window.parent.postMessage({ type: 'hermes-gev:filters', detail: { filters: { ...filters } } }, '*') } catch {} }
  function renderBar() {
    if (typeof document === 'undefined' || !document.body) return
    const cats = new Map()
    for (const p of pins.values()) for (const c of p.tags.cats) cats.set(c, (cats.get(c) || 0) + 1)
    const anyRating = [...pins.values()].some((p) => p.tags.rating != null)
    const anyHours = [...pins.values()].some((p) => p.tags.hours)
    if (!pins.size || (!cats.size && !anyRating && !anyHours)) { if (bar) bar.style.display = 'none'; return }
    if (!bar) {
      const st = document.createElement('style')
      st.textContent = `#hermes-gev-filters{position:fixed;left:50%;bottom:84px;transform:translateX(-50%);z-index:2147483646;display:flex;flex-wrap:wrap;gap:6px;max-width:min(720px,92vw);
        padding:6px 8px;background:rgba(3,14,11,.86);border:1px solid rgba(120,255,200,.35);border-radius:8px;font:12px/1 ui-monospace,Menlo,monospace}
        #hermes-gev-filters button{background:rgba(120,255,200,.06);color:#bfffe6;border:1px solid rgba(120,255,200,.35);border-radius:12px;padding:5px 9px;cursor:pointer;font:inherit}
        #hermes-gev-filters button.on{background:#38f2c8;color:#06121c;border-color:#38f2c8;font-weight:700}
        #hermes-gev-filters .n{opacity:.65;margin-left:4px} #hermes-gev-filters .sum{color:#7fbfa8;align-self:center;padding:0 4px}`
      document.head.appendChild(st)
      bar = document.createElement('div'); bar.id = 'hermes-gev-filters'; bar.setAttribute('role', 'toolbar'); bar.setAttribute('aria-label', 'Pin filters')
      bar.addEventListener?.('click', (ev) => {
        const b = ev.target?.closest?.('button[data-f]'); if (!b) return
        const f = b.getAttribute('data-f'), val = b.getAttribute('data-v')
        if (f === 'cat') { const s = new Set(filters.categories); s.has(val) ? s.delete(val) : s.add(val); setFilters({ categories: [...s] }) }
        else if (f === 'rating') setFilters({ min_rating: filters.min_rating == null ? 4 : null })
        else if (f === 'open') setFilters({ open_now: !filters.open_now })
        else if (f === 'reset') setFilters({ categories: [], min_rating: null, open_now: false })
        tellFilters()
      })
      document.body.appendChild(bar)
    }
    bar.style.display = 'flex'
    const shown = [...pins.values()].filter((p) => p.visible).length
    const chip = (f, v, text, on, n) => `<button type="button" data-f="${f}" data-v="${esc(v)}" class="${on ? 'on' : ''}" aria-pressed="${on}">${esc(text)}${n != null ? `<span class="n">${n}</span>` : ''}</button>`
    bar.innerHTML = [...cats.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(([c, n]) => chip('cat', c, c.replace(/_/g, ' '), filters.categories.includes(c), n)).join('') +
      (anyRating || filters.min_rating != null ? chip('rating', '', `★ ≥ ${filters.min_rating ?? 4}`, filters.min_rating != null) : '') +
      (anyHours ? chip('open', '', 'Open now', filters.open_now) : '') +
      ((filters.categories.length || filters.min_rating != null || filters.open_now) ? chip('reset', '', 'Reset', false) : '') +
      `<span class="sum">${shown}/${pins.size}</span>`
  }
  function setFilters(f = {}) {
    if (f.clear) f = { categories: [], min_rating: null, open_now: false, include_unknown: true, cluster: true }
    const n = { ...filters }
    if (Array.isArray(f.categories)) n.categories = f.categories.map((c) => String(c).trim().toLowerCase().replace(/\s+/g, '_')).filter(Boolean).slice(0, 16)
    if ('min_rating' in f) n.min_rating = Number.isFinite(Number.parseFloat(f.min_rating)) ? Number.parseFloat(f.min_rating) : null
    if ('open_now' in f) n.open_now = Boolean(f.open_now)
    if ('include_unknown' in f) n.include_unknown = f.include_unknown !== false
    if ('cluster' in f) n.cluster = f.cluster !== false
    if ('now' in f) nowOverride = f.now ? String(f.now) : null
    filters = n
    refreshView(true)
    return filterState()
  }
  const filterState = () => {
    const all = [...pins.values()]
    return { ok: true, action: 'hermes_pin_filter', filters: { ...filters }, total: all.length,
      shown: all.filter((p) => p.visible !== false).length, clusters: [...clusters.values()].map((c) => ({ id: c.id, count: c.ids.length, latitude: c.lat, longitude: c.lon })),
      categories: [...new Set(all.flatMap((p) => p.tags.cats))].slice(0, 40),
      hidden: all.filter((p) => p.visible === false).slice(0, 30).map(pinInfo),
      openState: all.slice(0, 50).map((p) => ({ id: p.id, open: p.tags.hours ? openState(p) : null })) }
  }
  const filterCommand = (args = {}) => {
    if (args.action === 'state') return filterState()
    if (args.action === 'expand') return { action: 'hermes_pin_filter', ...expandCluster(String(args.cluster_id || '')) }
    return setFilters(args)
  }
  window.__hermesPins = { add, remove, clear, detach, open, close, command, renderMarkdown, heightFor, syncWithBoard, labelOffsets,
    setFilters, filterCommand, filterState, expandCluster, refreshView,
    state: () => ({ open: openId, filters: { ...filters }, clusters: [...clusters.values()].map((c) => ({ id: c.id, count: c.ids.length })),
      pins: [...pins.values()].map((p) => ({ ...pinInfo(p), h: p.h, visible: p.visible !== false, clustered: Boolean(p.clustered) })) }) }
})()
