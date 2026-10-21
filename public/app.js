// Keep one category open, including in browsers without details[name] support.
const categories = [...document.querySelectorAll('.menu-category')];
for (const category of categories) {
  category.addEventListener('toggle', () => {
    if (!category.open) return;
    for (const other of categories) {
      if (other !== category) other.open = false;
    }
  });
}
