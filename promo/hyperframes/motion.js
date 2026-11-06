/* One survey-head transition carried across every locked cut. */
(() => {
  const film = document.getElementById('film');
  const scenes = [...film.querySelectorAll('.scene-host')];
  const sweep = film.querySelector('.survey-sweep');
  const clock = film.querySelector('.film-time');
  const cells = [...film.querySelectorAll('.chapter-cell i')];
  const tl = gsap.timeline({paused:true});
  scenes.forEach((scene,i) => tl.fromTo(cells[i],{scaleX:0},{scaleX:1,duration:Number(scene.dataset.duration),ease:'none'},Number(scene.dataset.start)));
  function update() {
    const t = tl.time();
    const current = scenes.findLast(scene => t >= Number(scene.dataset.start)) || scenes[0];
    film.style.setProperty('--chrome',current.dataset.stock === 'ink' ? '#E9E4D8' : '#0B0D0C');
    const cut = scenes.slice(1).find(scene => Math.abs(t-Number(scene.dataset.start)) < .28);
    sweep.style.visibility = cut ? 'visible' : 'hidden';
    if (cut) sweep.style.transform = `translateX(${2044*(t-Number(cut.dataset.start)+.28)/.56}px)`;
    const frame = Math.floor(t*30+1e-5);
    clock.textContent = [Math.floor(frame/1800),Math.floor(frame/30)%60,frame%30].map(n=>String(n).padStart(2,'0')).join(':');
  }
  tl.to({}, {duration:Number(film.dataset.duration)},0);
  tl.eventCallback('onUpdate',update);
  update();
  window.__timelines = window.__timelines || {};
  window.__timelines['eoh-promo'] = tl;
})();
