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

for (const match of html.matchAll(/srcset="([^"]+)"/g)) {
  for (const candidate of match[1].split(',')) {
    const path = candidate.trim().split(/\s+/)[0];
    assert(path.startsWith('./') && existsSync(`public/${path.slice(2)}`), `Missing responsive asset: ${path}`);
  }
}
assert(html.includes('http-equiv="Content-Security-Policy"'));
assert(html.includes("script-src 'self'"));
assert(html.includes('name="referrer" content="no-referrer"'));

const counter = JSON.parse(readFileSync('config/analytics.json', 'utf8')).yandex_counter_id;
assert.equal(html.includes('meta name="yandex-metrika-id"'), Number.isSafeInteger(counter) && counter > 0);
