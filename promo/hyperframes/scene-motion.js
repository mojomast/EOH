/* Inlined into each sub-composition. All time is local, finite, and seekable. */
(() => {
  const scene = document.getElementById('__SCENE_ID__');
  const duration = __SCENE_DURATION__;
  const number = Number(scene.id.split('-')[1]);
  const tl = gsap.timeline({ paused: true });
  const all = selector => [...scene.querySelectorAll(selector)];
  const reveal = (selector, from, to, at) => {
    const elements = all(selector);
    if (elements.length) tl.fromTo(elements, from, to, at);
  };

  // Depth comes from separate focal planes, rather than moving the whole slide.
  reveal('.scene-bloom', { x: -90, y: 35, scale: .85, opacity: .35 },
    { x: 80, y: -25, scale: 1.12, opacity: 1, duration, ease: 'sine.inOut' }, 0);
  reveal('.chapter-ghost', { x: 90, opacity: 0 },
    { x: 0, opacity: 1, duration: 1.7, ease: 'expo.out' }, .12);
  reveal('.scene-hairline', { scaleX: 0, opacity: 0 },
    { scaleX: 1, opacity: 1, duration: 1.8, ease: 'power3.out' }, .2);

  // Per-word overflow masks produce an editorial roll-up, including the <em> line.
  reveal('.title-word', { yPercent: 115, rotationX: -32, opacity: 0 },
    { yPercent: 0, rotationX: 0, opacity: 1, duration: .92, stagger: .055, ease: 'expo.out' }, number === 13 ? .65 : .23);
  reveal('.eyebrow span', { y: 12, opacity: 0 },
    { y: 0, opacity: 1, duration: .55, stagger: .1, ease: 'power3.out' }, .15);
  reveal('.intro-line,.hook-copy p,.scene-copy p,.architecture-heading p,.stats-heading p', { y: 22, opacity: 0 },
    { y: 0, opacity: 1, duration: .75, ease: 'power3.out' }, .8);
  reveal('.side-rule', { scaleX: 0, transformOrigin: 'left' },
    { scaleX: 1, duration: .65, ease: 'expo.inOut' }, .75);
  reveal('.side-note', { y: 14, opacity: 0 },
    { y: 0, opacity: 1, duration: .65, ease: 'power3.out' }, 1);
  reveal('.vo-slot,.small-slot', { opacity: 0 }, { opacity: .84, duration: .5 }, 1.1);

  const reverse = [5, 9, 10].includes(number);
  reveal('.display-area,.hook-visual', {
    x: reverse ? -52 : 70, y: 24, scale: .965, rotationY: reverse ? 5 : -6,
    transformPerspective: 1600, transformOrigin: reverse ? 'right center' : 'left center', opacity: 0,
    clipPath: reverse ? 'inset(0 100% 0 0 round 12px)' : 'inset(0 0 0 100% round 12px)'
  }, { x: 0, y: 0, scale: 1, rotationY: 0, opacity: 1, clipPath: 'inset(0 0% 0 0% round 12px)', duration: 1.15, ease: 'expo.out' }, .12);
  reveal('.display-area', { scale: 1, y: 0 },
    { scale: 1.018, y: -5, duration: duration - 1.6, ease: 'sine.inOut', immediateRender: false }, 1.35);
  reveal('.hud-frame', { '--edge': 0 }, { '--edge': 1, duration: 1, ease: 'power3.inOut' }, .65);
  reveal('.hud-top span,.hud-bottom span,.pane-label,.chat-status,.chat-hint', { y: 8, opacity: 0 },
    { y: 0, opacity: 1, duration: .5, stagger: .055, ease: 'power2.out' }, .65);
  reveal('.slot-card > *', { y: 12, opacity: 0 },
    { y: 0, opacity: 1, duration: .65, stagger: .075, ease: 'power3.out' }, .72);
  reveal('.lower-number', { rotationX: -80, opacity: 0, transformPerspective: 600 },
    { rotationX: 0, opacity: 1, duration: .8, ease: 'back.out(1.35)' }, .65);
  reveal('.lower-third b,.lower-third small', { x: -24, opacity: 0 },
    { x: 0, opacity: 1, duration: .7, stagger: .1, ease: 'expo.out' }, .85);

  // Ambient motion is deliberately behind the missing-media labels. Supplied
  // footage has no reticle/scan decoration and is never cropped by a Ken Burns zoom.
  reveal('.slot-reticle', { rotation: -35 }, { rotation: 55, duration, ease: 'none' }, 0);
  reveal('.slot-scan', { left: '-20%' }, { left: '110%', duration: duration - 1.2, ease: 'sine.inOut' }, .6);
  reveal('.callout-ring', { scale: 1.8, opacity: 0 },
    { scale: 1, opacity: .8, duration: .8, ease: 'expo.out' }, 1.5);
  reveal('.callout-ring', { rotation: 0 }, { rotation: 65, duration: duration - 1.5, ease: 'none', immediateRender: false }, 1.5);
  reveal('.callout-arrow', { x: -25, y: 25, opacity: 0 },
    { x: 0, y: 0, opacity: 1, duration: .75, ease: 'back.out(1.3)' }, 1.5);

  // The hero and closing lockup share a drawn-on astrolabe with counter-rotating
  // SVG rings. svgOrigin keeps each rotor centred in its own 800x800 viewBox.
  reveal('.intro-visual', { scale: .76, rotationY: -18, rotationX: 8, opacity: 0, transformPerspective: 1800 },
    { scale: 1, rotationY: 0, rotationX: 0, opacity: 1, duration: 1.65, ease: 'expo.out' }, .08);
  reveal('.outro-optics', { scale: 1.24, opacity: 0 },
    { scale: 1, opacity: .32, duration: 1.8, ease: 'expo.out' }, .1);
  reveal('.optic-draw', { strokeDasharray: 1, strokeDashoffset: 1 },
    { strokeDashoffset: 0, duration: 1.8, ease: 'power3.inOut' }, .15);
  reveal('.optic-rotor', { rotation: -70, svgOrigin: '400 400' },
    { rotation: 65, duration, ease: 'power1.inOut' }, 0);
  reveal('.optic-ticks', { rotation: 15, svgOrigin: '400 400', opacity: 0 },
    { rotation: -18, opacity: .45, duration, ease: 'power1.out' }, 0);
  reveal('.optic-gimbal', { rotation: -30, svgOrigin: '400 400' },
    { rotation: 35, duration, ease: 'sine.inOut' }, 0);
  reveal('.brand-mark,.outro-mark', { scale: .75, y: 14, opacity: 0 },
    { scale: 1, y: 0, opacity: 1, duration: 1.25, ease: 'expo.out' }, .12);
  reveal('.repo-url', { y: 24, opacity: 0, clipPath: 'inset(0 100% 0 0)' },
    { y: 0, opacity: 1, clipPath: 'inset(0 0% 0 0)', duration: 1.05, ease: 'expo.out' }, 1.05);
  reveal('.catalog-line,.hackathon', { y: 14, opacity: 0 },
    { y: 0, opacity: 1, duration: .75, stagger: .18, ease: 'power3.out' }, 1.45);

  // Mock Hermes terminal: finish typing the prompt before the map transition (data-action-at),
  // then optionally type a Hermes reply while the footage plays.
  const pane = scene.querySelector('.chat-pane');
  const chat = scene.querySelector('.chat-bubble.chat-user') || scene.querySelector('.chat-bubble');
  if (chat) {
    reveal('.chat-bubble.chat-user, .chat-bubble:not(.chat-reply)', { scale: .94, y: 16, opacity: 0, transformOrigin: 'top left' },
      { scale: 1, y: 0, opacity: 1, duration: .55, ease: 'back.out(1.2)' }, .55);
    const lines = JSON.parse(chat.dataset.chat || '[]');
    const prompt = lines[0] || '';
    const reply = lines[1] || '';
    const target = chat.querySelector('.chat-text');
    const state = { characters: 0 };
    const typeStart = .85;
    const actionAt = Number(pane?.dataset.actionAt);
    const available = Number.isFinite(actionAt) && actionAt > typeStart + 0.4
      ? Math.max(actionAt - typeStart - 0.2, 1.2)
      : Math.min(Math.max(prompt.length * 0.022, 1.5), Math.max(duration - 2.2, 1.5));
    const typeDuration = Math.min(Math.max(prompt.length * 0.022, 1.4), available);
    tl.to(state, { characters: prompt.length, duration: typeDuration, ease: 'none',
      onUpdate: () => { target.textContent = prompt.slice(0, Math.floor(state.characters)); } }, typeStart);
    const blinks = Math.max(4, Math.floor(typeDuration / .36));
    reveal('.chat-user .cursor, .chat-bubble:not(.chat-reply) .cursor', { opacity: 1 }, { opacity: 0, duration: .36, repeat: blinks, yoyo: true, ease: 'steps(1)' }, typeStart);

    if (reply) {
      const replyEl = scene.querySelector('.chat-reply');
      const replyTarget = scene.querySelector('.chat-reply-text');
      if (replyEl && replyTarget) {
        const replyStart = typeStart + typeDuration + 0.35;
        reveal('.chat-reply', { scale: .94, y: 12, opacity: 0, transformOrigin: 'top left' },
          { scale: 1, y: 0, opacity: 1, duration: .45, ease: 'back.out(1.15)' }, replyStart);
        const rstate = { characters: 0 };
        const replyDur = Math.min(Math.max(reply.length * 0.018, 1.8), Math.max(duration - replyStart - 0.4, 1.5));
        tl.to(rstate, { characters: reply.length, duration: replyDur, ease: 'none',
          onUpdate: () => { replyTarget.textContent = reply.slice(0, Math.floor(rstate.characters)); } }, replyStart + 0.2);
        const rblinks = Math.max(4, Math.floor(replyDur / .36));
        reveal('.reply-cursor', { opacity: 1 }, { opacity: 0, duration: .36, repeat: rblinks, yoyo: true, ease: 'steps(1)' }, replyStart + 0.2);
      }
    }
  }

  // Trace the real architecture in order; the traveling packet is a schematic.
  reveal('.arch-node', { y: 40, rotationX: -16, opacity: 0, transformPerspective: 1000 },
    { y: 0, rotationX: 0, opacity: 1, duration: .85, stagger: .18, ease: 'expo.out' }, .7);
  reveal('.link-line', { scaleX: 0 }, { scaleX: 1, duration: .45, stagger: .25, ease: 'power3.out' }, 1.1);
  reveal('.link-arrow', { x: -12, opacity: 0 }, { x: 0, opacity: 1, duration: .45, stagger: .25, ease: 'power3.out' }, 1.2);
  reveal('.architecture-note', { y: 12, opacity: 0 }, { y: 0, opacity: 1, duration: .65 }, 1.65);
  const nodes = all('.arch-node');
  [2.1, 4.6].forEach(start => {
    nodes.forEach((node, index) => {
      tl.fromTo(node, { '--sheen': '0%' }, { '--sheen': '450%', duration: .8, ease: 'power2.inOut', immediateRender: false }, start + index * .42);
    });
    all('.link-packet').forEach((packet, index) => {
      const at = start + .28 + index * .42;
      tl.fromTo(packet, { x: 0, opacity: 0 }, { x: 40, opacity: 1, duration: .36, ease: 'none', immediateRender: false }, at);
      tl.to(packet, { opacity: 0, duration: .12 }, at + .36);
    });
  });
  reveal('.stat-card', { y: 55, scale: .94, rotationX: -12, opacity: 0, transformPerspective: 1200 },
    { y: 0, scale: 1, rotationX: 0, opacity: 1, duration: 1, stagger: .14, ease: 'expo.out' }, .7);
  reveal('.stat-card', { '--sheen': '0%' }, { '--sheen': '450%', duration: 1.25, stagger: .14, ease: 'power2.inOut' }, 1.5);
  all('.counter').forEach(counter => {
    const raw = counter.dataset.count;
    if (!/^\d+$/.test(raw)) return;
    const state = { value: 0 };
    tl.to(state, { value: Number(raw), duration: 1.7, ease: 'power3.out',
      onUpdate: () => { counter.textContent = Math.round(state.value).toLocaleString('en-US'); } }, 1);
  });

  tl.to({}, { duration }, 0);
  window.__timelines = window.__timelines || {};
  window.__timelines['__SCENE_ID__'] = tl;
})();
