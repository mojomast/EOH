/* Inlined into each generated scene; placeholders are replaced by build.mjs. */
(() => {
  const scene = document.getElementById('__SCENE_ID__');
  const tl = gsap.timeline({ paused: true });
  tl.fromTo(scene.querySelectorAll('.enter'), { opacity: 0, y: 25 },
    { opacity: 1, y: 0, duration: 0.65, stagger: 0.1, ease: 'power2.out' }, 0.08);
  const chat = scene.querySelector('.chat-bubble');
  if (chat) {
    const text = JSON.parse(chat.dataset.chat).join('  ');
    const target = chat.querySelector('.chat-text');
    const state = { characters: 0 };
    tl.to(state, { characters: text.length, duration: Math.min(3.4, __SCENE_DURATION__ - 1), ease: 'none',
      onUpdate: () => { target.textContent = text.slice(0, Math.floor(state.characters)); } }, 0.65);
  }
  scene.querySelectorAll('.counter').forEach(counter => {
    const raw = counter.dataset.count;
    if (!/^\d+$/.test(raw)) return;
    const state = { value: 0 };
    tl.to(state, { value: Number(raw), duration: 1.7, ease: 'power2.out',
      onUpdate: () => { counter.textContent = Math.round(state.value).toLocaleString('en-US'); } }, 0.5);
  });
  const nodes = scene.querySelectorAll('.arch-node, .arch-link');
  if (nodes.length) tl.fromTo(nodes, { opacity: 0, y: 14 },
    { opacity: 1, y: 0, duration: 0.4, stagger: 0.15, ease: 'power2.out' }, 0.6);
  const orbits = scene.querySelectorAll('.orbit');
  if (orbits.length) tl.fromTo(orbits, { rotation: 0 }, { rotation: 45, duration: __SCENE_DURATION__, ease: 'none' }, 0);
  tl.to({}, { duration: __SCENE_DURATION__ }, 0);
  window.__timelines = window.__timelines || {};
  window.__timelines['__SCENE_ID__'] = tl;
})();
