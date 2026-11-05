// God's Eye View pane for Hermes Desktop and the Webapp. Plain ESM, no build step.
//
// The pane is an MCP Apps host for God's Eye View's own globe panel (the same panel Claude and
// ChatGPT show). The panel runs inside the SDK's SandboxedFrame (opaque origin, scripts only) and
// loads the whole app through JSON-RPC postMessage -> tools/call panel_request, which this pane
// forwards to the plugin backend (ctx.rest) -> the God's Eye View MCP server. No framing headers,
// CORS, or allow-same-origin are needed, so Hermes' guest-content sandbox stays as designed.
import { SandboxedFrame, Button } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const PROTOCOL = '2026-01-26'

// A data: document has no hierarchical base URL, so the panel could not resolve the app's
// relative paths. Give it the app address its runtime already treats as the app's own.
const PANEL_BASE_TAG = '<base href="https://app.gods-eye-view.invalid/">'

// Cesium decides a worker is cross-origin whenever the page's own location (data:/about:srcdoc in
// an opaque-origin guest frame) differs from its base URL, and then starts it as
// `import "<name>"` or `import "<base>/Workers/<name>.js"`, a URL no guest frame can load: terrain never decodes and the
// globe draws no imagery. Start those workers from the panel's embedded worker bundle
// (window.CESIUM_WORKERS, which the panel runtime fills) instead. Runs inside the guest only.
export const PANEL_WORKER_SHIM = `<script>(() => {
  const NativeBlob = window.Blob, nativeUrl = URL.createObjectURL.bind(URL), NativeWorker = window.Worker
  const classic = new Set(); let bundle = null
  const shim = /^import "(?:[^"]*\\/Workers\\/)?([\\w-]+)(?:\\.js)?";$/
  function Blob(parts, options) {
    const m = parts && parts.length === 1 && typeof parts[0] === 'string' ? shim.exec(parts[0].trim()) : null
    if (m && typeof window.CESIUM_WORKERS === 'string') {
      bundle = bundle || nativeUrl(new NativeBlob([window.CESIUM_WORKERS], { type: 'text/javascript' }))
      const b = new NativeBlob(['importScripts(' + JSON.stringify(bundle) + ');CesiumWorkers[' + JSON.stringify(m[1]) + ']();'], { type: 'text/javascript' })
      b.__gevClassic = true
      return b
    }
    return new NativeBlob(parts, options)
  }
  Blob.prototype = NativeBlob.prototype
  window.Blob = Blob
  URL.createObjectURL = (o) => { const u = nativeUrl(o); if (o && o.__gevClassic) classic.add(u); return u }
  function Worker(url, options) {
    return new NativeWorker(url, classic.has(String(url)) ? { ...options, type: 'classic' } : options)
  }
  Worker.prototype = NativeWorker.prototype
  window.Worker = Worker
})()</script>`

// Opt-in on God's Eye View's side (GEV_EMBED_FULL_UI): show the app's own controls, HUD and
// panels instead of the clean embedded globe.
const FULL_UI_TAG = '<script>window.GEV_EMBED_FULL_UI = true</script>'

export function preparePanelHtml(html, { fullUi = true } = {}) {
  const head = PANEL_BASE_TAG + (fullUi ? FULL_UI_TAG : '') + PANEL_WORKER_SHIM
  return /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, (m) => m + head) : head + html
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
  const [html, setHtml] = useState(null)
  const ref = useRef(null)
  const statusRef = useRef(null)
  statusRef.current = status

  useEffect(() => {
    ctx.rest('/status').then(setStatus).catch(() => setStatus({ appUp: false }))
    ctx.rest('/panel').then((r) => (r?.html ? setHtml(r.html) : setErr('panel unavailable'))).catch((e) => setErr(String(e)))
  }, [])

  useEffect(() => {
    if (html) setSrc(toDataUrl(preparePanelHtml(html, { fullUi })))
  }, [html, fullUi])

  useEffect(() => {
    if (!src) return
    const onMessage = createPanelHost({ ctx, frame: () => ref.current?.contentWindow, status: () => statusRef.current })
    return ctx.addEventListener(window, 'message', onMessage)
  }, [src])

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
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setFullUi((v) => !v), children: fullUi ? 'Globe only' : 'Full UI' }),
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
