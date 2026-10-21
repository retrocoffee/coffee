const pages = [...document.querySelectorAll('.page')];
const controls = [...document.querySelectorAll('[data-page]')];
const previous = document.querySelector('.previous');
const next = document.querySelector('.next');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let current = 0;
let busy = false;
async function turnPage(target, updateHash = true) {
  if (busy || target === current || !pages[target]) return;
  busy = true;
  const outgoing = pages[current];
  const incoming = pages[target];
  const backwards = target < current;
  outgoing.inert = true;
  incoming.hidden = false;
  incoming.inert = false;
  incoming.classList.add('entering');
  outgoing.classList.add('leaving');
  document.body.classList.toggle('menu-open', target === 1);
  window.scrollTo({ top: 0, behavior: 'instant' });
  if (!reducedMotion.matches) {
    await Promise.all([
      outgoing.animate([
        { transform: 'rotateY(0deg)', opacity: 1, filter: 'brightness(1)' },
        { transform: `rotateY(${backwards ? 85 : -85}deg)`, opacity: 0, filter: 'brightness(.4)' }
      ], { duration: 750, easing: 'cubic-bezier(.4,0,.2,1)', fill: 'forwards' }).finished,
      incoming.animate([
        { transform: `rotateY(${backwards ? -12 : 12}deg)`, opacity: 0 },
        { transform: 'rotateY(0deg)', opacity: 1 }
      ], { duration: 800, easing: 'cubic-bezier(.2,.7,.2,1)' }).finished
    ]);
  }
  outgoing.hidden = true;
  outgoing.getAnimations().forEach(animation => animation.cancel());
  outgoing.classList.remove('active', 'leaving');
  incoming.classList.remove('entering');
  incoming.classList.add('active');
  current = target;
  previous.hidden = current === 0;
  next.hidden = current === 1;
  controls.forEach(button => {
    const active = Number(button.dataset.page) === current;
    button.classList.toggle('active', active);
    button.classList.toggle('selected', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  if (updateHash) history.replaceState(null, '', current ? '#menu' : '#home');
  document.querySelector('#page-announcement').textContent = current ? 'Страница 2: меню кофейни' : 'Страница 1: РЕТРОКОФЕ';
  if (outgoing.contains(document.activeElement) || document.activeElement.hidden) {
    controls.find(button => Number(button.dataset.page) === current).focus({ preventScroll: true });
  }
  busy = false;
}
controls.forEach(button => button.addEventListener('click', () => turnPage(Number(button.dataset.page))));
previous.addEventListener('click', () => turnPage(0));
next.addEventListener('click', () => turnPage(1));
document.querySelector('.wordmark').addEventListener('click', event => { event.preventDefault(); turnPage(0); });
window.addEventListener('keydown', event => {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  if (event.key === 'ArrowRight') turnPage(1);
  if (event.key === 'ArrowLeft' || event.key === 'Escape') turnPage(0);
});
let touchStart;
document.querySelector('#book').addEventListener('touchstart', event => { touchStart = [event.touches[0].clientX, event.touches[0].clientY]; }, { passive: true });
document.querySelector('#book').addEventListener('touchend', event => {
  if (!touchStart) return;
  const dx = event.changedTouches[0].clientX - touchStart[0];
  const dy = event.changedTouches[0].clientY - touchStart[1];
  if (Math.abs(dx) > 65 && Math.abs(dx) > Math.abs(dy) * 1.5) turnPage(dx < 0 ? 1 : 0);
  touchStart = null;
}, { passive: true });
window.addEventListener('hashchange', () => turnPage(location.hash === '#menu' ? 1 : 0, false));
if (location.hash === '#menu') turnPage(1, false);
const motionButton = document.querySelector('#motion-toggle');
let paused = reducedMotion.matches;
function setMotion() {
  motionButton.setAttribute('aria-pressed', String(paused));
  motionButton.querySelector('.pause-icon').textContent = paused ? '▷' : 'Ⅱ';
  motionButton.querySelector('.motion-label').textContent = paused ? 'Продолжить мгновение' : 'Остановить мгновение';
  window.dispatchEvent(new CustomEvent('coffee-motion', { detail: { paused } }));
}
motionButton.addEventListener('click', () => { paused = !paused; setMotion(); });
reducedMotion.addEventListener('change', event => { paused = event.matches; setMotion(); });
setMotion();
