"""Replace only menu contents; retain the site's header, CSS and scripts."""
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.menu import load, render
from scripts.carousel import render_carousel

page = Path('public/index.html')
html = page.read_text()
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
page.write_text(html)
