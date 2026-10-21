import { readFileSync, existsSync } from 'node:fs';
import assert from 'node:assert/strict';
const html = readFileSync('public/index.html', 'utf8');
for (const match of html.matchAll(/(?:src|href)="\.\/([^"#]+)"/g)) {
  const path = match[1].split('?')[0];
  assert(existsSync(`public/${path}`), `Missing asset: ${path}`);
}
assert(html.includes('lang="ru"'));
assert(html.includes('РЕТРОКОФЕ'));
const menu = JSON.parse(readFileSync('data/menu.json', 'utf8'));
assert.equal((html.match(/class="menu-category"/g) || []).length, menu.sections.filter(section => !section.hidden).length);
assert(!html.includes('coffee-bean'));
console.log('Menu categories and local assets verified.');
