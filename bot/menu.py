"""Menu schema shared by the renderer and Telegram editor (standard library only)."""
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo
import re
from html import escape
from pathlib import Path
from bot.images import menu_image, valid_image_path

def local_day():
    return datetime.now(ZoneInfo('Asia/Yekaterinburg')).date()


def is_unavailable(item):
    return item.get('unavailable_on') == local_day().isoformat()


MENU_PATH = Path('data/menu.json')


def validate(menu):
    if menu.get('version') != 1 or not isinstance(menu.get('sections'), list):
        raise ValueError('Неподдерживаемый формат меню')
    def text(value, limit=500, empty=False):
        if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
            raise ValueError('Некорректный или слишком длинный текст')
    def ids(rows):
        seen = set()
        for row in rows:
            ident = row.get('id', '')
            if not re.fullmatch(r'[a-zA-Z0-9-]{1,36}', ident) or ident in seen:
                raise ValueError('Некорректный или повторяющийся ID')
            seen.add(ident)
    ids(menu['sections'])
    for section in menu['sections']:
        text(section['name'], 100)
        if type(section.get('hidden')) is not bool:
            raise ValueError('Некорректная видимость раздела')
        ids(section['groups'])
        for group in section['groups']:
            if group['kind'] not in ('drinks', 'extras'):
                raise ValueError('Некорректный тип группы')
            text(group['heading'], 100, True)
            text(group['caption'], 150)
            if len(group['columns']) > 6:
                raise ValueError('Не более 6 объёмов в группе')
            for col in group['columns']:
                text(col, 40)
            ids(group['items'])
            for item in group['items']:
                text(item['name'], 200)
                if 'unavailable_on' in item:
                    try: date.fromisoformat(item['unavailable_on'])
                    except (ValueError, TypeError): raise ValueError('Некорректная дата отсутствия позиции')
                if 'image' in item and not valid_image_path(item['image']):
                    raise ValueError('Некорректная картинка позиции')
                for note in item['notes']:
                    text(note)
                if len(item['notes']) > 6 or len(item['prices']) != max(1, len(group['columns'])):
                    raise ValueError('Количество цен должно совпадать с количеством объёмов')
                for price in item['prices']:
                    if not re.fullmatch(r'(?:\d{1,6}(?:[.,]\d{1,2})? ₽|бесплатно|—)', price):
                        raise ValueError('Некорректная цена')
    return menu


def load():
    return validate(json.loads(MENU_PATH.read_text()))


def encode(menu):
    return json.dumps(validate(menu), ensure_ascii=False, indent=2) + '\n'


def prices(raw, count):
    result = []
    for value in raw.split(';'):
        value = value.strip().lower().replace('₽', '').strip()
        if value in ('бесплатно', '—'):
            result.append(value)
        elif re.fullmatch(r'\d{1,6}(?:[.,]\d{1,2})?', value):
            result.append(value + ' ₽')
        else:
            raise ValueError('Цена: число, «бесплатно» или «—». Разделитель цен — точка с запятой.')
    if len(result) != count:
        raise ValueError(f'Введите {count} цен через точку с запятой.')
    return result


def item_anchor(section, group, item):
    """Stable across renaming; dots cannot occur in menu IDs."""
    return f'menu-item-{section["id"]}.{group["id"]}.{item["id"]}'


def render(menu):
    validate(menu)
    out = []
    for section in menu['sections']:
        if section['hidden']:
            continue
        out.append(f'<details class="menu-category" name="coffee-menu" id="{section["id"]}">')
        out.append(f'<summary><span>{escape(section["name"])}</span><span class="expand-icon" aria-hidden="true"></span></summary>')
        out.append('<div class="category-content">')
        for group in section['groups']:
            if group['heading']:
                out.append(f'<h2 class="subheading">{escape(group["heading"])}</h2>')
            out.append(f'<table class="price-table {group["kind"]}"><caption class="visually-hidden">{escape(group["caption"])}</caption>')
            out.append('<tbody>')
            for item in group['items']:
                volume_notes = [note for note in item['notes'] if re.fullmatch(r'\d+(?:[.,]\d+)?\s*(?:мл|л|г|гр\.)', note)]
                descriptions = [note for note in item['notes'] if note not in volume_notes]
                notes = ''.join(f'<span class="item-note">{escape(note)}</span>' for note in descriptions)
                if sum(map(len, descriptions)) > 110:
                    notes = '<details class="item-description"><summary>Состав и описание</summary>' + notes + '</details>'
                variants = []
                for n, price in enumerate(item['prices']):
                    volume = group['columns'][n] if group['columns'] else ' · '.join(volume_notes)
                    variants.append('<span class="menu-variant">' + (f'<span class="menu-volume">{escape(volume)}</span>' if volume else '') + f'<span>{escape(price)}</span></span>')
                image = escape(menu_image(section, group, item), quote=True)
                badge = '<span class="availability-label"' + ('' if is_unavailable(item) else ' hidden') + '>Сегодня нет</span>'
                day = escape(item.get('unavailable_on', ''), quote=True)
                out.append(f'<tr id="{item_anchor(section, group, item)}" tabindex="-1" data-unavailable-on="{day}"><th scope="row"><div class="menu-item"><img class="menu-item-image" src="{image}" alt="" width="38" height="42" loading="lazy" decoding="async"><div class="menu-item-copy">{escape(item["name"])}{badge}{notes}</div></div></th><td class="menu-prices">' + ''.join(variants) + '</td></tr>')
            out.append('</tbody></table>')
        out.append('</div></details>')
    return '\n'.join(out)
