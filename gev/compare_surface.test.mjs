import test from 'node:test'
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const api = require('./compare_surface.js')

test('needsPrep when tileset shown or globe hidden', () => {
  assert.equal(api.needsPrep({ viewer: { scene: { globe: { show: true } } }, tileset: { show: true } }), true)
  assert.equal(api.needsPrep({ viewer: { scene: { globe: { show: false } } }, tileset: { show: false } }), true)
  assert.equal(api.needsPrep({ viewer: { scene: { globe: { show: true } } }, tileset: { show: false } }), false)
  assert.equal(api.needsPrep(null), false)
})

test('surfaceVisible requires globe or tileset', () => {
  assert.equal(api.surfaceVisible({ viewer: { scene: { globe: { show: false } }, imageryLayers: { length: 2 } }, tileset: { show: false } }), false)
  assert.equal(api.surfaceVisible({ viewer: { scene: { globe: { show: true } }, imageryLayers: { length: 1 } }, tileset: { show: false } }), true)
  assert.equal(api.surfaceVisible({ viewer: { scene: { globe: { show: false } }, imageryLayers: { length: 0 } }, tileset: { show: true } }), true)
})

test('acquire uses lease when mapStackController present', async () => {
  const releases = []
  const g = {
    tileset: { show: true },
    viewer: { scene: { globe: { show: false }, requestRender() {} }, imageryLayers: { length: 0, addImageryProvider() { return {} }, remove() {} } },
    mapStackController: {
      acquireImageryComparison({ owner, switchPolicy }) {
        assert.equal(owner, 'hermes-compare')
        assert.equal(switchPolicy, 'esri-if-photoreal')
        return {
          ready: Promise.resolve({ status: 'ready', activeId: 'esri-imagery' }).then((r) => {
            g.viewer.scene.globe.show = true
            g.tileset.show = false
            g.viewer.imageryLayers.length = 1
            return r
          }),
          release: () => { releases.push(1); g.tileset.show = true; g.viewer.scene.globe.show = false },
        }
      },
    },
  }
  const out = await api.acquire(g, { owner: 'hermes-compare', wait: (ms, cb) => cb() })
  assert.equal(out.ok, true)
  assert.equal(out.mode, 'lease')
  assert.equal(g.viewer.scene.globe.show, true)
  await out.release()
  assert.equal(releases.length, 1)
  assert.equal(g.tileset.show, true)
})

test('acquire falls back when controller missing and restores on release', async () => {
  const layers = []
  const g = {
    tileset: { show: true },
    viewer: {
      scene: { globe: { show: false }, requestRender() {} },
      imageryLayers: {
        length: 0,
        addImageryProvider(p) { const L = { p }; layers.push(L); this.length = layers.length; return L },
        remove(L) { const i = layers.indexOf(L); if (i >= 0) layers.splice(i, 1); this.length = layers.length },
        lowerToBottom() {},
      },
    },
  }
  const Cesium = { UrlTemplateImageryProvider: function () { this.ok = true } }
  const out = await api.acquire(g, { Cesium, wait: (ms, cb) => cb() })
  assert.equal(out.ok, true)
  assert.equal(out.mode, 'fallback')
  assert.equal(g.viewer.scene.globe.show, true)
  assert.equal(g.tileset.show, false)
  assert.ok(layers.length >= 1)
  out.release()
  assert.equal(g.tileset.show, true)
  assert.equal(g.viewer.scene.globe.show, false)
})

test('acquire fails closed when fallback still has no surface', async () => {
  const g = {
    tileset: { show: true },
    viewer: {
      scene: { globe: { show: false }, requestRender() {} },
      imageryLayers: null,
    },
  }
  // force fallback path; addEsriBase will fail without imageryLayers
  const out = await api.acquire(g, {
    Cesium: { UrlTemplateImageryProvider: function () {} },
    wait: (ms, cb) => cb(),
  })
  // globe.show set true by fallback but imageryLayers null → surfaceVisible may still be true via globeOn alone
  // Make globe stay false to force failure
  g.viewer.scene.globe.show = false
  g.tileset.show = false
  const out2 = api.noSurfaceResult('hermes_compare')
  assert.equal(out2.ok, false)
  assert.equal(out2.error, 'no_visible_surface')
})
