// Original vector ornament. These are abstract optical instruments, not map data.
// Generate stable markup at build time so every frame can be sought independently.
export function optics() {
  const ticks = Array.from({ length: 72 }, (_, index) => {
    const major = index % 6 === 0;
    return `<line x1="400" y1="${major ? 26 : 32}" x2="400" y2="${major ? 48 : 40}" transform="rotate(${index * 5} 400 400)"/>`;
  }).join('');
  return `<svg class="optics" viewBox="0 0 800 800" fill="none" aria-hidden="true" data-layout-ignore>
    <g class="optic-ticks">${ticks}</g>
    <circle class="optic-rim optic-draw" cx="400" cy="400" r="337" pathLength="1"/>
    <g class="optic-rotor"><circle cx="400" cy="400" r="354" stroke-dasharray="320 48 9 48"/><circle class="optic-gold" cx="400" cy="400" r="309" stroke-dasharray="160 480"/><path class="optic-pointer" d="M400 36 L392 54 L408 54 Z"/></g>
    <g class="optic-gimbal"><ellipse cx="400" cy="400" rx="279" ry="111"/><ellipse cx="400" cy="400" rx="111" ry="279"/><circle cx="400" cy="400" r="279"/></g>
    <g class="optic-crosshair"><path d="M400 100v32 M400 668v32 M100 400h32 M668 400h32"/><path class="optic-gold" d="M386 400h28 M400 386v28"/></g>
  </svg>`;
}

export function placeholderInstrument() {
  return `<div class="slot-instrument" aria-hidden="true" data-layout-ignore>
    <svg class="slot-reticle" viewBox="0 0 400 400" fill="none"><circle cx="200" cy="200" r="168"/><circle cx="200" cy="200" r="149" stroke-dasharray="60 174"/><path d="M200 8v32 M200 360v32 M8 200h32 M360 200h32"/></svg>
    <div class="slot-scan"></div><div class="slot-horizon"></div>
  </div>`;
}


/** Mock Hermes dark-mode chat pane. Typed by scene-motion.js from data-chat.
 *  Used as a designed overlay; real footage is the cropped GEV map pane only.
 *  Optional data-reply becomes a second bubble typed after the prompt.
 *  data-action-at marks when the map transition begins (prompt must finish first). */
export function mockHermesChat({ label = '01', prompt, reply = '', actionAt = '' }) {
  const lines = reply ? [prompt, reply] : [prompt];
  const dataChat = JSON.stringify(lines).replace(/&/g, '&amp;').replace(/'/g, '&#39;');
  const actionAttr = actionAt !== '' && actionAt != null ? ` data-action-at="${actionAt}"` : '';
  const replyBubble = reply
    ? `<div class="chat-bubble chat-reply" data-chat-reply="1"><span class="chat-role">HERMES</span><span class="chat-text chat-reply-text"></span><span class="cursor reply-cursor">▌</span></div>`
    : '';
  return `<div class="chat-pane"${actionAttr}><div class="pane-label">HERMES CHAT <span>${label}</span></div><div class="chat-status">LIVE SESSION</div><div class="chat-bubble chat-user" data-chat='${dataChat}'><span class="chat-role">YOU</span><span class="chat-text"></span><span class="cursor">▌</span></div>${replyBubble}<div class="chat-hint">Eye Of Hermes · GEV map pane</div></div>`;
}

export function decorateScene(body, number) {
  // Preserve emphasis, manual line breaks, and natural word wrapping in the source.
  body = body.replace(/<(h[12])>([\s\S]*?)<\/\1>/g, (_, tag, text) => {
    const words = text.split(/(<[^>]+>)/g).map(part => part.startsWith('<') ? part :
      part.replace(/\S+/g, word => `<span class="word-mask" data-layout-allow-overflow><span class="title-word">${word}</span></span>`)).join('');
    return `<${tag} data-motion-blur="off">${words}</${tag}>`;
  });
  body = body.replace(/<div\s+data-mock-hermes-chat\s+([^>]*)><\/div>/g, (_, attrs) => {
    const label = /data-label="([^"]*)"/.exec(attrs)?.[1] || '01';
    const prompt = /data-prompt="([^"]*)"/.exec(attrs)?.[1];
    const reply = /data-reply="([^"]*)"/.exec(attrs)?.[1] || '';
    const actionAt = /data-action-at="([^"]*)"/.exec(attrs)?.[1] || '';
    if (!prompt) throw new Error(`data-mock-hermes-chat missing data-prompt (label ${label})`);
    const decode = (s) => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
    return mockHermesChat({ label, prompt: decode(prompt), reply: decode(reply), actionAt });
  });
  body = body.replace('<!-- OPTICS -->', optics());
  body = body.replaceAll('<div class="arch-link">→</div>', '<div class="arch-link" aria-label="to"><span class="link-line"></span><span class="link-packet" aria-hidden="true"></span><span class="link-arrow">›</span></div>');
  return `<div class="scene-atmosphere" aria-hidden="true" data-layout-ignore><div class="scene-bloom"></div><span class="chapter-ghost">${number}</span><div class="scene-hairline"></div></div>${body}`;
}

export function chapterRail(scenes) {
  return `<div class="chapter-rail" aria-hidden="true" data-layout-ignore>${scenes.map(scene =>
    `<span class="chapter-cell" style="flex:${scene.duration}"><i></i></span>`).join('')}</div>`;
}
