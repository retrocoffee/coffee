"""Replace only menu contents; retain the site's header, CSS and scripts."""
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.menu import load, render

page = Path('public/index.html')
html = page.read_text()
pattern = r'(<main\b[^>]*class="menu"[^>]*>)[\s\S]*?(</main>)'
html, count = re.subn(pattern, lambda m: m[1] + '\n' + render(load()) + '\n' + m[2], html)
if count != 1:
    raise SystemExit('Expected exactly one main.menu in public/index.html')
page.write_text(html)
