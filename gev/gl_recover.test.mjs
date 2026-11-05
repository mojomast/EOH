import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const GL = require('./gl_recover.js')

function lostEvent(canvas) {
  return { target: canvas, prevented: false, preventDefault() { this.prevented = true } }
}

test('recovery is on unless the flag is an explicit off', () => {
  assert.equal(GL.MAX_FAILURES, 3)
  assert.equal(GL.WINDOW_MS, 20000)
  assert.equal(GL.recoverEnabled(undefined), true)
  assert.equal(GL.recoverEnabled(''), true)
  assert.equal(GL.recoverEnabled('1'), true)
  assert.equal(GL.recoverEnabled(true), true)
  for (const off of ['0', 'false', 'off', 'no', false]) assert.equal(GL.recoverEnabled(off), false)
  assert.equal(GL.backoffMs(1), 50)
  assert.equal(GL.backoffMs(2), 200)
  assert.equal(GL.backoffMs(3), 400)
})

test('failure budget forgets losses outside the window', () => {
  let now = 0
  const budget = GL.createFailureBudget({ max: 3, windowMs: 1000, now: () => now })
  assert.equal(budget.record().exhausted, false)
  assert.equal(budget.record().exhausted, false)
  assert.equal(budget.record().exhausted, true)
  now = 5000
  assert.equal(budget.record().exhausted, false)
})

test('restore commands put the camera, pins, layers, and drawer back', () => {
  const state = { camera: { lat: 0, lon: 0 }, pins: [{ id: 'old', latitude: 0, longitude: 0, label: 'old' }] }
  const applied = GL.applyRestoreCommands(state, [
    ['fly_to_location', { latitude: 35.6895, longitude: 139.6917, rangeM: 2500 }],
    ['hermes_frame', { pitchDeg: -45, headingDeg: 120, rollDeg: 12, rangeM: 2500 }],
    ['set_layer_visibility', { layerId: 'flights', enabled: true }],
    ['clear_annotations', {}],
    ['annotate_map', { annotations: [
      { label: 'Tokyo', latitude: 35.6895, longitude: 139.6917 },
      { label: 'Port', latitude: 35.62, longitude: 139.77 }
    ] }],
    ['hermes_pin_filter', { categories: ['ramen'], open_now: true }],
    ['hermes_pin_drawer', { action: 'open', latitude: 35.6895, longitude: 139.6917, label: 'Tokyo' }]
  ])
  assert.equal(applied.camera.headingDeg, 120)
  assert.equal(applied.camera.pitchDeg, -45)
  assert.equal(applied.camera.rollDeg, 12)
  assert.equal(applied.camera.lat, 35.6895)
  assert.deepEqual(applied.pins.map((p) => p.label), ['Tokyo', 'Port'])
  assert.deepEqual(applied.layers, ['flights'])
  assert.equal(applied.filters.open_now, true)
  assert.equal(applied.drawer.label, 'Tokyo')
  assert.equal(state.pins.some((p) => p.label === 'old'), false)
})

test('restore commands put a saved time slider back, paused', () => {
  const applied = GL.applyRestoreCommands({}, [
    ['hermes_timeline', { action: 'set', mode: 'wayback', from: '2016-07-13', to: '2023-12-07', at: '2020-04-08', playing: true, transition: 'swipe', index: 2 }]
  ])
  assert.equal(applied.timeline.mode, 'wayback')
  assert.equal(applied.timeline.at, '2020-04-08')
  assert.equal(applied.timeline.playing, false)
  assert.equal(applied.timeline.transition, 'swipe')
})

test('intentional dispose loses the context before destroy and ignores a later loss', () => {
  const order = []
  const viewer = { canvas: { id: 'c' }, epoch: 1 }
  const ctl = GL.attach({
    listen: false,
    getViewer: () => viewer,
    setViewer() {},
    releaseContext() { order.push('lose') },
    destroyViewer() { order.push('destroy') },
    enabled: () => true,
    createViewer: async () => { throw new Error('should not recreate') },
    getScene: async () => [],
    applyScene: async () => ({})
  })
  ctl.dispose()
  ctl.dispose()
  assert.deepEqual(order, ['lose', 'destroy'])
  ctl.onContextLost(lostEvent(viewer.canvas))
  assert.deepEqual(order, ['lose', 'destroy'])
})

test('a lost context recreates the viewer and restores the scene', async () => {
  const order = []
  let viewer = { canvas: { id: 'old' }, epoch: 1 }
  let restored = null
  const commands = [
    ['hermes_frame', { headingDeg: 10, pitchDeg: -20, rollDeg: 3, rangeM: 100 }],
    ['annotate_map', { annotations: [{ latitude: 1, longitude: 2, label: 'A' }] }]
  ]
  const ctl = GL.attach({
    listen: false,
    delay: async () => { order.push('wait') },
    getViewer: () => viewer,
    setViewer(next) { viewer = next },
    containerOf: () => ({ id: 'same' }),
    releaseContext(prev) { order.push('lose:' + prev.epoch) },
    destroyViewer(prev) { order.push('destroy:' + prev.epoch) },
    createViewer: async () => ({ canvas: { id: 'new' }, epoch: 2, state: { camera: {}, pins: [], layers: [], shapes: [] } }),
    getScene: async () => commands,
    applyScene: async (next, cmds) => GL.applyRestoreCommands(next.state, cmds),
    onLost() { order.push('lost') },
    onRecovered(applied) { order.push('recovered'); restored = applied },
    onGiveUp() { order.push('giveup') }
  })
  await ctl.onContextLost(lostEvent(viewer.canvas))
  assert.equal(order[0], 'lost')
  assert.ok(order.indexOf('lose:1') < order.indexOf('destroy:1'))
  assert.equal(order.at(-1), 'recovered')
  assert.equal(viewer.epoch, 2)
  assert.equal(restored.camera.rollDeg, 3)
  assert.equal(restored.pins[0].label, 'A')
  assert.equal(order.includes('giveup'), false)
})

test('the flag off shows the fallback and does not recreate', async () => {
  let created = 0
  let gave = null
  const viewer = { canvas: { id: 'c' } }
  const ctl = GL.attach({
    listen: false,
    enabled: () => false,
    getViewer: () => viewer,
    setViewer() {},
    releaseContext() {},
    destroyViewer() {},
    createViewer: async () => { created += 1; return { canvas: {} } },
    getScene: async () => [],
    applyScene: async () => ({}),
    onGiveUp(info) { gave = info }
  })
  await ctl.onContextLost(lostEvent(viewer.canvas))
  assert.equal(created, 0)
  assert.equal(gave.auto, false)
  assert.equal(gave.exhausted, false)
})

test('three losses in the window stop and show the card', async () => {
  let created = 0
  let gave = 0
  let viewer = { canvas: { id: 'a' }, epoch: 0 }
  const ctl = GL.attach({
    listen: false,
    delay: async () => {},
    getViewer: () => viewer,
    setViewer(next) { viewer = next },
    containerOf: () => ({}),
    releaseContext() {},
    destroyViewer() {},
    createViewer: async () => {
      created += 1
      return { canvas: { id: 'n' + created }, epoch: created, state: { camera: {}, pins: [], layers: [], shapes: [] } }
    },
    getScene: async () => [],
    applyScene: async (next) => GL.applyRestoreCommands(next.state, []),
    onLost() {},
    onRecovered() {},
    onGiveUp() { gave += 1 }
  })
  await ctl.onContextLost(lostEvent(viewer.canvas))
  await ctl.onContextLost(lostEvent(viewer.canvas))
  await ctl.onContextLost(lostEvent(viewer.canvas))
  assert.equal(created, 2)
  assert.equal(gave, 1)
  const createdAfter = created
  await ctl.onContextLost(lostEvent(viewer.canvas))
  assert.equal(created, createdAfter)
})

test('a viewer that cannot be created backs off and then stops', async () => {
  let created = 0
  let gave = null
  const viewer = { canvas: { id: 'a' } }
  const ctl = GL.attach({
    listen: false,
    delay: async () => {},
    getViewer: () => viewer,
    setViewer() {},
    containerOf: () => ({}),
    releaseContext() {},
    destroyViewer() {},
    createViewer: async () => { created += 1; throw new Error('no gpu') },
    getScene: async () => [],
    applyScene: async () => ({}),
    onLost() {},
    onRecovered() { throw new Error('should not recover') },
    onGiveUp(info) { gave = info }
  })
  await ctl.onContextLost(lostEvent(viewer.canvas))
  assert.equal(created, 2)
  assert.equal(gave.exhausted, true)
  assert.match(gave.error, /no gpu/)
})
