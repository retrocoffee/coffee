"""Render featured drinks from current menu data; no separate copy of prices."""
import json
import re
from html import escape
from pathlib import Path
from bot.images import valid_image_path


def render_carousel(menu):
    entries = json.loads(Path('data/carousel.json').read_text())
    cards = []
    for entry in entries:
        section = next((s for s in menu['sections'] if s['id'] == entry['section'] and not s['hidden']), None)
        if section is None:
            continue
        group = next((g for g in section['groups'] if g['id'] == entry['group']), None)
        if group is None:
            continue
        item = next((i for i in group['items'] if i['id'] == entry['item']), None)
        if item is None:
            continue
        variant = next((n for n,p in enumerate(item['prices']) if p != '—'), None)
        if variant is None:
            continue
        volume = group['columns'][variant] if group['columns'] else next(
            (note for note in item['notes'] if re.fullmatch(r'\d+(?:[.,]\d+)?\s*(?:мл|л|г|гр\.)', note)), '')
        description = ' '.join(note for note in item['notes'] if note != volume) or entry.get('description', '')
        image = item.get('image', entry['image'])
        if not (valid_image_path(image) or re.fullmatch(r'\./assets/products/[a-z0-9-]+\.(?:svg|png|webp|jpe?g|avif)', image)) or not Path('public', image[2:]).is_file():
            raise ValueError('Invalid carousel image: ' + image)
        cards.append(f'''<article class="product-card">
<img class="product-image" src="{escape(image)}" alt="{escape(item['name'])}" width="460" height="420" decoding="async">
<div class="product-copy">
<h2 class="product-name">{escape(item['name'])}</h2>
<p class="product-description">{escape(description)}</p>
<p class="product-meta"><span class="product-volume">{escape(volume)}</span><span class="product-price">{escape(item['prices'][variant])}</span></p>
</div>
</article>''')
    if not cards:
        return ''
    arrow = '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" aria-hidden="true"><path d="m14 5-7 7 7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    return f'''<section class="product-carousel" aria-label="Напитки" aria-roledescription="карусель">
<div class="carousel-window" aria-label="Листать напитки стрелками или свайпом">
<div class="carousel-track">{''.join(cards)}</div>
</div>
<button class="carousel-arrow carousel-previous" type="button" aria-label="Предыдущая позиция" hidden>{arrow}</button>
<button class="carousel-arrow carousel-next" type="button" aria-label="Следующая позиция" hidden>{arrow}</button>
<button class="carousel-playback" type="button" aria-label="Приостановить автопролистывание" aria-pressed="false" hidden>
<svg class="pause-symbol" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M5 3v10M11 3v10" stroke="currentColor" stroke-width="2"/></svg>
<svg class="play-symbol" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="m5 3 8 5-8 5Z" fill="currentColor"/></svg>
</button>
<span class="visually-hidden carousel-status" aria-live="polite" aria-atomic="true"></span>
</section>'''
