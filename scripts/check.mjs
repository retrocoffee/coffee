import { readFileSync, existsSync } from 'node:fs';
import assert from 'node:assert/strict';
const html = readFileSync('public/index.html', 'utf8');
for (const match of html.matchAll(/(?:src|href)="\.\/([^"#]+)"/g)) {
  assert(existsSync(`public/${match[1]}`), `Missing asset: ${match[1]}`);
}
assert(html.includes('lang="ru"'));
assert(html.includes('РЕТРОКОФЕ'));
assert.equal((html.match(/class="menu-item"/g) || []).length, 8);
console.log('Local assets and all eight menu positions verified.');
