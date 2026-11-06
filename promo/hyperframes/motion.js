/* The root timeline drives transitions; each mounted scene owns its motion. */
(() => {
  const tl = gsap.timeline({ paused: true });
  tl.set(['.transition-iris', '.transition-wipe'], { opacity: 0 }, 0);
  const starts = [6, 11, 21, 28, 35, 42, 49, 56, 63, 70, 78, 83];
  starts.forEach((start, index) => {
    if ((index + 1) % 3 === 0) {
      tl.fromTo('.transition-iris', { scale: 0.6, opacity: 0.8 },
        { scale: 14, opacity: 0, duration: 0.6, ease: 'power2.out', immediateRender: false }, start);
    } else {
      tl.fromTo('.transition-wipe', { x: 0, opacity: 0 },
        { x: 2260, opacity: 0.75, duration: 0.42, ease: 'power2.inOut', immediateRender: false }, start - 0.1);
      tl.set('.transition-wipe', { opacity: 0 }, start + 0.34);
    }
  });
  tl.to({}, { duration: 89 }, 0);
  window.__timelines = window.__timelines || {};
  window.__timelines['eoh-promo'] = tl;
})();
