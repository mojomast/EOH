// Eye Of Hermes (EOH) branding for the pane (runs inside the sandboxed frame, before the app).
// Upstream renders its own "GOD'S EYE VIEW" header, loader title, page <title>, status line and
// "Open in God's Eye View" button. Swap only those brand spots (never credits/attribution text),
// add a quiet "built on God's Eye View" credit, and re-apply when the app re-renders its header.
(() => {
  const TITLE = 'Eye Of Hermes (EOH)'
  const CREDIT = "Eye Of Hermes (EOH) · built on God's Eye View"
  const OLD = /God['’]s Eye View/g
  const setText = (el, t) => { if (el && el.textContent !== t) el.textContent = t }
  // Upstream header is <h1><span class=title-logo>…</span> <span>GOD'S EYE …</span></h1>.
  // The mark is the 128px PNG (window.__EOH_LOGO); the tab icon is a separate <link> from panel_inject.
  const paintLogo = (h) => {
    const src = window.__EOH_LOGO
    if (!src || !h) return
    let logo = null
    for (const c of h.children || []) if (c && c.className === 'title-logo') logo = c
    if (!logo && typeof h.querySelector === 'function') logo = h.querySelector('.title-logo')
    if (!logo || logo.dataset.eohLogo === '1') return
    logo.dataset.eohLogo = '1'
    if (logo.style) logo.style.backgroundImage = 'none'
    const img = document.createElement('img')
    img.setAttribute('alt', '')
    img.setAttribute('aria-hidden', 'true')
    img.setAttribute('src', src)
    img.setAttribute('width', '28')
    img.setAttribute('height', '28')
    if (img.style) img.style.cssText = 'width:28px;height:28px;display:block;object-fit:contain'
    logo.appendChild(img)
  }
  const paintIcon = () => {
    const src = window.__EOH_LOGO_ICON || window.__EOH_LOGO
    const head = document.head
    if (!src || !head || typeof head.appendChild !== 'function') return
    const links = typeof document.querySelectorAll === 'function'
      ? document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"]') : []
    if (!links.length) {
      const link = document.createElement('link')
      link.setAttribute('rel', 'icon')
      link.setAttribute('type', 'image/png')
      link.setAttribute('href', src)
      head.appendChild(link)
      return
    }
    for (const link of links) if (!link.getAttribute || link.getAttribute('href') !== src) link.setAttribute('href', src)
  }
  const brandHeading = (h) => {
    // <h1>[logo] <span translate=no>GOD'S EYE <span class=title-accent>VIEW</span></span></h1>
    paintLogo(h)
    const accent = h.querySelector('.title-accent')
    const holder = accent ? accent.parentElement : h
    if (holder.dataset.eoh === '1' && /^EOH$/.test(holder.textContent.trim())) return
    if (!/GOD['’]S\s+EYE/i.test(holder.textContent)) return
    holder.dataset.eoh = '1'
    holder.textContent = 'EO'  // short form: fits where upstream's header sits next to the toolbar
    const acc = document.createElement('span')
    acc.className = 'title-accent'
    acc.textContent = 'H'
    holder.appendChild(acc)
    h.title = CREDIT
    h.setAttribute('aria-label', TITLE)
  }
  const apply = () => {
    paintIcon()
    if (document.title !== TITLE) document.title = TITLE
    for (const h of document.querySelectorAll('h1, h2')) brandHeading(h)
    // tagline under the header becomes the credit line
    for (const p of document.querySelectorAll('.subtitle')) {
      if (/NO PLACE LEFT BEHIND/i.test(p.textContent)) { setText(p, 'EYE OF HERMES'); p.title = CREDIT }
    }
    const open = document.getElementById('open')
    if (open && OLD.test(open.textContent)) { setText(open, 'Open full app'); open.title = "Open the full God's Eye View app" }
    OLD.lastIndex = 0
    const st = document.getElementById('status')
    if (st && OLD.test(st.textContent)) setText(st, st.textContent.replace(OLD, TITLE))
    OLD.lastIndex = 0
  }
  let queued = false
  const schedule = () => { if (!queued) { queued = true; (window.requestAnimationFrame || setTimeout)(() => { queued = false; try { apply() } catch {} }) } }
  const start = () => {
    try { apply() } catch {}
    new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true, characterData: true })
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true })
  else start()
  window.__hermesBrand = { TITLE, CREDIT, apply }
})()
