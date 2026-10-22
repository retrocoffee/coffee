// Keep one category open, including in browsers without details[name] support.
const categories = [...document.querySelectorAll('.menu-category')];
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
for (const category of categories) {
  category.addEventListener('toggle', () => {
    if (!category.open) return;
    for (const other of categories) {
      if (other !== category) other.open = false;
    }

    // After the browser lays out the expanded category, bring its heading to the top.
    requestAnimationFrame(() => {
      if (!category.open) return;
      category.querySelector('summary').scrollIntoView({
        behavior: reduceMotion.matches ? 'auto' : 'smooth',
        block: 'start'
      });
    });
  });
}

// One continuous loop, with the current menu rendered by the build step.
const carousel = document.querySelector('.product-carousel');
if (carousel) {
  const viewport = carousel.querySelector('.carousel-window');
  const track = carousel.querySelector('.carousel-track');
  const originals = [...track.children];
  const previous = carousel.querySelector('.carousel-previous');
  const next = carousel.querySelector('.carousel-next');
  const playback = carousel.querySelector('.carousel-playback');
  const status = carousel.querySelector('.carousel-status');
  const count = originals.length;
  const clone = card => {
    const copy = card.cloneNode(true);
    copy.setAttribute('aria-hidden', 'true');
    copy.inert = true;
    return copy;
  };
  if (count > 1) {
    track.prepend(...originals.map(clone));
    track.append(...originals.map(clone));
    [previous, next, playback].forEach(button => { button.hidden = false; });
  }
  originals.forEach((card, index) => {
    card.setAttribute('role', 'group');
    card.setAttribute('aria-roledescription', 'слайд');
    card.setAttribute('aria-label', `${index + 1} из ${count}`);
  });
  carousel.classList.add('is-ready');
  viewport.tabIndex = 0;
  const cards = [...track.children];
  let step = 0;
  let offset = 0;
  let cycle = 0;
  let motion = null;
  let drag = null;
  let paused = reduceMotion.matches || count < 2;
  let hovered = false;
  let focused = false;
  let visible = true;
  let resumeAt = 0;
  let lastTime = 0;

  const normalize = value => count > 1 ? count * step + ((value - count * step) % cycle + cycle) % cycle : 0;
  const announce = () => {
    const index = ((Math.round(offset / step) % count) + count) % count;
    status.textContent = `${originals[index].querySelector('.product-name').textContent}, ${originals[index].querySelector('.product-meta').textContent}`;
  };
  const draw = () => {
    track.style.transform = `translate3d(${viewport.clientWidth / 2 - originals[0].offsetWidth / 2 - offset}px,0,0)`;
    cards.forEach((card, index) => {
      const distance = Math.abs(index * step - offset) / step;
      card.style.opacity = Math.max(.18, 1 - distance * .32).toFixed(3);
      card.style.transform = `scale(${(.9 + .1 * Math.max(0, 1 - distance)).toFixed(3)})`;
    });
  };
  const layout = () => {
    const progress = step ? offset / step : count > 1 ? count : 0;
    step = originals[0].offsetWidth + parseFloat(getComputedStyle(track).gap);
    cycle = step * count;
    offset = normalize(progress * step);
    motion = null;
    drag = null;
    draw();
  };
  const go = direction => {
    if (count < 2) return;
    const target = (Math.round(offset / step) + direction) * step;
    if (reduceMotion.matches) {
      offset = normalize(target);
      motion = null;
      draw();
      announce();
    } else {
      motion = { from: offset, to: target, time: performance.now(), duration: 650 };
    }
    resumeAt = performance.now() + 3200;
  };
  const updatePlayback = () => {
    playback.setAttribute('aria-pressed', String(paused));
    playback.setAttribute('aria-label', paused ? 'Включить автопролистывание' : 'Приостановить автопролистывание');
  };
  previous.addEventListener('click', () => go(-1));
  next.addEventListener('click', () => go(1));
  playback.addEventListener('click', () => {
    paused = !paused;
    updatePlayback();
  });
  viewport.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      go(event.key === 'ArrowLeft' ? -1 : 1);
    }
  });
  carousel.addEventListener('pointerenter', event => { if (event.pointerType === 'mouse') hovered = true; });
  carousel.addEventListener('pointerleave', event => { if (event.pointerType === 'mouse') hovered = false; });
  carousel.addEventListener('focusin', event => { focused = event.target.matches(':focus-visible'); });
  carousel.addEventListener('focusout', () => {
    requestAnimationFrame(() => { focused = Boolean(carousel.querySelector(':focus-visible')); });
  });
  viewport.addEventListener('pointerdown', event => {
    if (!event.isPrimary || event.button !== 0 || count < 2) return;
    motion = null;
    drag = { id: event.pointerId, x: event.clientX, from: offset };
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    offset = normalize(drag.from - (event.clientX - drag.x));
    draw();
  });
  const finishDrag = event => {
    if (!drag || drag.id !== event.pointerId) return;
    drag = null;
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    const target = Math.round(offset / step) * step;
    if (reduceMotion.matches) {
      offset = normalize(target);
      draw();
      announce();
    } else {
      motion = { from: offset, to: target, time: performance.now(), duration: 400 };
    }
    resumeAt = performance.now() + 3200;
  };
  viewport.addEventListener('pointerup', finishDrag);
  viewport.addEventListener('pointercancel', finishDrag);
  viewport.addEventListener('lostpointercapture', finishDrag);
  reduceMotion.addEventListener('change', () => {
    paused = reduceMotion.matches || count < 2;
    updatePlayback();
  });
  new ResizeObserver(layout).observe(viewport);
  new IntersectionObserver(entries => { visible = entries[0].isIntersecting; }).observe(carousel);
  const tick = time => {
    const delta = Math.min((time - (lastTime || time)) / 1000, .05);
    lastTime = time;
    if (!document.hidden && visible) {
      let changed = false;
      if (motion) {
        const progress = Math.min(1, (time - motion.time) / motion.duration);
        const eased = progress < .5 ? 4 * progress ** 3 : 1 - (-2 * progress + 2) ** 3 / 2;
        offset = motion.from + (motion.to - motion.from) * eased;
        if (progress === 1) {
          offset = normalize(offset);
          motion = null;
          announce();
        }
        changed = true;
      } else if (!paused && !hovered && !focused && !drag && time >= resumeAt) {
        offset = normalize(offset + delta * 20);
        changed = true;
      }
      if (changed) draw();
    }
    requestAnimationFrame(tick);
  };
  layout();
  updatePlayback();
  requestAnimationFrame(tick);
}


// First tap reveals the review link; navigation requires a separate tap.
const reviewWidget = document.querySelector('.review-widget');
if (reviewWidget) {
  const toggle = reviewWidget.querySelector('.review-toggle');
  const panel = reviewWidget.querySelector('.review-panel');
  const setReviewOpen = open => {
    reviewWidget.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Свернуть ссылку для отзыва' : 'Открыть ссылку для отзыва');
    panel.setAttribute('aria-hidden', String(!open));
    panel.inert = !open;
  };
  toggle.addEventListener('click', () => setReviewOpen(toggle.getAttribute('aria-expanded') !== 'true'));
  document.addEventListener('pointerdown', event => {
    if (!reviewWidget.contains(event.target)) setReviewOpen(false);
  });
  reviewWidget.addEventListener('keydown', event => {
    if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
      setReviewOpen(false);
      toggle.focus();
    }
  });
  reviewWidget.addEventListener('focusout', event => {
    if (!reviewWidget.contains(event.relatedTarget)) setReviewOpen(false);
  });
}
