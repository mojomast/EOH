// Eye Of Hermes (EOH) pane (built on God's Eye View) for Hermes Desktop and the Webapp. Plain ESM, no build step.
//
// The pane is an MCP Apps host for God's Eye View's own globe panel (the same panel Claude and
// ChatGPT show). The panel runs inside the SDK's SandboxedFrame (opaque origin, scripts only) and
// loads the whole app through JSON-RPC postMessage -> tools/call panel_request, which this pane
// forwards to the plugin backend (ctx.rest) -> the God's Eye View MCP server. No framing headers,
// CORS, or allow-same-origin are needed, so Hermes' guest-content sandbox stays as designed.
import * as sdk from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const PROTOCOL = '2026-01-26'

// The backend (/panel, gev/panel_inject.py) returns the panel document ready for the sandboxed
// frame: a <base> for the data: document, the opt-in full-UI flag, a Cesium worker shim and the
// live-globe agent that runs Hermes tool calls with God's Eye View's own voice action runner.
// All of that runs only inside the opaque-origin frame.

const { SandboxedFrame, Button } = sdk

// Chat session ids this pane is showing (runtime + stored ids, when the SDK exposes them), so the
// relay can route a session's tool calls to the pane next to it.
export function paneSessions(host = sdk.host) {
  const st = host?.state || {}
  const ids = ['focusedSessionId', 'activeSessionId', 'focusedStoredSessionId']
    .map((k) => { try { return st[k]?.get?.() } catch { return null } })
    .filter((v) => typeof v === 'string' && v)
  return [...new Set(ids)]
}

export function newPaneId() {
  const c = globalThis.crypto
  return 'pane-' + (c?.randomUUID ? c.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10))
}

// One stable pane id per chat session (survives remounts). Falls back to a fresh id with no session.
const PANE_BIND_PREFIX = 'hermes-gev:pane-id:'
export function stablePaneId(sessions = paneSessions(), store = globalThis.sessionStorage) {
  const primary = (sessions || []).find((s) => typeof s === 'string' && s) || null
  if (!primary || !store) return newPaneId()
  const key = PANE_BIND_PREFIX + primary
  try {
    const existing = store.getItem(key)
    if (existing) return existing
    const id = newPaneId()
    store.setItem(key, id)
    return id
  } catch {
    return newPaneId()
  }
}

// Long-poll the plugin backend for Hermes tool calls aimed at the live globe, run them in the
// frame, and post the results back. Each pane polls with its own id and session ids.
// Returns { onMessage, stop }.
export function createLiveRelay({ ctx, frame, paneId = newPaneId(), sessions = () => paneSessions(), onReload = null, label = null }) {
  let stopped = false
  let fresh = true  // first poll of a new frame: the relay restores this session's scene
  let paneLabel = typeof label === 'function' ? label() : label
  const pending = new Map()
  // Commands go to the frame only once its app is up: a message posted earlier would land in the
  // iframe's initial about:blank document and be lost (seen as a stuck "pane busy").
  let markReady
  const frameReady = new Promise((r) => { markReady = r })
  const pinger = setInterval(() => { try { frame()?.postMessage({ type: 'hermes-gev:ping' }, '*') } catch {} }, 2000)
  frameReady.then(() => clearInterval(pinger))
  const nextPath = () => {
    const q = new URLSearchParams({ pane: paneId })
    if (fresh) q.set('fresh', '1')
    const ids = sessions()
    if (ids.length) q.set('sessions', ids.join(','))
    const lbl = typeof label === 'function' ? label() : paneLabel
    if (lbl) q.set('label', String(lbl).slice(0, 80))
    return '/live/next?' + q
  }
  const setLabel = (next) => { paneLabel = next ? String(next).slice(0, 80) : null }
  async function loop() {
    await Promise.race([frameReady, new Promise((r) => setTimeout(r, 180000))])
    while (!stopped) {
      let cmd = null
      try { cmd = (await ctx.rest(nextPath()))?.command; fresh = false } catch { await new Promise((r) => setTimeout(r, 3000)); continue }
      if (!cmd || stopped) continue
      if (cmd.name === 'hermes_reload') {  // pane-level: re-fetch /panel and rebuild the frame
        await ctx.rest('/live/result', { method: 'POST', body: { id: cmd.id, pane: paneId, payload: { ok: true, action: 'hermes_reload', pane: paneId } } }).catch(() => {})
        stopped = true
        onReload?.()
        break
      }
      const done = new Promise((resolve) => pending.set(cmd.id, resolve))
      frame()?.postMessage({ type: 'hermes-gev:run', id: cmd.id, name: cmd.name, args: cmd.args }, '*')
      const payload = await Promise.race([done, new Promise((r) => setTimeout(() => r({ ok: false, error: 'pane timeout' }), 150000))])
      pending.delete(cmd.id)
      ctx.rest('/live/result', { method: 'POST', body: { id: cmd.id, pane: paneId, payload } }).catch(() => {})
    }
  }
  loop()
  return {
    paneId,
    setLabel,
    getLabel: () => paneLabel,
    onMessage(event) {
      const win = frame()
      if (!win || event.source !== win) return false
      if (event.data?.type === 'hermes-gev:ready') { markReady(); return true }
      if (event.data?.type !== 'hermes-gev:result') return false
      pending.get(event.data.id)?.(event.data.payload)
      return true
    },
    stop() { stopped = true; clearInterval(pinger) }
  }
}

// Release the frame's WebGL context now instead of at GC (see gev/panel_live_agent.js).
export function disposeFrame(win) {
  try { win?.postMessage({ type: 'hermes-gev:dispose' }, '*') } catch {}
}

// GL-health messages from the frame: 'lost' | 'failed' | 'restored', else null.
export function glEvent(event, win) {
  if (!win || event.source !== win) return null
  const t = event.data?.type
  return t === 'hermes-gev:gl-lost' ? 'lost' : t === 'hermes-gev:gl-failed' ? 'failed' : t === 'hermes-gev:gl-restored' ? 'restored' : null
}

// Panel documents survive pane remounts, so a remount never refetches or rebuilds the data: URL.
const panelCache = new Map()
export function clearPanelCache() { panelCache.clear() }
export function loadPanel(ctx, fullUi) {
  const key = fullUi ? 'full' : 'globe'
  if (!panelCache.has(key)) {
    const p = ctx.rest(`/panel?fullUi=${fullUi}`).then((r) => {
      if (!r?.html) throw new Error('panel unavailable')
      return toDataUrl(r.html)
    })
    p.catch(() => panelCache.delete(key))
    panelCache.set(key, p)
  }
  return panelCache.get(key)
}

export function toDataUrl(html) {
  const bytes = new TextEncoder().encode(html)
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return 'data:text/html;base64,' + btoa(bin)
}

const DEFAULT_VIEW = { camera: { lat: 20, lon: 0, altitude_m: 15000000 }, style: 'normal', layers: ['flights'] }

// Links from the panel name the app at the MCP server's API base (often a loopback or raw IP);
// open them at the browser-reachable public URL instead (GODSEYE_PUBLIC_URL via /status).
export function publicLink(url, status) {
  const from = status?.apiBase, to = status?.frameUrl
  if (!url || !from || !to || from === to) return url
  return url.startsWith(from) ? to + url.slice(from.length) : url
}

export function createPanelHost({ ctx, frame, initialView, status = () => null }) {
  const post = (msg) => frame()?.postMessage({ jsonrpc: '2.0', ...msg }, '*')
  const reply = (id, result) => post({ id, result })
  const fail = (id, message) => post({ id, error: { code: -32000, message } })
  const callTool = (name, args) => ctx.rest('/tool', { method: 'POST', body: { name, arguments: args } })

  async function sendInitialView() {
    const result = await callTool('show_in_gods_eye_view', initialView || DEFAULT_VIEW)
    post({ method: 'ui/notifications/tool-result', params: result })
  }

  return async function onMessage(event) {
    const win = frame()
    if (!win || event.source !== win) return
    const msg = event.data
    if (!msg || msg.jsonrpc !== '2.0' || !msg.method) return
    const { id, method, params } = msg
    try {
      if (method === 'ui/initialize')
        return reply(id, {
          protocolVersion: params?.protocolVersion || PROTOCOL,
          hostInfo: { name: 'hermes', version: '1' },
          hostCapabilities: { openLinks: {}, serverTools: {} },
          hostContext: { displayMode: 'inline', availableDisplayModes: ['inline'], theme: 'dark' }
        })
      if (method === 'ui/notifications/initialized') return void sendInitialView()
      if (method === 'tools/call') {
        if (params?.name !== 'panel_request') return fail(id, 'tool not available to the panel')
        return reply(id, await callTool('panel_request', params.arguments || {}))
      }
      if (method === 'ui/open-link') {
        await ctx.os.openExternal(publicLink(String(params?.url || ''), status()))
        return reply(id, {})
      }
      if (method === 'ui/request-display-mode') return reply(id, { mode: 'inline' })
      if (id !== undefined) return reply(id, {})
    } catch (error) {
      if (id !== undefined) fail(id, String(error?.message || error))
    }
  }
}

function GlobePane({ ctx }) {
  const [status, setStatus] = useState(null)
  const [src, setSrc] = useState(null)
  const [err, setErr] = useState('')
  const [hud, setHud] = useState('')
  const [fullUi, setFullUi] = useState(true)
  const [homeView, setHomeView] = useState(undefined)
  const [gl, setGl] = useState(null)
  const [nonce, setNonce] = useState(0)
  const [epoch, setEpoch] = useState(0)
  const ref = useRef(null)
  const paneIdRef = useRef(null)
  if (!paneIdRef.current) paneIdRef.current = stablePaneId(paneSessions())
  const labelRef = useRef(null)
  const statusRef = useRef(null)
  statusRef.current = status

  useEffect(() => {
    let live = true
    const pull = () => ctx.rest('/status').then(async (st) => {
      if (!live) return
      let panes = null
      try { panes = (await ctx.rest('/live/panes'))?.panes } catch {}
      const next = { ...st, ...(panes ? { panes } : {}) }
      const mine = (panes || []).find((p) => p.id === paneIdRef.current)
      if (mine?.label) labelRef.current = mine.label
      setStatus(next)
      if (st?.patched && !st.patched.fullUi) setFullUi(false)
    }).catch(() => live && setStatus({ appUp: false }))
    pull()
    const t = setInterval(pull, 15000)
    return () => { live = false; clearInterval(t) }
  }, [])

  // Cold start opens at the saved home view (/gev home set), else the default globe.
  useEffect(() => {
    ctx.rest('/home').then((r) => setHomeView(r?.initialView || null)).catch(() => setHomeView(null))
  }, [])

  useEffect(() => {
    let live = true
    loadPanel(ctx, fullUi).then((u) => live && setSrc(u)).catch((e) => live && setErr(String(e?.message || e)))
    return () => { live = false }
  }, [fullUi, epoch])

  useEffect(() => {
    if (!src || homeView === undefined) return
    let lastWin = null  // refs are cleared before cleanup runs; keep the window to dispose it
    const frame = () => { const w = ref.current?.contentWindow; if (w) lastWin = w; return w || lastWin }
    const onMessage = createPanelHost({ ctx, frame, initialView: homeView || undefined, status: () => statusRef.current })
    // godseye_live_reload_pane / godseye_rebuild_panel: fetch a fresh panel, rebuild the frame.
    const onReload = () => { disposeFrame(frame()); clearPanelCache(); setSrc(null); setEpoch((n) => n + 1); setNonce((n) => n + 1) }
    const live = createLiveRelay({ ctx, frame, paneId: paneIdRef.current, onReload, label: () => labelRef.current })
    const onGl = (e) => {
      const kind = glEvent(e, frame())
      if (kind) setGl(kind === 'restored' ? null : kind)
      return Boolean(kind)
    }
    const off = ctx.addEventListener(window, 'message', (e) => onGl(e) || live.onMessage(e) || onMessage(e))
    return () => { live.stop(); off?.(); disposeFrame(frame()) }
  }, [src, homeView, nonce])
  const reloadGraphics = () => { disposeFrame(ref.current?.contentWindow); setGl(null); setNonce((n) => n + 1) }

  const base = status?.frameUrl || status?.apiBase || 'http://localhost:4173'
  const askHud = async () => {
    setHud('…')
    const r = await ctx.rest('/hud', { method: 'POST', body: { context: { locale: 'en' } } }).catch((e) => ({ error: String(e) }))
    setHud(r.summary || r.error || 'no summary')
  }
  return jsxs('div', {
    className: 'flex h-full flex-col',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-2 p-2 text-xs text-(--ui-text-tertiary)',
        children: [
          jsx('span', { children: status?.appUp ? 'live' : `app not running (${base})` }),
          jsx('span', { className: 'truncate', title: paneIdRef.current,
            children: (status?.panes || []).find((p) => p.id === paneIdRef.current)?.label || paneIdRef.current }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: askHud, children: 'HUD (Hermes)' }),
          status?.patched?.fullUi === false ? jsx('span', { title: 'Full UI needs patches/0002 on your God\'s Eye View checkout', children: 'globe only' }) : jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setFullUi((v) => !v), children: fullUi ? 'Globe only' : 'Full UI' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => ctx.os.openExternal(base + '/'), children: 'Open full app' }),
          jsx('span', { className: 'truncate', children: hud || err })
        ]
      }),
      gl
        ? jsxs('div', {
          className: 'flex flex-1 flex-col items-center justify-center gap-2 p-4 text-sm', style: { minHeight: 520 },
          children: [
            jsx('div', { children: gl === 'failed' ? 'The browser could not start 3D graphics (too many WebGL contexts in use).' : 'Graphics reset: the browser dropped the 3D context.' }),
            jsx(Button, { size: 'sm', onClick: reloadGraphics, children: 'Click to reload the globe' }),
            jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'If it fails again, close other 3D/map tabs or reopen this browser tab.' })
          ]
        })
        : src && homeView !== undefined
        ? jsx(SandboxedFrame, { key: `${fullUi ? 'f' : 'g'}-${nonce}`, ref, src, title: "Eye Of Hermes (EOH)", className: 'w-full flex-1 border-0', style: { minHeight: 520 } })
        : jsx('div', { className: 'p-3 text-sm', children: err || 'Loading God\u2019s Eye View…' })
    ]
  })
}

export default {
  id: 'godseye',
  name: "Eye Of Hermes (EOH)",
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: 'globe',
      area: 'panes',
      title: "Eye Of Hermes (EOH)",
      // Keep-alive: the pane host is never remounted or moved when the layout changes (e.g. the
      // window crosses the narrow breakpoint), so the frame and its WebGL context survive resizes.
      // Pane ids are stable per chat session (see stablePaneId); labels show in the chrome + godseye_live_panes.
      data: { placement: 'right', width: '900px', lifecycleKeepAlive: true },
      render: () => jsx(GlobePane, { ctx })
    })
  }
}
