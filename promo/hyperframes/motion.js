/* Root choreography; mounted sub-compositions own scene-local animation. */
(() => {
  const tl = gsap.timeline({ paused: true });
  const film = document.getElementById('film');
  const scenes = [...film.querySelectorAll('.scene-host')];
  const cells = [...film.querySelectorAll('.chapter-cell i')];
  tl.set('.transition-slice', { x: -2900, skewX: -17, visibility: 'visible' }, 0);
  tl.set('.shutter-top', { yPercent: -105, visibility: 'visible' }, 0);
  tl.set('.shutter-bottom', { yPercent: 105, visibility: 'visible' }, 0);
  tl.set('.transition-iris', { clipPath: 'circle(0% at 70% 48%)' }, 0);
  tl.set('.transition-ring', { opacity: 0 }, 0);

  scenes.forEach((scene, index) => {
    const start = Number(scene.dataset.start);
    const duration = Number(scene.dataset.duration);
    tl.fromTo(cells[index], { scaleX: 0 }, { scaleX: 1, duration, ease: 'none' }, start);
    if (index === 0) return;

    // Each cut closes onto a fully opaque frame exactly at the scene boundary.
    // The new scene's staggered entrances resolve as the transition opens again.
    if ([2, 6, 12].includes(index)) {
      tl.to('.transition-iris', { clipPath: 'circle(145% at 70% 48%)', duration: .28, ease: 'power3.in' }, start - .28);
      tl.to('.transition-iris', { clipPath: 'circle(0% at 70% 48%)', duration: .48, ease: 'power3.out' }, start);
      tl.fromTo('.transition-ring', { scale: 1, opacity: .6 },
        { scale: 7, opacity: 0, duration: .85, ease: 'expo.out', immediateRender: false }, start + .08);
    } else if ([1, 5, 9, 10].includes(index)) {
      tl.to('.shutter-top', { yPercent: 0, duration: .24, ease: 'power3.in' }, start - .24);
      tl.to('.shutter-bottom', { yPercent: 0, duration: .24, ease: 'power3.in' }, start - .24);
      tl.to('.shutter-top', { yPercent: -105, duration: .44, ease: 'expo.out' }, start);
      tl.to('.shutter-bottom', { yPercent: 105, duration: .44, ease: 'expo.out' }, start);
    } else {
      tl.fromTo('.transition-slice', { x: -2900 },
        { x: 0, duration: .28, ease: 'power3.in', immediateRender: false }, start - .28);
      tl.to('.transition-slice', { x: 2900, duration: .44, ease: 'power3.out' }, start);
    }
  });
  tl.to({}, { duration: Number(film.dataset.duration) }, 0);
  window.__timelines = window.__timelines || {};
  window.__timelines['eoh-promo'] = tl;
})();
