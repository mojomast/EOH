/* Scene-local, absolute-time choreography. HyperFrames owns media playback. */
(() => {
  const scene = document.getElementById('__SCENE_ID__');
  const duration = __SCENE_DURATION__;
  const number = Number('__SCENE_ID__'.split('-')[1]);
  const all = selector => [...scene.querySelectorAll(selector)];
  const surveyOut = t => 1 - Math.pow(1 - t, 5);
  const pen = t => t * t * (3 - 2 * t);
  const tl = gsap.timeline({paused:true});
  const reveal = (selector, from, to, at) => {
    const nodes = all(selector);
    if (nodes.length) tl.fromTo(nodes, from, {force3D:false,...to}, at);
  };
  reveal('.title-line', {yPercent:115}, {yPercent:0, duration:.8, stagger:.085, ease:surveyOut}, number === 13 ? .2 : .12);
  reveal('.eyebrow,.scene-copy p,.plate-heading,.hero-index,.outro-signature', {clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0% 0 0)', duration:.65, stagger:.09, ease:surveyOut}, .2);
  reveal('.scene-copy', {'--underline':0}, {'--underline':1, duration:.65, ease:pen}, .72);
  reveal('.plate-caption,.plate-scale', {clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0% 0 0)', duration:.75, stagger:.08, ease:surveyOut}, .35);
  // The media window is stationary and always fully open: preserve original lead-ins.
  reveal('.terminal', {clipPath:'inset(0 0 100% 0)'}, {clipPath:'inset(0 0 0% 0)', duration:.46, ease:surveyOut}, .1);
  reveal('.hero-rule', {scaleX:0}, {scaleX:1, duration:.65, ease:pen}, 2.397);
  all('.hero-tagline span').forEach((node, i) => tl.fromTo(node, {clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0% 0 0)', duration:.6, ease:surveyOut}, [2.397,3.784][i]));
  reveal('.eye-contour', {strokeDashoffset:1}, {strokeDashoffset:0, duration:1.2, stagger:.08, ease:pen}, .05);
  reveal('.eye-globe .draw,.register-mark', {strokeDashoffset:1}, {strokeDashoffset:0, duration:1.1, stagger:.06, ease:pen}, .4);
  reveal('.proof-globe .draw,.reference-cross', {strokeDashoffset:1}, {strokeDashoffset:0, duration:1.3, stagger:.025, ease:pen}, .05);

  const coordinates = all('.coordinate-value');
  coordinates.forEach(node => {
    const text = node.textContent;
    node.textContent = '';
    [...text].forEach(char => {
      const digit = document.createElement('span');
      digit.className = 'coordinate-digit';
      digit.textContent = char === ' ' ? '\u00a0' : char;
      node.appendChild(digit);
    });
  });
  reveal('.coordinate-digit', {clipPath:'inset(100% 0 0 0)', y:5}, {clipPath:'inset(0% 0 0 0)', y:0, duration:.25, stagger:.022, ease:surveyOut}, .35);

  const pane = scene.querySelector('.terminal');
  const target = scene.querySelector('.typed-prompt');
  const replyTarget = scene.querySelector('.typed-reply');
  const cursor = scene.querySelector('.cursor');
  const prompt = target?.dataset.text || '';
  const reply = replyTarget?.dataset.text || '';
  const actionAt = Number(pane?.dataset.actionAt);
  const typeStart = .85;
  // This is the original v6 formula; all nine completion times are locked by QC.
  const available = Number.isFinite(actionAt) && actionAt > typeStart + .4
    ? Math.max(actionAt - typeStart - .2, 1.2)
    : Math.min(Math.max(prompt.length * .022, 1.5), Math.max(duration - 2.2, 1.5));
  const typeDuration = Math.min(Math.max(prompt.length * .022, 1.4), available);
  const typeEnd = typeStart + typeDuration;
  const replyStart = typeEnd + .55;
  const replyDuration = Math.min(Math.max(reply.length * .018, 1.8), Math.max(duration - (typeEnd + .35) - .4, 1.5));
  if (pane) pane.dataset.typeEnd = typeEnd;
  reveal('.command-rule', {scaleX:0}, {scaleX:1, duration:.35, ease:pen}, typeEnd);
  if (reply) reveal('.terminal-reply', {clipPath:'inset(0 0 100% 0)'}, {clipPath:'inset(0 0 0% 0)', duration:.35, ease:surveyOut}, typeEnd+.35);
  const clamp = x => Math.min(1, Math.max(0,x));
  function updateText() {
    const t = tl.time();
    if (target) target.textContent = prompt.slice(0, Math.floor(prompt.length * clamp((t-typeStart)/typeDuration)));
    if (replyTarget) replyTarget.textContent = reply.slice(0, Math.floor(reply.length * clamp((t-replyStart)/replyDuration)));
    if (cursor) cursor.style.visibility = t >= typeStart && t < typeEnd && Math.floor((t-typeStart)/.3)%2 === 0 ? 'visible' : 'hidden';
  }

  all('.arch-node').forEach((node, i) => {
    const at = [.2,1.05,2.034,3.3][i];
    tl.fromTo(node, {clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0% 0 0)',duration:.65,ease:surveyOut},at);
  });
  all('.arch-link').forEach((node,i) => tl.fromTo(node,{scaleX:0},{scaleX:1,duration:.6,ease:pen},[.8,1.7,2.9][i]));
  reveal('.architecture-note', {clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0% 0 0)', duration:.8,ease:surveyOut},4.1);
  reveal('.stat-value', {clipPath:'inset(100% 0 0 0)'}, {clipPath:'inset(0% 0 0 0)',duration:.8,ease:surveyOut},.2);
  reveal('.tool-stat>div,.stat-index>div', {clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0% 0 0)',duration:.65,stagger:.13,ease:surveyOut},1.2);
  reveal('.outro-copy>p', {clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0% 0 0)',duration:.7,ease:surveyOut},1.363);
  reveal('.repo-lockup', {clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0% 0 0)',duration:.75,ease:surveyOut},2.1);
  reveal('.repo-lockup i', {scaleX:0}, {scaleX:1,duration:.75,ease:pen},4.38);
  tl.to({}, {duration}, 0);
  tl.eventCallback('onUpdate', updateText);
  updateText();
  window.__timelines = window.__timelines || {};
  window.__timelines['__SCENE_ID__'] = tl;
})();
