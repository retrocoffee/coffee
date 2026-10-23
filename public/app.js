// Keep one category open, including in browsers without details[name] support.
const categories = [...document.querySelectorAll('.menu-category')];
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
let pageDialogOpen = false;
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

// One centered card at a time, with the current menu rendered by the build step.
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
  let cardAnchor = 0;
  let motion = null;
  let drag = null;
  let paused = reduceMotion.matches || count < 2;
  let hovered = false;
  let focused = false;
  let visible = true;
  const autoplayInterval = 1000;
  const transitionDuration = 100;
  let autoplayElapsed = 0;
  let lastTime = null;

  const normalize = value => count > 1 ? count * step + ((value - count * step) % cycle + cycle) % cycle : 0;
  const announce = () => {
    const index = ((Math.round(offset / step) % count) + count) % count;
    status.textContent = `${originals[index].querySelector('.product-name').textContent}, ${originals[index].querySelector('.product-meta').textContent}`;
  };
  const draw = () => {
    track.style.transform = `translate3d(${viewport.clientWidth / 2 - cardAnchor - offset}px,0,0)`;
    cards.forEach((card, index) => {
      const distance = Math.abs(index * step - offset) / step;
      card.style.opacity = Math.max(.18, 1 - distance * .32).toFixed(3);
      card.style.transform = `scale(${(.9 + .1 * Math.max(0, 1 - distance)).toFixed(3)})`;
    });
  };
  const layout = () => {
    const progress = step ? offset / step : count > 1 ? count : 0;
    const cardWidth = parseFloat(getComputedStyle(originals[0]).width);
    step = cardWidth + parseFloat(getComputedStyle(track).gap);
    cycle = step * count;
    cardAnchor = cardWidth / 2;
    offset = normalize(Math.round(progress) * step);
    motion = null;
    drag = null;
    autoplayElapsed = 0;
    draw();
  };
  const moveTo = target => {
    autoplayElapsed = 0;
    if (reduceMotion.matches) {
      offset = normalize(target);
      motion = null;
      draw();
      announce();
    } else {
      motion = { from: offset, to: target, elapsed: 0 };
    }
  };
  const go = direction => {
    if (count < 2) return;
    moveTo((Math.round(offset / step) + direction) * step);
  };
  const updatePlayback = () => {
    playback.setAttribute('aria-pressed', String(paused));
    playback.setAttribute('aria-label', paused ? 'Включить автопролистывание' : 'Приостановить автопролистывание');
  };
  previous.addEventListener('click', () => go(-1));
  next.addEventListener('click', () => go(1));
  playback.addEventListener('click', () => {
    paused = !paused;
    autoplayElapsed = 0;
    updatePlayback();
  });
  viewport.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      focused = true;
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
    drag = { id: event.pointerId, x: event.clientX, from: offset, distance: 0 };
    autoplayElapsed = 0;
    viewport.setPointerCapture(event.pointerId);
  });
  viewport.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    drag.distance = Math.max(-step, Math.min(step, event.clientX - drag.x));
    offset = drag.from - drag.distance;
    draw();
  });
  const finishDrag = event => {
    if (!drag || drag.id !== event.pointerId) return;
    const cancelled = event.type !== 'pointerup';
    const direction = !cancelled && Math.abs(drag.distance) >= Math.min(48, step * .15)
      ? (drag.distance < 0 ? 1 : -1) : 0;
    const target = (Math.round(drag.from / step) + direction) * step;
    drag = null;
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    moveTo(target);
  };
  viewport.addEventListener('pointerup', finishDrag);
  viewport.addEventListener('pointercancel', finishDrag);
  viewport.addEventListener('lostpointercapture', finishDrag);
  reduceMotion.addEventListener('change', () => {
    paused = reduceMotion.matches || count < 2;
    autoplayElapsed = 0;
    if (reduceMotion.matches && motion) moveTo(motion.to);
    updatePlayback();
  });
  document.addEventListener('visibilitychange', () => {
    autoplayElapsed = 0;
    lastTime = null;
  });
  new ResizeObserver(layout).observe(viewport);
  new IntersectionObserver(entries => {
    visible = entries[0].isIntersecting;
    if (!visible) autoplayElapsed = 0;
  }).observe(carousel);
  const tick = time => {
    const delta = lastTime === null ? 0 : Math.max(0, Math.min(time - lastTime, 50));
    lastTime = time;
    if (!document.hidden && visible && !pageDialogOpen) {
      let changed = false;
      if (!paused && !hovered && !focused && !drag) autoplayElapsed += delta;
      else autoplayElapsed = 0;
      if (motion) {
        motion.elapsed += delta;
        const progress = Math.min(1, motion.elapsed / transitionDuration);
        const eased = progress < .5 ? 4 * progress ** 3 : 1 - (-2 * progress + 2) ** 3 / 2;
        offset = motion.from + (motion.to - motion.from) * eased;
        if (progress === 1) {
          offset = normalize(offset);
          motion = null;
          announce();
        }
        changed = true;
      } else if (!paused && !hovered && !focused && !drag && autoplayElapsed >= autoplayInterval) {
        go(1);
      }
      if (changed) draw();
    } else autoplayElapsed = 0;
    requestAnimationFrame(tick);
  };
  layout();
  updatePlayback();
  requestAnimationFrame(tick);
}


// Native dialogs keep focus inside the window and support Escape.
for (const trigger of document.querySelectorAll('[data-open-dialog]')) {
  const dialog = document.getElementById(trigger.dataset.openDialog);
  if (!dialog || typeof dialog.showModal !== 'function') continue;
  const open = () => {
    if (dialog.open) return;
    dialog.showModal();
    pageDialogOpen = true;
    document.body.classList.add('dialog-open');
  };
  trigger.addEventListener('click', event => {
    event.preventDefault();
    open();
  });
  dialog.querySelector('[data-close-dialog]').addEventListener('click', event => {
    event.preventDefault();
    dialog.close();
  });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close();
  });
  dialog.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const controls = [...dialog.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),[tabindex]:not([tabindex="-1"])')]
      .filter(element => element.getClientRects().length > 0);
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  dialog.addEventListener('close', () => {
    pageDialogOpen = false;
    document.body.classList.remove('dialog-open');
    if (location.hash === `#${dialog.id}`) history.replaceState(null, '', location.pathname + location.search);
    trigger.focus({ preventScroll: true });
  });
  if (location.hash === `#${dialog.id}`) open();
}
