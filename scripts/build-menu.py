"""Replace only menu contents; retain the site's header, CSS and scripts."""
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.menu import load, render
from scripts.carousel import render_carousel
from bot.site import load as load_site
from scripts.site import render_site
from scripts import seasonal

page = Path('public/index.html')
html = page.read_text()
html = re.sub(r'\sdata-season="halloween"', '', html, count=1)
if seasonal.enabled():
    html, season_count = re.subn(r'(<html\b[^>]*)(>)', r'\1 data-season="halloween"\2', html, count=1)
    if season_count != 1:
        raise SystemExit('Expected one html element for seasonal decorations')
menu = load()
pattern = r'(<main\b[^>]*class="menu"[^>]*>)[\s\S]*?(</main>)'
html, count = re.subn(pattern, lambda m: m[1] + '\n' + render(menu) + '\n' + m[2], html)
if count != 1:
    raise SystemExit('Expected exactly one main.menu in public/index.html')
carousel = '<!-- carousel:start -->\n' + render_carousel(menu) + '\n<!-- carousel:end -->'
if '<!-- carousel:start -->' in html:
    html = re.sub(r'<!-- carousel:start -->[\s\S]*?<!-- carousel:end -->', lambda _: carousel, html)
else:
    html = html.replace('<main class="menu"', carousel + '\n<main class="menu"', 1)
footer = '<!-- site-info:start -->\n' + render_site(load_site()) + '\n<!-- site-info:end -->'
if '<!-- site-info:start -->' in html:
    html, count = re.subn(r'<!-- site-info:start -->[\s\S]*?<!-- site-info:end -->', lambda _: footer, html)
else:
    html, count = re.subn('<div class="end-line" aria-hidden="true"></div>', lambda _: footer, html)
if count != 1:
    raise SystemExit('Expected exactly one site-info block in public/index.html')
page.write_text(html)
