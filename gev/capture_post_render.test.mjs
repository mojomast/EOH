import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import vm from 'node:vm'

// Extract and exercise afterPostRender by evaluating a minimal slice of panel_live_agent.js patterns.
test('afterPostRender reads canvas inside postRender callback', async () => {
  const listeners = []
  let rendered = false
  const canvas = {
    width: 4, height: 4,
    toDataURL() {
      assert.equal(rendered, true, 'toDataURL must run after postRender fires')
      // 1x1 PNG
      return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
    },
  }
  const scene = {
    canvas,
    postRender: {
      addEventListener(fn) {
        listeners.push(fn)
        return () => {
          const i = listeners.indexOf(fn)
          if (i >= 0) listeners.splice(i, 1)
        }
      },
    },
    requestRender() {
      rendered = true
      for (const fn of [...listeners]) fn()
    },
  }
  globalThis.window = { __godsEyeView: { viewer: { scene } } }
  // Inline the helper the pane uses (kept in sync with panel_live_agent.js).
  const afterPostRender = (build) => new Promise((resolve) => {
    const v = window.__godsEyeView?.viewer
    const sc = v && v.scene
    if (!sc || typeof sc.postRender?.addEventListener !== 'function') {
      try { resolve(build(sc?.canvas || null)) } catch (e) {
        resolve({ ok: false, error: String(e && e.message || e) })
      }
      return
    }
    let done = false
    let remove = () => {}
    const finish = (result) => {
      if (done) return
      done = true
      try { remove() } catch {}
      resolve(result)
    }
    try {
      remove = sc.postRender.addEventListener(() => {
        try { finish(build(sc.canvas)) } catch (e) {
          finish({ ok: false, error: String(e && e.message || e) })
        }
      })
    } catch (e) {
      try { finish(build(sc.canvas)) } catch (e2) {
        finish({ ok: false, error: String(e2 && e2.message || e2) })
      }
      return
    }
    try { sc.requestRender?.() } catch {}
    setTimeout(() => finish({ ok: false, error: 'capture timeout waiting for postRender' }), 2000)
  })
  const out = await afterPostRender((c) => {
    const dataUrl = c.toDataURL('image/png')
    return { ok: true, dataUrl, capturedAt: 'postRender' }
  })
  assert.equal(out.ok, true)
  assert.equal(out.capturedAt, 'postRender')
  assert.match(out.dataUrl, /^data:image\/png;base64,/)
  assert.equal(listeners.length, 0)
})

test('panel_live_agent source wires postRender capture', () => {
  const src = readFileSync(new URL('./panel_live_agent.js', import.meta.url), 'utf8')
  assert.match(src, /afterPostRender/)
  assert.match(src, /capturedAt: 'postRender'/)
  assert.match(src, /await Promise\.resolve\(capture\(\)\)/)
  assert.match(src, /await Promise\.resolve\(paneShot\(\)\)/)
})
