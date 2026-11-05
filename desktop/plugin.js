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

// Fork embed protocol (mojomast/gods-eye-view docs/EMBEDDING.md).
// Visibility: {type:'gev:visibility', visible}. Reply: {type:'gev:visibility-applied', visible}.
// Config: {type:'gev:config', googleMapsApiKey, cesiumIonToken, photonUrl, mapillaryToken, perf}
// plus an optional nested config object. Reply: {type:'gev:config-applied', ok, perf}.
// Post config before the viewer exists (?waitForConfig=1 / GEV_WAIT_FOR_CONFIG). Key values
// are never written into this file; the backend supplies configMessage.
export function forkVisibilityMessage(visible) {
  return { type: 'gev:visibility', visible: !!visible }
}
const CONFIG_FIELDS = ['googleMapsApiKey', 'cesiumIonToken', 'photonUrl', 'mapillaryToken', 'perf']
export function forkConfigMessage(config, perf) {
  const src = config && typeof config === 'object' ? config : {}
  const nested = src.config && typeof src.config === 'object' && !Array.isArray(src.config) ? src.config : null
  const flat = { ...(nested || {}), ...src }
  const msg = { type: 'gev:config' }
  for (const key of CONFIG_FIELDS) {
    if (typeof flat[key] === 'string' && flat[key]) msg[key] = flat[key]
  }
  const mode = perf || flat.perf
  if (mode === 'embed' || mode === 'default') msg.perf = mode
  const body = {}
  for (const key of CONFIG_FIELDS) if (typeof msg[key] === 'string') body[key] = msg[key]
  if (Object.keys(body).length) msg.config = body
  return msg
}
export function postForkMessage(frame, message) {
  try { (frame?.() || frame)?.postMessage(message, '*') } catch {}
}
// Page Visibility plus IntersectionObserver. Hidden means the globe pane is covered or the tab is in the background.
export function watchPaneVisibility(el, onChange) {
  if (typeof onChange !== 'function') return () => {}
  let intersecting = true
  const emit = () => {
    const pageOn = typeof document === 'undefined' || document.visibilityState !== 'hidden'
    onChange(Boolean(pageOn && intersecting))
  }
  const clean = []
  if (typeof document !== 'undefined' && document.addEventListener) {
    const fn = () => emit()
    document.addEventListener('visibilitychange', fn)
    clean.push(() => document.removeEventListener('visibilitychange', fn))
  }
  if (el && typeof IntersectionObserver === 'function') {
    try {
      const io = new IntersectionObserver((entries) => {
        intersecting = entries.some((e) => e.isIntersecting && e.intersectionRatio > 0)
        emit()
      }, { threshold: [0, 0.01] })
      io.observe(el)
      clean.push(() => io.disconnect())
    } catch { /* ref is not an Element yet; page visibility still applies */ }
  }
  emit()
  return () => { for (const fn of clean) fn() }
}

// Long-poll the plugin backend for Hermes tool calls aimed at the live globe, run them in the
// frame, and post the results back. Each pane polls with its own id and session ids.
// Returns { onMessage, stop }.
export function createLiveRelay({ ctx, frame, paneId = newPaneId(), sessions = () => paneSessions(), onReload = null, label = null, onConfigApplied = null }) {
  let stopped = false
  let fresh = true  // first poll of a new frame: the relay restores this session's scene
  let paneLabel = typeof label === 'function' ? label() : label
  const pending = new Map()
  let lastState = ''
  // Commands go to the frame only once its app is up: a message posted earlier would land in the
  // iframe's initial about:blank document and be lost (seen as a stuck "pane busy").
  let markReady
  const frameReady = new Promise((r) => { markReady = r })
  const pinger = setInterval(() => { try { frame()?.postMessage({ type: 'hermes-gev:ping' }, '*') } catch {} }, 2000)
  frameReady.then(() => clearInterval(pinger))
  const PANE_COMMAND_MS = 150000
  let inflightName = ''
  // One command at a time. A second long-poll would start a second command.
  // This timer posts /live/heartbeat so the stale clock does not depend on that loop.
  const heart = setInterval(() => {
    if (stopped) return
    const body = { pane: paneId, busy: Boolean(inflightName) }
    if (inflightName) body.name = inflightName
    ctx.rest('/live/heartbeat', { method: 'POST', body }).catch(() => {})
  }, 5000)
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
        inflightName = ''
        onReload?.()
        break
      }
      const done = new Promise((resolve) => pending.set(cmd.id, resolve))
      inflightName = String(cmd.name || '')
      frame()?.postMessage({ type: 'hermes-gev:run', id: cmd.id, name: cmd.name, args: cmd.args }, '*')
      const payload = await Promise.race([done, new Promise((r) => setTimeout(() => r({ ok: false, error: 'pane timeout' }), PANE_COMMAND_MS))])
      inflightName = ''
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
      if (event.data?.type === 'gev:config-applied') { onConfigApplied?.(event.data); return true }
      if (event.data?.type === 'gev:visibility-applied') return true
      if (event.data?.type === 'hermes-gev:ready') { markReady(); return true }
      if (event.data?.type === 'hermes-gev:state') {  // cached map line; not a tool call
        const snap = event.data.detail
        const key = JSON.stringify(snap || {})
        if (key !== lastState) {
          lastState = key
          ctx.rest('/live/state', { method: 'POST', body: { pane: paneId, state: snap } }).catch(() => {})
        }
        return true
      }
      if (event.data?.type === 'hermes-gev:drawer') {  // a click opened/closed a pin drawer: persist it
        const pin = event.data.detail?.pin
        const ok = pin && Number.isFinite(pin.latitude) && Number.isFinite(pin.longitude)
        ctx.rest('/live/drawer', { method: 'POST', body: { pane: paneId, pin: ok ? { latitude: pin.latitude, longitude: pin.longitude, label: String(pin.label ?? '').slice(0, 120) } : null } }).catch(() => {})
        return true
      }
      if (event.data?.type === 'hermes-gev:timeline') {  // the time slider moved: keep the playhead in the scene
        const tl = event.data.detail?.timeline
        if (tl && typeof tl === 'object') ctx.rest('/live/timeline', { method: 'POST', body: { pane: paneId, timeline: {
          mode: tl.mode === 'events' ? 'events' : 'wayback',
          from: String(tl.from || '').slice(0, 10), to: String(tl.to || '').slice(0, 10),
          at: String(tl.at || '').slice(0, 10), playing: Boolean(tl.playing),
          transition: tl.transition === 'fade' ? 'fade' : 'swipe',
          index: Number.isFinite(tl.index) ? tl.index : 0,
          clear: Boolean(tl.clear) } } }).catch(() => {})
        return true
      }
      if (event.data?.type === 'hermes-gev:filters') {  // a filter chip was toggled: persist it with the scene
        const f = event.data.detail?.filters
        if (f && typeof f === 'object') ctx.rest('/live/filters', { method: 'POST', body: { pane: paneId, filters: {
          categories: (Array.isArray(f.categories) ? f.categories : []).slice(0, 16).map((c) => String(c).slice(0, 40)),
          min_rating: Number.isFinite(f.min_rating) ? f.min_rating : null, open_now: Boolean(f.open_now),
          include_unknown: f.include_unknown !== false, cluster: f.cluster !== false } } }).catch(() => {})
        return true
      }
      if (event.data?.type === 'hermes-gev:ask') {  // "Ask Hermes" in the pane: see askHermes()
        askHermes(ctx, event.data.prompt).then((r) => { try { win.postMessage({ type: 'hermes-gev:ask-result', ...r }, '*') } catch {} })
        return true
      }
      if (event.data?.type !== 'hermes-gev:result') return false
      pending.get(event.data.id)?.(event.data.payload)
      return true
    },
    stop() { stopped = true; clearInterval(pinger); clearInterval(heart) }
  }
}

// "Ask Hermes" (pane toolbar / pin drawer). The desktop plugin SDK has no API to submit or prefill a
// chat message (runAction only allows view/navigation ids such as composer.focus), so the prompt is
// copied to the clipboard and the composer focused: the user pastes (Ctrl/Cmd+V) and sends.
export async function askHermes(ctx, prompt) {
  const text = String(prompt || '').slice(0, 2000)
  if (!text) return { ok: false, error: 'empty prompt' }
  let copied = false, focused = false
  try { copied = Boolean(await ctx.os?.writeClipboard?.(text)) } catch {}
  try { focused = ctx.runAction?.('composer.focus')?.ok !== false && typeof ctx.runAction === 'function' } catch {}
  return { ok: copied, copied, focused, prompt: text }
}

// Release the frame's WebGL context now instead of at GC (see gev/panel_live_agent.js).
export function disposeFrame(win) {
  try { win?.postMessage({ type: 'hermes-gev:dispose' }, '*') } catch {}
}

// GL-health messages from the frame.
// 'lost' | 'failed' show the click-to-reload card. 'recovering' / 'recovered' /
// 'need-scene' keep the same pane mounted. 'restored' is the browser event
// used only when automatic recovery is off.
export function glEvent(event, win) {
  if (!win || event.source !== win) return null
  const t = event.data?.type
  if (t === 'hermes-gev:gl-lost') return 'lost'
  if (t === 'hermes-gev:gl-failed') return 'failed'
  if (t === 'hermes-gev:gl-restored') return 'restored'
  if (t === 'hermes-gev:gl-recovering') return 'recovering'
  if (t === 'hermes-gev:gl-recovered') return 'recovered'
  if (t === 'hermes-gev:gl-need-scene') return 'need-scene'
  return null
}

function reportGl(ctx, paneId, body) {
  const sessions = paneSessions()
  ctx.rest('/live/gl', { method: 'POST', body: { pane: paneId, sessions, ...body } }).catch(() => {})
}

// Panel documents survive pane remounts, so a remount never refetches or rebuilds the data: URL.
const panelCache = new Map()
export function clearPanelCache() { panelCache.clear() }
export function loadPanel(ctx, fullUi) {
  // configOnly is cheap and rewrites dist/panel/config.json. The document is cached per
  // configRev, so a key change fetches a new panel and a remount of the same keys does not.
  const q = `fullUi=${fullUi ? 'true' : 'false'}`
  return ctx.rest(`/panel?${q}&configOnly=1`).then(async (meta) => {
    const rev = meta?.configRev || '0'
    const gl = meta?.glRecover === false ? '0' : '1'
    const key = (fullUi ? 'full:' : 'globe:') + rev + ':gl' + gl
    if (!panelCache.has(key)) {
      const p = ctx.rest(`/panel?${q}`).then((r) => {
        if (!r?.html) throw new Error('panel unavailable')
        return { src: toDataUrl(r.html), configMessage: r.configMessage || meta?.configMessage || forkConfigMessage(null, 'embed'), configRev: r.configRev || rev }
      })
      p.catch(() => panelCache.delete(key))
      panelCache.set(key, p)
    }
    const doc = await panelCache.get(key)
    return { ...doc, configMessage: meta?.configMessage || doc.configMessage }
  })
}

// Repost gev:config until the app acknowledges it or the wait window ends.
// The pane is a data document, so this has to land after embed-boot.js installs its queue.
export function startEmbedConfig(frame, message) {
  if (!message) return () => {}
  let stopped = false
  const send = () => { if (!stopped) postForkMessage(frame, message) }
  send()
  const timer = setInterval(send, 100)
  const killer = setTimeout(finish, 2500)
  function finish() { stopped = true; clearInterval(timer); clearTimeout(killer) }
  return finish
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

// Links the panel may ask the host to open (pin drawer links, app links): http(s) only.
export function safeLink(url) {
  try { const u = new URL(String(url || '')); return u.protocol === 'https:' || u.protocol === 'http:' } catch { return false }
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
        if (!safeLink(params?.url)) return fail(id, 'only http(s) links can be opened')
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

function ageText(age) {
  if (typeof age !== 'number' || !Number.isFinite(age)) return 'age unknown'
  if (age < 90) return Math.round(age) + 's'
  if (age < 7200) return Math.round(age / 60) + 'm'
  return Math.round(age / 3600) + 'h'
}

// Control room drawer. The explicit start/stop, busy line, and error line follow
// cygnostik/Hermes-Plugin-Gods-Eye-View desktop/plugin.js (MIT). This pane only
// renders what GET /control returns. It never starts the app on its own.
function ControlRoom({ room, busy, error, onClose, onAction }) {
  const life = room?.lifecycle || {}
  const off = life.supported === false
  const pending = Boolean(busy || room?.busy)
  const app = room?.app || {}
  const relay = room?.relay || {}
  const pane = room?.pane || {}
  const gl = room?.rendering || {}
  const feeds = room?.feeds || {}
  const line = (label, value) => jsxs('div', { className: 'flex justify-between gap-3', children: [
    jsx('span', { children: label }),
    jsx('span', { className: 'truncate text-right', children: value == null || value === '' ? '—' : String(value) })
  ] })
  const version = [app.version, app.commit].filter(Boolean).join(' · ')
  return jsxs('section', {
    className: 'max-h-64 overflow-auto border-b border-(--ui-stroke-secondary) px-2 py-2 text-xs',
    role: 'region', 'aria-label': 'Control room',
    children: [
      jsxs('div', { className: 'mb-2 flex items-center justify-between gap-2', children: [
        jsx('strong', { children: 'Control room' }),
        jsx(Button, { size: 'sm', variant: 'ghost', onClick: onClose, children: 'Close' })
      ] }),
      off ? jsx('p', { children: life.detail || 'Lifecycle is unsupported on this host.' }) : null,
      pending ? jsx('p', { role: 'status', children: `${busy || room.busy}…` }) : null,
      error ? jsx('p', { role: 'alert', children: error }) : null,
      !error && room?.lastError?.message ? jsx('p', { role: 'alert', children: room.lastError.message }) : null,
      jsxs('div', { className: 'grid gap-2 sm:grid-cols-2', children: [
        jsxs('div', { children: [
          jsx('div', { className: 'font-medium', children: 'App' }),
          line('state', app.state || 'unknown'),
          line('http', app.http),
          line('version', version),
          app.url ? jsx('div', { className: 'truncate', title: app.url, children: app.url }) : null
        ] }),
        jsxs('div', { children: [
          jsx('div', { className: 'font-medium', children: 'Relay' }),
          line('loopback', relay.state || 'unknown'),
          line('HUD bridge', relay.hudBridge || 'unknown')
        ] }),
        jsxs('div', { children: [
          jsx('div', { className: 'font-medium', children: 'Pane' }),
          line('connected', pane.connected ?? 0),
          ...(pane.panes || []).slice(0, 6).map((p) => jsx('div', {
            className: 'truncate',
            children: `${p.id || 'pane'} · ${(p.sessions || []).join(', ') || 'no session'} · ${p.heartbeat === 'heartbeat' ? 'heartbeat' : 'poll'} ${ageText(p.lastHeartbeatS)}`
          }, p.id || p.lastHeartbeatS))
        ] }),
        jsxs('div', { children: [
          jsx('div', { className: 'font-medium', children: 'Rendering' }),
          line('WebGL', gl.webgl || 'unknown'),
          line('context losses', gl.contextLosses ?? 0),
          line('recover', gl.recover == null ? null : (gl.recover ? 'on' : 'off')),
          line('recreateViewer', gl.recreateViewer == null ? 'unknown' : (gl.recreateViewer ? 'present' : 'absent'))
        ] })
      ] }),
      jsxs('div', { className: 'mt-2', children: [
        jsx('div', { className: 'font-medium', children: 'Feeds' }),
        (feeds.items || []).length ? (feeds.items || []).slice(0, 12).map((f) => jsx('div', {
          className: 'truncate',
          children: `${f.id}: ${f.state}${typeof f.ageS === 'number' ? ' · ' + ageText(f.ageS) : ''}${f.state === 'error' && f.detail ? ' · ' + String(f.detail).slice(0, 80) : ''}`
        }, f.id)) : jsx('div', { children: 'no feed report yet' }),
        (feeds.missingKeys || []).length ? jsx('div', { children: 'missing ' + feeds.missingKeys.join(', ') }) : null
      ] }),
      jsxs('div', { className: 'mt-2 flex flex-wrap gap-2', children: [
        jsx(Button, { size: 'sm', disabled: off || pending, onClick: () => onAction('start'), children: (busy || room?.busy) === 'start' ? 'Starting…' : 'Start' }),
        jsx(Button, { size: 'sm', disabled: off || pending, onClick: () => onAction('stop'), children: (busy || room?.busy) === 'stop' ? 'Stopping…' : 'Stop' }),
        jsx(Button, { size: 'sm', disabled: off || pending, onClick: () => onAction('restart'), children: (busy || room?.busy) === 'restart' ? 'Restarting…' : 'Restart' })
      ] }),
      !off && life.detail ? jsx('p', { className: 'mt-2 text-(--ui-text-tertiary)', children: life.detail }) : null
    ]
  })
}

function GlobePane({ ctx }) {
  const [status, setStatus] = useState(null)
  const [src, setSrc] = useState(null)
  const configMessageRef = useRef(null)
  const [err, setErr] = useState('')
  const [hud, setHud] = useState('')
  const [fullUi, setFullUi] = useState(true)
  const [homeView, setHomeView] = useState(undefined)
  const [gl, setGl] = useState(null)
  const [roomOpen, setRoomOpen] = useState(false)
  const [room, setRoom] = useState(null)
  const [roomBusy, setRoomBusy] = useState('')
  const [roomErr, setRoomErr] = useState('')
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

  useEffect(() => {
    if (!roomOpen) return
    let live = true
    const pull = () => ctx.rest('/control').then((r) => {
      if (live && r && typeof r === 'object') setRoom(r)
    }).catch((e) => { if (live) setRoomErr(String(e?.message || e)) })
    pull()
    const t = setInterval(pull, 10000)
    return () => { live = false; clearInterval(t) }
  }, [roomOpen])

  const controlAction = async (action) => {
    if (roomBusy || room?.busy || room?.lifecycle?.supported === false) return
    setRoomBusy(action)
    setRoomErr('')
    try {
      const result = await ctx.rest('/control/app', { method: 'POST', body: { action }, timeoutMs: 120000 })
      if (!result || result.ok === false) setRoomErr(result?.message || result?.error || 'The request failed.')
      const next = await ctx.rest('/control').catch(() => null)
      if (next && typeof next === 'object') setRoom(next)
    } catch (e) {
      setRoomErr(String(e?.message || e))
    } finally {
      setRoomBusy('')
    }
  }

  // Cold start opens at the saved home view (/gev home set), else the default globe.
  useEffect(() => {
    ctx.rest('/home').then((r) => setHomeView(r?.initialView || null)).catch(() => setHomeView(null))
  }, [])

  useEffect(() => {
    let live = true
    loadPanel(ctx, fullUi).then((u) => {
      if (!live) return
      configMessageRef.current = u?.configMessage || null
      setSrc(u?.src || null)
    }).catch((e) => live && setErr(String(e?.message || e)))
    return () => { live = false }
  }, [fullUi, epoch])

  useEffect(() => {
    if (!src || homeView === undefined) return
    let lastWin = null  // refs are cleared before cleanup runs; keep the window to dispose it
    const frame = () => { const w = ref.current?.contentWindow; if (w) lastWin = w; return w || lastWin }
    const onMessage = createPanelHost({ ctx, frame, initialView: homeView || undefined, status: () => statusRef.current })
    // godseye_live_reload_pane / godseye_rebuild_panel: fetch a fresh panel, rebuild the frame.
    const onReload = () => { disposeFrame(frame()); clearPanelCache(); setSrc(null); setEpoch((n) => n + 1); setNonce((n) => n + 1) }
    let stopCfg = () => {}
    const live = createLiveRelay({
      ctx, frame, paneId: paneIdRef.current, onReload, label: () => labelRef.current,
      onConfigApplied: () => stopCfg(),
    })
    const onGl = (e) => {
      const kind = glEvent(e, frame())
      if (!kind) return false
      const detail = e.data?.detail || {}
      if (kind === 'need-scene') {
        ctx.rest('/live/gl', { method: 'POST', body: { pane: paneIdRef.current, event: 'restore', sessions: paneSessions() } })
          .then((r) => {
            try { frame()?.postMessage({ type: 'hermes-gev:gl-scene', commands: r?.commands || [], error: !r || r.ok === false }, '*') } catch {}
          })
          .catch(() => { try { frame()?.postMessage({ type: 'hermes-gev:gl-scene', error: true, commands: [] }, '*') } catch {} })
        return true
      }
      if (kind === 'recovering') {
        reportGl(ctx, paneIdRef.current, { event: 'lost', kind: 'recovering' })
        return true
      }
      if (kind === 'recovered') {
        reportGl(ctx, paneIdRef.current, { event: 'recovered', detail })
        setGl(null)
        return true
      }
      if (kind === 'restored') { setGl(null); return true }
      const notice = kind === 'lost' && detail.exhausted ? 'exhausted' : 'manual'
      reportGl(ctx, paneIdRef.current, { event: 'lost', kind: notice })
      setGl(kind === 'failed' ? 'failed' : 'lost')
      return true
    }
    const off = ctx.addEventListener(window, 'message', (e) => onGl(e) || live.onMessage(e) || onMessage(e))
    const stopVis = watchPaneVisibility(ref.current, (visible) => postForkMessage(frame, forkVisibilityMessage(visible)))
    stopCfg = startEmbedConfig(frame, configMessageRef.current)
    return () => { stopCfg(); stopVis(); live.stop(); off?.(); disposeFrame(frame()) }
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
          jsx(Button, { size: 'sm', variant: 'ghost', 'aria-expanded': roomOpen, onClick: () => setRoomOpen((v) => !v), children: roomOpen ? 'Hide control room' : 'Control room' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: askHud, children: 'HUD (Hermes)' }),
          status?.patched?.fullUi === false ? jsx('span', { title: 'Full UI needs patches/0002 on your God\'s Eye View checkout', children: 'globe only' }) : jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setFullUi((v) => !v), children: fullUi ? 'Globe only' : 'Full UI' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => ctx.os.openExternal(base + '/'), children: 'Open full app' }),
          jsx('span', { className: 'truncate', children: hud || err })
        ]
      }),
      roomOpen ? jsx(ControlRoom, {
        room, busy: roomBusy, error: roomErr, onClose: () => setRoomOpen(false), onAction: controlAction
      }) : null,
      gl
        ? jsxs('div', {
          className: 'flex flex-1 flex-col items-center justify-center gap-2 p-4 text-sm', style: { minHeight: 520 },
          children: [
            jsx('div', { children: gl === 'failed' ? 'The browser could not start 3D graphics (too many WebGL contexts in use).' : 'Graphics reset: the browser dropped the 3D context. Automatic recovery is off or stopped after repeated failures.' }),
            jsx(Button, { size: 'sm', onClick: reloadGraphics, children: 'Click to reload the globe' }),
            jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'If it fails again, close other 3D/map tabs or reopen this browser tab.' })
          ]
        })
        : src && homeView !== undefined
        ? jsx(SandboxedFrame, { key: `${fullUi ? 'f' : 'g'}-${nonce}`, ref, src, title: "Eye Of Hermes (EOH)", className: 'w-full flex-1 border-0', style: { minHeight: 520 } })
        : jsx('div', { className: 'p-3 text-sm', children: err || 'Loading Eye Of Hermes (EOH)…' })
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
