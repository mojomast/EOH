// Bespoke survey graphics: mathematical graticules and the EOH winged-eye motif.
// No contour is represented as geographical terrain or live telemetry.
export const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[char]);

export function eye() {
  const wings = Array.from({ length: 5 }, (_, i) => {
    const d = i * 13;
    return `<path class="eye-contour draw" pathLength="1" d="M${28+d} 220 Q${172+d} ${198-d} 320 ${78+d} M${28+d} 220 Q${172+d} ${242+d} 320 ${362-d} M${772-d} 220 Q${628-d} ${198-d} 480 ${78+d} M${772-d} 220 Q${628-d} ${242+d} 480 ${362-d}"/>`;
  }).join('');
  return `<svg class="eye-mark" viewBox="0 0 800 440" fill="none" role="img" aria-label="Eye Of Hermes winged-eye survey mark"><g>${wings}</g><g class="eye-globe"><circle class="draw" pathLength="1" cx="400" cy="220" r="118"/><ellipse class="draw" pathLength="1" cx="400" cy="220" rx="59" ry="118"/><ellipse class="draw" pathLength="1" cx="400" cy="220" rx="118" ry="59"/><path class="draw" pathLength="1" d="M282 220H518M400 102V338"/></g><path class="register-mark draw" pathLength="1" d="M0 220h18m764 0h18M400 40v32m0 296v32"/></svg>`;
}

export function proofPlate() {
  // Only emitted by the explicit --proof build, never as a delivery fallback.
  const parallels = [-60, -30, 0, 30, 60].map(deg => {
    const rad = deg * Math.PI / 180;
    const y = 369 - 295 * Math.sin(rad);
    const radius = 295 * Math.cos(rad);
    return `<path class="draw" pathLength="1" d="M${656-radius} ${y}H${656+radius}"/><text x="${676+radius}" y="${y+5}">${Math.abs(deg)}°${deg > 0 ? 'N' : deg < 0 ? 'S' : ''}</text>`;
  }).join('');
  return `<div class="proof-plate"><svg viewBox="0 0 1312 738" fill="none" aria-label="Reference graticule, graphic study"><path class="proof-grid" d="M0 123H1312M0 246H1312M0 369H1312M0 492H1312M0 615H1312M218 0V738M437 0V738M656 0V738M875 0V738M1094 0V738"/><g class="proof-globe"><circle class="draw" pathLength="1" cx="656" cy="369" r="295"/><ellipse class="draw" pathLength="1" cx="656" cy="369" rx="147.5" ry="295"/><ellipse class="draw" pathLength="1" cx="656" cy="369" rx="255.48" ry="295"/>${parallels}<path class="draw" pathLength="1" d="M656 74V664"/></g><path class="reference-cross draw" pathLength="1" d="M636 369h40M656 349v40"/></svg><span class="proof-label">REFERENCE GRATICULE</span><span class="proof-note">ORTHOGRAPHIC / GRAPHIC STUDY</span></div>`;
}

function coordinate(value, axis) {
  const suffix = axis === 'lat' ? (value < 0 ? 'S' : 'N') : (value < 0 ? 'W' : 'E');
  return `${Math.abs(value).toFixed(4)}° ${suffix}`;
}

function terminal(attrs) {
  const get = name => new RegExp(`${name}="([^"]*)"`).exec(attrs)?.[1] || '';
  const prompt = get('data-prompt');
  const reply = get('data-reply');
  return `<aside class="terminal${reply ? ' with-reply' : ''}" data-action-at="${get('data-action-at')}"><div class="terminal-heading"><span>HERMES / COMMAND</span><span class="prompt-glyph">↳</span></div><div class="terminal-user"><span class="terminal-role">YOU</span><p class="typed-prompt" data-text="${prompt}"></p><span class="cursor">▌</span></div>${reply ? `<div class="terminal-reply"><span class="terminal-role">HERMES / SIMULATED REPLY</span><p class="typed-reply" data-text="${reply}"></p></div>` : ''}<div class="terminal-foot"><span>ASK → SEE</span><i class="command-rule"></i></div></aside>`;
}

export function decorateScene(body, number, location) {
  body = body.replaceAll('<span class="title-stop">', '<span class="title-stop" aria-hidden="true" data-layout-ignore>');
  body = body.replaceAll('<!-- EYE -->', eye());
  body = body.replace(/<div data-terminal ([^>]*)><\/div>/g, (_, attrs) => terminal(attrs));
  body = body.replace(/<(h[12])>([\s\S]*?)<\/\1>/g, (_, tag, text) => `<${tag}>${text.split('<br>').map(line => `<span class="title-mask"><span class="title-line">${line}</span></span>`).join('')}</${tag}>`);
  if (location) {
    body += `<div class="coordinates"><span>${escapeHtml(location.reference)}</span><div><b class="coordinate-value">${coordinate(location.lat, 'lat')}</b><b class="coordinate-value">${coordinate(location.lon, 'lon')}</b></div></div>`;
    body = body.replace(/<div class="plate" data-caption="([^"]*)">([\s\S]*?)<\/div>/, (_, caption, media) => `<div class="plate"><div class="plate-heading"><span>${escapeHtml(location.name)}</span><span>PLATE ${number}</span></div><div class="media-window">${media}</div><div class="plate-caption"><span>${caption}</span><span>GEV</span></div><div class="corner-ticks" aria-hidden="true" data-layout-ignore></div><div class="plate-scale"><span>FRAME / %</span><i></i><span>0</span><span>50</span><span>100</span></div></div>`);
  }
  return `<div class="paper-texture" aria-hidden="true" data-layout-ignore></div><div class="registration" aria-hidden="true" data-layout-ignore></div>${body}`;
}

export function chapterRail(scenes) {
  return `<div class="chapter-rail" aria-label="Film progress">${scenes.map(scene => `<span class="chapter-cell" style="flex:${scene.duration}"><i></i></span>`).join('')}</div>`;
}
