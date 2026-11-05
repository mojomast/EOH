// God's Eye View pane for Hermes Desktop. Plain ESM, no build step.
import { SandboxedFrame, Button } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useState } from 'react'

function GlobePane({ ctx }) {
  const [status, setStatus] = useState(null)
  const [hud, setHud] = useState('')
  useEffect(() => {
    ctx.rest('/status').then(setStatus).catch(() => setStatus({ appUp: false }))
  }, [])
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
          jsx('span', { children: status?.appUp ? `live · ${base}` : `app not running at ${base} (npm run dev)` }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: askHud, children: 'HUD (Hermes)' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => ctx.os.openExternal(base + '/'), children: 'Open full app' }),
          jsx('span', { className: 'truncate', children: hud })
        ]
      }),
      status?.appUp
        ? jsx(SandboxedFrame, { src: base + '/', title: "God's Eye View", className: 'flex-1 w-full border-0' })
        : jsx('div', { className: 'p-3 text-sm', children: 'Start God\u2019s Eye View with `npm run dev`, then reopen this pane.' })
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
      data: { placement: 'right', width: '560px' },
      render: () => jsx(GlobePane, { ctx })
    })
  }
}
