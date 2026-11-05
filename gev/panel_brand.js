// Eye Of Hermes (EOH) branding for the pane (runs inside the sandboxed frame, before the app).
// Upstream renders its own "GOD'S EYE VIEW" header, loader title, page <title>, status line and
// "Open in God's Eye View" button. Swap only those brand spots (never credits/attribution text),
// add a quiet "built on God's Eye View" credit, and re-apply when the app re-renders its header.
(() => {
  const TITLE = 'Eye Of Hermes (EOH)'
  const CREDIT = "Eye Of Hermes (EOH) · built on God's Eye View"
  const OLD = /God['’]s Eye View/g
  const setText = (el, t) => { if (el && el.textContent !== t) el.textContent = t }
  const brandHeading = (h) => {
    // <h1>[logo] <span translate=no>GOD'S EYE <span class=title-accent>VIEW</span></span></h1>
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
