"""Menu schema shared by the renderer and Telegram editor (standard library only)."""
import json
import re
from html import escape
from pathlib import Path
from bot.images import menu_image, valid_image_path

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
            if group['columns']:
                out.append('<thead><tr><th scope="col"><span class="visually-hidden">Напиток</span></th>' + ''.join(f'<th scope="col">{escape(col)}</th>' for col in group['columns']) + '</tr></thead>')
            out.append('<tbody>')
            for item in group['items']:
                notes = ''.join(f'<span class="item-note">{escape(note)}</span>' for note in item['notes'])
                image = escape(menu_image(section, group, item), quote=True)
                out.append(f'<tr><th scope="row"><span class="menu-item"><img class="menu-item-image" src="{image}" alt="" width="38" height="42" loading="lazy" decoding="async"><span class="menu-item-copy">{escape(item["name"])}{notes}</span></span></th>' + ''.join(f'<td>{escape(price)}</td>' for price in item['prices']) + '</tr>')
            out.append('</tbody></table>')
        out.append('</div></details>')
    return '\n'.join(out)
