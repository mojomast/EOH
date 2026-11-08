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

function encoder(encode) {
  const src = readFileSync(new URL('./panel_live_agent.js', import.meta.url), 'utf8')
  const helper = src.slice(src.indexOf('  const encodePaneCapture ='), src.indexOf('  // PNG of the whole pane:'))
  const sizes = []
  const document = { createElement: () => {
    const c = { width: 0, height: 0,
      getContext: () => ({ fillRect() {}, drawImage() {}, fillText() {} }),
      toDataURL(mime, q) { sizes.push([this.width, this.height, q]); return encode(mime, q, this) } }
    return c
  } }
  return { run: vm.runInNewContext(helper + '; encodePaneCapture', { document }), sizes }
}
test('pane encoder bounds dimensions and steps quality below relay limit', () => {
  const { run, sizes } = encoder((mime, q) => 'data:' + mime + ';base64,' + 'a'.repeat(q > 0.55 ? 500000 : 400000))
  const r = run({ width: 3840, height: 2160 }, { innerText: 'Test pin' })
  assert.equal(r.ok, true)
  assert.equal(r.mime, 'image/jpeg')
  assert.ok(Math.max(r.width, r.height) <= 1600)
  assert.ok(r.encodedBytes <= 450 * 1024)
  assert.equal(sizes.length, 3)
  assert.equal(r.includesDrawer, true)
})
test('pane encoder downscales again if quality alone is insufficient', () => {
  const { run, sizes } = encoder((mime, q, c) => 'data:' + mime + ';base64,' + 'a'.repeat(c.width > 1200 ? 500000 : 200000))
  assert.equal(run({ width: 4000, height: 2000 }, null).ok, true)
  assert.ok(sizes.length > 4)
})
test('pane encoder returns explicit oversize error if encoding never fits', () => {
  const { run } = encoder(() => 'data:image/png;base64,' + 'a'.repeat(500000))
  assert.equal(run({ width: 2000, height: 1000 }, null).code, 'result_too_large')
})
test('desktop upload failure sends a small error completion', async () => {
  const src = readFileSync(new URL('../desktop/plugin.js', import.meta.url), 'utf8')
  const helper = src.slice(src.indexOf('  async function postResult('), src.indexOf('  async function loop()'))
  const calls = []
  const ctx = { rest: async (path, request) => {
    calls.push(request.body)
    if (calls.length === 1) throw new Error('upload rejected')
    return { ok: true }
  } }
  const fn = vm.runInNewContext(helper + '; postResult', { ctx, paneId: 'P', TextEncoder, console: { info() {}, error() {} } })
  await fn({ id: 7 }, { ok: true, dataUrl: 'a'.repeat(600000) })
  assert.equal(calls.length, 2)
  assert.equal(calls[1].id, 7)
  assert.equal(calls[1].payload.code, 'result_upload_failed')
  assert.ok(JSON.stringify(calls[1]).length < 300)
})
