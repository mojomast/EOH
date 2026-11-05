// Headless shot of the Eye Of Hermes time slider. Listens on 127.0.0.1:4244.
// Writes a JPEG only when the page shows the EOH header and the slider brand.
import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const PORT = 4244
const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..')
const CHROME = process.env.CHROME_PATH || '/usr/bin/google-chrome'
const OUT = process.argv[2] || path.join('/tmp', 'eoh-timeline.jpg')

function pageHtml() {
  const brand = fs.readFileSync(path.join(ROOT, 'gev', 'panel_brand.js'), 'utf8')
  const slider = fs.readFileSync(path.join(ROOT, 'gev', 'panel_timeline.js'), 'utf8')
  return `<!doctype html>
<html><head><meta charset="utf-8"><title>GOD'S EYE VIEW</title>
<style>
  html, body { margin: 0; height: 100%; background: #071410; color: #d7ffe8; font-family: ui-monospace, Menlo, monospace; }
  header { display: flex; align-items: baseline; gap: 12px; padding: 14px 18px; }
  h1 { margin: 0; font-size: 22px; letter-spacing: .08em; }
  .title-accent { color: #ffe45a; }
  .subtitle { margin: 0; letter-spacing: .14em; font-size: 11px; opacity: .8; }
  #globe { position: absolute; inset: 72px 0 0 0; background:
    radial-gradient(ellipse at 50% 60%, #16382c 0%, #071410 70%); }
</style></head>
<body>
<header>
  <h1><span>GOD'S EYE <span class="title-accent">VIEW</span></span></h1>
  <p class="subtitle">NO PLACE LEFT BEHIND</p>
</header>
<div id="globe"></div>
<script>${brand}<\/script>
<script>
window.Cesium = {
  SplitDirection: { LEFT: -1, RIGHT: 1, NONE: 0 },
  UrlTemplateImageryProvider: class { constructor(o) { this.url = o.url } }
}
const imagery = []
window.__godsEyeView = {
  tileset: { show: true },
  viewer: {
    imageryLayers: {
      addImageryProvider() {
        const layer = { show: true, alpha: 1, splitDirection: 0 }
        imagery.push(layer)
        return layer
      },
      remove() {}
    },
    scene: { splitPosition: 0.5, requestRender() {} }
  }
}
<\/script>
<script>${slider}<\/script>
<script>
const releases = [
  { release: 13851, date: '2016-07-13', label: 'Esri Wayback 2016-07-13', url: 'https://wayback.maptiles.arcgis.com/tile/13851/{z}/{y}/{x}' },
  { release: 48376, date: '2020-04-08', label: 'Esri Wayback 2020-04-08', url: 'https://wayback.maptiles.arcgis.com/tile/48376/{z}/{y}/{x}' },
  { release: 56102, date: '2023-12-07', label: 'Esri Wayback 2023-12-07', url: 'https://wayback.maptiles.arcgis.com/tile/56102/{z}/{y}/{x}' }
]
window.__hermesTimeline.command({
  action: 'set', mode: 'wayback', transition: 'swipe',
  from: '2016-07-13', to: '2023-12-07', at: '2020-04-08', releases
})
window.__hermesTimeline.command({ action: 'position', position: 0.55 })
window.__ready = true
<\/script>
</body></html>`
}

function listen() {
  const html = pageHtml()
  const server = http.createServer((_req, res) => {
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
      cwd: HERE, stdio: ['ignore', 'pipe', 'inherit'],
    })
    if (result.stdout) process.stderr.write(result.stdout)
    if (result.status !== 0) process.exit(result.status || 1)
  }
  return createRequire(path.join(HERE, 'package.json'))('puppeteer-core')
}

async function main() {
  if (!fs.existsSync(CHROME)) {
    console.error('Chrome not found at ' + CHROME)
    process.exit(1)
  }
  const puppeteer = loadPuppeteer()
  const server = await listen()
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--hide-scrollbars']
  })
  try {
    const page = await browser.newPage()
    await page.setViewport({ width: 1100, height: 640, deviceScaleFactor: 1 })
    await page.goto('http://127.0.0.1:' + PORT + '/', { waitUntil: 'load', timeout: 15000 })
    await page.waitForFunction(() => window.__ready === true, { timeout: 10000 })
    const brand = await page.evaluate(() => {
      const header = document.querySelector('h1')?.innerText || ''
      const bar = document.getElementById('hermes-gev-timeline')
      const text = bar ? bar.innerText : ''
      return {
        header, bar: text, shown: bar ? getComputedStyle(bar).display !== 'none' : false,
        title: document.title
      }
    })
    if (!/EOH/.test(brand.header) || !/Eye Of Hermes/.test(brand.bar) || !brand.shown) {
      console.error(JSON.stringify(brand))
      process.exit(1)
    }
    await page.screenshot({ path: OUT, type: 'jpeg', quality: 80 })
    console.log(JSON.stringify({ ok: true, path: OUT, ...brand }))
  } finally {
    await browser.close()
    await new Promise((resolve) => server.close(resolve))
  }
}

main().catch((error) => {
  console.error(error && error.stack || error)
  process.exit(1)
})
