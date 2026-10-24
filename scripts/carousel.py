"""Render featured drinks from current menu data; no separate copy of prices."""
import re
from html import escape
from pathlib import Path
from bot import carousel as selection


def render_carousel(menu):
    entries = selection.load()
    cards = []
    for entry in entries:
        if entry.get('hidden'):
            continue
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
        description = entry.get('description_override', description)
        image = entry['image'] if entry.get('custom_image') else item.get('image', entry['image'])
        if not selection.valid_image(image) or not Path('public', image[2:]).is_file():
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
<span class="visually-hidden carousel-status" aria-live="polite" aria-atomic="true"></span>
</section>'''
