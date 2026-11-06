// Headless check: force WEBGL_lose_context and require the viewer to come back
// in the same container with the camera and pins from SceneStore.restore_commands.
// Listens on 127.0.0.1:4233 only. SwiftShader is used when the machine has no GPU.
import { spawnSync, execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const PORT = 4233
const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')

function defaultLookup(name) {
  try {
    const found = execFileSync('which', [name], { encoding: 'utf8' }).trim()
    return found || ''
  } catch {
    return ''
  }
}

/** Chrome when it is installed, otherwise Chromium. CHROME_PATH wins. */
export function resolveChrome(env = process.env, exists = fs.existsSync, lookup = defaultLookup) {
  if (env.CHROME_PATH) return env.CHROME_PATH
  const candidates = [
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ]
  for (const candidate of candidates) {
    if (exists(candidate)) return candidate
  }
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    const found = lookup(name)
    if (found) return found
  }
  return ''
}

const CHROME = resolveChrome()

function commandsFromPython() {
  const py = spawnSync('python3', ['-c', [
    'import json',
    'from gev.gl_recover import fixture_commands',
    'print(json.dumps(fixture_commands()))'
  ].join('\n')], { cwd: ROOT, encoding: 'utf8' })
  if (py.status !== 0) {
    console.error(py.stderr || py.stdout)
    process.exit(py.status || 1)
  }
  return JSON.parse(py.stdout)
}

function expectFrom(commands) {
  const fly = commands.find((cmd) => cmd[0] === 'fly_to_location')[1]
  const frame = commands.find((cmd) => cmd[0] === 'hermes_frame')[1]
  const pins = []
  for (const cmd of commands) {
    if (cmd[0] !== 'annotate_map') continue
    for (const spec of cmd[1].annotations || []) {
      pins.push({ latitude: spec.latitude, longitude: spec.longitude, label: spec.label || '' })
    }
  }
  const layers = commands.filter((cmd) => cmd[0] === 'set_layer_visibility' && cmd[1].enabled !== false)
    .map((cmd) => cmd[1].layerId)
  const filter = commands.find((cmd) => cmd[0] === 'hermes_pin_filter')[1]
  const drawer = commands.find((cmd) => cmd[0] === 'hermes_pin_drawer')[1]
  return { fly, frame, pins, layers, filter, drawer }
}

function pageHtml(commands, expect) {
  const payload = JSON.stringify({ commands, expect }).replace(/</g, '\\u003c')
  return `<!doctype html>
<html><head><meta charset="utf-8"><title>gl-recover</title></head>
<body>
<div id="cesiumContainer"></div>
<script src="/gl_recover.js"></script>
<script>
const payload = ${payload}
const commands = payload.commands
const expect = payload.expect
const GL = window.__hermesGlRecover
const container = document.getElementById('cesiumContainer')
let epoch = 0
let viewer = null
let ctl = null
let firstGl = null
const notices = []
function makeViewer(el) {
  epoch += 1
  const canvas = document.createElement('canvas')
  canvas.width = 64
  canvas.height = 64
  el.appendChild(canvas)
  const gl = canvas.getContext('webgl', { failIfMajorPerformanceCaveat: false })
    || canvas.getContext('experimental-webgl')
  if (!gl) throw new Error('no webgl')
  gl.clearColor(0.05, 0.12, 0.08, 1)
  gl.clear(gl.COLOR_BUFFER_BIT)
  const state = { camera: {}, pins: [], layers: [], shapes: [] }
  const view = {
    epoch,
    container: el,
    canvas,
    scene: { context: { _gl: gl }, canvas, requestRender() {} },
    state,
    _dead: false,
    isDestroyed() { return view._dead },
    destroy() { view._dead = true; try { canvas.remove() } catch (e) {} }
  }
  canvas.addEventListener('webglcontextlost', (event) => ctl.onContextLost(event))
  return view
}
function finish(ok, extra) {
  const gl = viewer && viewer.scene.context._gl
  const camera = (viewer && viewer.state && viewer.state.camera) || {}
  const pins = (viewer && viewer.state && viewer.state.pins) || []
  const result = {
    ok,
    error: extra && extra.error || null,
    epoch: viewer && viewer.epoch,
    oldLost: !!(firstGl && firstGl.isContextLost && firstGl.isContextLost()),
    newLost: !!(gl && gl.isContextLost && gl.isContextLost()),
    canvases: container.querySelectorAll('canvas').length,
    sameContainer: viewer && viewer.container === container,
    camera,
    pins: pins.map((p) => ({ latitude: p.latitude, longitude: p.longitude, label: p.label })),
    layers: (viewer && viewer.state && viewer.state.layers) || [],
    filters: (viewer && viewer.state && viewer.state.filters) || null,
    drawer: (viewer && viewer.state && viewer.state.drawer) || null,
    notices: notices.slice()
  }
  if (ok) {
    const problems = []
    if (result.epoch < 2) problems.push('viewer was not recreated')
    if (!result.oldLost) problems.push('original context was not lost')
    if (result.newLost) problems.push('new context is lost')
    if (result.canvases !== 1) problems.push('canvas count ' + result.canvases)
    if (!result.sameContainer) problems.push('container changed')
    if (camera.headingDeg !== expect.frame.headingDeg) problems.push('heading')
    if (camera.pitchDeg !== expect.frame.pitchDeg) problems.push('pitch')
    if (camera.rollDeg !== expect.frame.rollDeg) problems.push('roll')
    if (camera.lat !== expect.fly.latitude || camera.lon !== expect.fly.longitude) problems.push('look-at')
    if (camera.rangeM !== expect.frame.rangeM) problems.push('range')
    if (JSON.stringify(result.pins) !== JSON.stringify(expect.pins)) problems.push('pins ' + JSON.stringify(result.pins))
    if (JSON.stringify(result.layers) !== JSON.stringify(expect.layers)) problems.push('layers')
    if (!result.filters || result.filters.open_now !== true) problems.push('filters')
    if (!result.drawer || result.drawer.label !== expect.drawer.label) problems.push('drawer')
    if (notices[0] !== 'lost' || notices.at(-1) !== 'recovered') problems.push('notices ' + notices.join(','))
    if (problems.length) {
      result.ok = false
      result.error = problems.join('; ')
    }
  }
  window.__result = result
  window.__done = true
  document.title = result.ok ? 'recovered' : 'failed'
}
try {
  viewer = makeViewer(container)
  firstGl = viewer.scene.context._gl
  GL.applyRestoreCommands(viewer.state, commands)
  ctl = GL.attach({
    listen: false,
    enabled: () => true,
    getViewer: () => viewer,
    setViewer(next) { viewer = next },
    containerOf: () => container,
    releaseContext(prev) {
      try {
        const gl = prev && prev.scene.context._gl
        gl && gl.getExtension('WEBGL_lose_context') && gl.getExtension('WEBGL_lose_context').loseContext()
      } catch (e) {}
    },
    destroyViewer(prev) { try { prev && prev.destroy() } catch (e) {} },
    createViewer() { return makeViewer(container) },
    getScene: async () => commands,
    applyScene(next, cmds) { return GL.applyRestoreCommands(next.state, cmds) },
    onLost() { notices.push('lost') },
    onRecovered() { notices.push('recovered'); finish(true) },
    onGiveUp(info) { notices.push('giveup'); finish(false, { error: 'gave up ' + JSON.stringify(info) }) }
  })
  window.__ready = true
  window.__lose = () => {
    const gl = viewer.scene.context._gl
    const ext = gl.getExtension('WEBGL_lose_context')
    if (!ext) throw new Error('WEBGL_lose_context is missing')
    ext.loseContext()
  }
} catch (error) {
  window.__result = { ok: false, error: String(error && error.message || error) }
  window.__done = true
  window.__ready = true
}
</script>
</body></html>`
}

function listen() {
  const js = fs.readFileSync(path.join(ROOT, 'gev', 'gl_recover.js'))
  const commands = commandsFromPython()
  const expect = expectFrom(commands)
  const html = pageHtml(commands, expect)
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/gl_recover.js')) {
      res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' })
      res.end(js)
      return
    }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    res.end(html)
  })
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(PORT, '127.0.0.1', () => resolve(server))
  })
}

function loadPuppeteer() {
  const installed = path.join(HERE, 'node_modules', 'puppeteer-core', 'package.json')
  if (!fs.existsSync(installed)) {
    const result = spawnSync('npm', ['install', '--no-fund', '--no-audit'], {
      cwd: HERE,
      stdio: ['ignore', 'pipe', 'inherit'],
    })
    if (result.stdout) process.stderr.write(result.stdout)
    if (result.status !== 0) process.exit(result.status || 1)
  }
  return createRequire(path.join(HERE, 'package.json'))('puppeteer-core')
}

async function main() {
  if (!CHROME || !fs.existsSync(CHROME)) {
    console.error('Chrome or Chromium not found. Set CHROME_PATH or install chromium.')
    process.exit(1)
  }
  const puppeteer = loadPuppeteer()
  const server = await listen()
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-dev-shm-usage',
      '--use-gl=angle',
      '--use-angle=swiftshader',
      '--enable-unsafe-swiftshader',
      '--ignore-gpu-blocklist',
      '--enable-webgl'
    ]
  })
  try {
    const page = await browser.newPage()
    const logs = []
    page.on('console', (msg) => logs.push(msg.type() + ' ' + msg.text()))
    page.on('pageerror', (err) => logs.push('pageerror ' + err.message))
    await page.goto('http://127.0.0.1:' + PORT + '/', { waitUntil: 'load', timeout: 15000 })
    await page.waitForFunction(() => window.__ready === true, { timeout: 10000 })
    const armed = await page.evaluate(() => {
      if (typeof window.__lose !== 'function') return window.__result || { ok: false, error: 'harness did not arm' }
      window.__lose()
      return null
    })
    if (armed) throw new Error(JSON.stringify(armed))
    await page.waitForFunction(() => window.__done === true, { timeout: 15000 })
    const result = await page.evaluate(() => window.__result)
    if (!result || !result.ok) {
      console.error(JSON.stringify({ result, logs }, null, 2))
      process.exit(1)
    }
    console.log(JSON.stringify({
      ok: true,
      port: PORT,
      epoch: result.epoch,
      camera: result.camera,
      pins: result.pins,
      layers: result.layers,
      oldLost: result.oldLost,
      newLost: result.newLost
    }))
  } finally {
    await browser.close()
    await new Promise((resolve) => server.close(resolve))
  }
}

const invoked = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
if (invoked) {
  main().catch((error) => {
    console.error(error && error.stack || error)
    process.exit(1)
  })
}
