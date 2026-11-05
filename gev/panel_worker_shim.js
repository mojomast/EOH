(() => {
  const NativeBlob = window.Blob, nativeUrl = URL.createObjectURL.bind(URL), NativeWorker = window.Worker
  const classic = new Set(); let bundle = null
  const shim = /^import "(?:[^"]*\/Workers\/)?([\w-]+)(?:\.js)?";$/
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
})()
