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
