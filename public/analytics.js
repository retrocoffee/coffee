// External bootstrap keeps inline scripts disallowed by CSP.
(() => {
  const id = Number(document.querySelector('meta[name="yandex-metrika-id"]')?.content);
  if (!Number.isSafeInteger(id) || id < 1 || window.RetroMetrics) return;
  window.ym = window.ym || function () { (window.ym.a = window.ym.a || []).push(arguments); };
  window.ym.l = Date.now();
  const allowed = new Set(['category_open', 'carousel_item', 'review_click', 'socials_open', 'social_click', 'about_open']);
  window.RetroMetrics = {
    goal(name, params = {}) {
      if (!allowed.has(name)) return;
      // Only menu references are custom parameters; no message text or profile fields.
      const clean = {};
      for (const key of ['section', 'item']) {
        if (typeof params[key] === 'string' && /^[A-Za-z0-9.\-]{1,130}$/.test(params[key])) clean[key] = params[key];
      }
      try { window.ym(id, 'reachGoal', name, clean); } catch { /* Analytics must never interrupt menu navigation. */ }
    }
  };
  window.ym(id, 'init', { webvisor: false, clickmap: false, trackLinks: false, trackHash: false, accurateTrackBounce: true });
  const tag = document.createElement('script');
  tag.async = true;
  tag.src = `https://mc.yandex.ru/metrika/tag.js?id=${id}`;
  document.head.append(tag);
  document.addEventListener('click', event => {
    const target = event.target.closest('a, summary');
    if (!target) return;
    const dialog = target.dataset.openDialog;
    if (dialog === 'social-dialog') window.RetroMetrics.goal('socials_open');
    if (dialog === 'about-dialog') window.RetroMetrics.goal('about_open');
    if (target.matches('.social-link')) window.RetroMetrics.goal('social_click');
    if (target.dataset.analyticsGoal === 'review_click') window.RetroMetrics.goal('review_click');
    if (target.matches('.menu-category > summary')) {
      const category = target.parentElement;
      requestAnimationFrame(() => { if (category.open) window.RetroMetrics.goal('category_open', { section: category.id }); });
    }
  });
})();
