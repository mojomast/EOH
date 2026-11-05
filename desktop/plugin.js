// God's Eye View pane for Hermes Desktop and the Webapp. Plain ESM, no build step.
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

// Long-poll the plugin backend for Hermes tool calls aimed at the live globe, run them in the
// frame, and post the results back. Each pane polls with its own id and session ids.
// Returns { onMessage, stop }.
export function createLiveRelay({ ctx, frame, paneId = newPaneId(), sessions = () => paneSessions() }) {
  let stopped = false
  const pending = new Map()
  const nextPath = () => {
    const q = new URLSearchParams({ pane: paneId })
    const ids = sessions()
    if (ids.length) q.set('sessions', ids.join(','))
    return '/live/next?' + q
  }
  async function loop() {
    while (!stopped) {
      let cmd = null
      try { cmd = (await ctx.rest(nextPath()))?.command } catch { await new Promise((r) => setTimeout(r, 3000)); continue }
      if (!cmd || stopped) continue
      const done = new Promise((resolve) => pending.set(cmd.id, resolve))
      frame()?.postMessage({ type: 'hermes-gev:run', id: cmd.id, name: cmd.name, args: cmd.args }, '*')
      const payload = await Promise.race([done, new Promise((r) => setTimeout(() => r({ ok: false, error: 'pane timeout' }), 55000))])
      pending.delete(cmd.id)
      ctx.rest('/live/result', { method: 'POST', body: { id: cmd.id, pane: paneId, payload } }).catch(() => {})
    }
  }
  loop()
  return {
    paneId,
    onMessage(event) {
      const win = frame()
      if (!win || event.source !== win || event.data?.type !== 'hermes-gev:result') return false
      pending.get(event.data.id)?.(event.data.payload)
      return true
    },
    stop() { stopped = true }
  }
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
  const ref = useRef(null)
  const paneIdRef = useRef(null)
  if (!paneIdRef.current) paneIdRef.current = newPaneId()
  const statusRef = useRef(null)
  statusRef.current = status

  useEffect(() => {
    ctx.rest('/status').then((st) => { setStatus(st); if (st?.patched && !st.patched.fullUi) setFullUi(false) }).catch(() => setStatus({ appUp: false }))
  }, [])

  // Cold start opens at the saved home view (/gev home set), else the default globe.
  useEffect(() => {
    ctx.rest('/home').then((r) => setHomeView(r?.initialView || null)).catch(() => setHomeView(null))
  }, [])

  useEffect(() => {
    ctx.rest(`/panel?fullUi=${fullUi}`)
      .then((r) => (r?.html ? setSrc(toDataUrl(r.html)) : setErr('panel unavailable')))
      .catch((e) => setErr(String(e)))
  }, [fullUi])

  useEffect(() => {
    if (!src || homeView === undefined) return
    const frame = () => ref.current?.contentWindow
    const onMessage = createPanelHost({ ctx, frame, initialView: homeView || undefined, status: () => statusRef.current })
    const live = createLiveRelay({ ctx, frame, paneId: paneIdRef.current })
    const off = ctx.addEventListener(window, 'message', (e) => live.onMessage(e) || onMessage(e))
    return () => { live.stop(); off?.() }
  }, [src, homeView])

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
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: askHud, children: 'HUD (Hermes)' }),
          status?.patched?.fullUi === false ? jsx('span', { title: 'Full UI needs patches/0002 on your God\'s Eye View checkout', children: 'globe only' }) : jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setFullUi((v) => !v), children: fullUi ? 'Globe only' : 'Full UI' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => ctx.os.openExternal(base + '/'), children: 'Open full app' }),
          jsx('span', { className: 'truncate', children: hud || err })
        ]
      }),
      src
        ? jsx(SandboxedFrame, { key: src.length + (fullUi ? 'f' : 'g'), ref, src, title: "God's Eye View", className: 'w-full flex-1 border-0', style: { minHeight: 520 } })
        : jsx('div', { className: 'p-3 text-sm', children: err || 'Loading God\u2019s Eye View…' })
    ]
  })
}

export default {
  id: 'godseye',
  name: "God's Eye View",
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: 'globe',
      area: 'panes',
      title: "God's Eye View",
      data: { placement: 'right', width: '900px' },
      render: () => jsx(GlobePane, { ctx })
    })
  }
}
