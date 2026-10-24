"""Carousel selection and independent, conflict-safe persistence."""
import hashlib
import json
import re
from pathlib import Path
from bot.images import MAX_IMAGE_BYTES, menu_image, valid_image_path

PATH = Path('data/carousel.json')
MAX_ITEMS = 50


def key(entry):
    return tuple(entry[k] for k in ('section', 'group', 'item'))


def valid_image(image):
    return isinstance(image, str) and bool(valid_image_path(image) or re.fullmatch(
        r'\./assets/(?:products|menu-icons)/[a-z0-9-]+\.(?:svg|png|webp|jpe?g|avif)', image))


def validate(entries):
    if not isinstance(entries, list) or len(entries) > MAX_ITEMS:
        raise ValueError(f'В карусели может быть до {MAX_ITEMS} позиций.')
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or not {'section', 'group', 'item', 'image'} <= set(entry) or set(entry) - {
                'section', 'group', 'item', 'image', 'description', 'description_override', 'custom_image', 'hidden'}:
            raise ValueError('Неверный формат позиции карусели.')
        if any(not isinstance(entry[k], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', entry[k])
               for k in ('section', 'group', 'item')) or key(entry) in seen:
            raise ValueError('Повторяющаяся или неверная позиция карусели.')
        seen.add(key(entry))
        if not valid_image(entry['image']):
            raise ValueError('Неверная картинка карусели.')
        for field in ('description', 'description_override'):
            if field in entry and (not isinstance(entry[field], str) or len(entry[field]) > 1000):
                raise ValueError('Описание карусели — до 1000 символов.')
        for field in ('custom_image', 'hidden'):
            if field in entry and type(entry[field]) is not bool:
                raise ValueError('Неверный переключатель карусели.')
    return entries


def encode(entries):
    return json.dumps(validate(entries), ensure_ascii=False, indent=2) + '\n'


def load():
    return validate(json.loads(PATH.read_text()))


def fingerprint(entries):
    return hashlib.sha256(encode(entries).encode()).hexdigest()


def lookup(menu, entry):
    section = next((s for s in menu['sections'] if s['id'] == entry['section']), None)
    group = next((g for g in section['groups'] if g['id'] == entry['group']), None) if section else None
    item = next((i for i in group['items'] if i['id'] == entry['item']), None) if group else None
    return (section, group, item) if item else None


def new_entry(section, group, item):
    return {'section': section['id'], 'group': group['id'], 'item': item['id'],
            'image': menu_image(section, group, item), 'description': ''}


def publish(entries, baseline, assets, update_id, git):
    validate(entries)
    git('fetch', 'origin', 'main')
    remote = validate(json.loads(git('show', f'FETCH_HEAD:{PATH}')))
    if fingerprint(remote) != baseline:
        raise ValueError('Карусель уже изменилась. Откройте /start → «Карусель» и повторите правку.')
    menu = json.loads(git('show', 'FETCH_HEAD:data/menu.json'))
    old_keys = {key(e) for e in remote}
    if any(key(e) not in old_keys and not lookup(menu, e) for e in entries):
        raise ValueError('Добавляемая позиция уже удалена из меню. Обновите данные через /start.')
    git('merge', '--ff-only', 'FETCH_HEAD')
    if fingerprint(entries) == fingerprint(remote):
        return False
    references = {e['image'] for e in entries}
    assets = {p: data for p, data in assets.items() if p in references}
    for image, data in assets.items():
        if not valid_image_path(image) or len(data) > MAX_IMAGE_BYTES or Path(image).stem != hashlib.sha256(data).hexdigest():
            raise ValueError('Неверная картинка карусели. Загрузите её снова.')
    for image in references:
        if image not in assets and not Path('public', image[2:]).is_file():
            raise ValueError('Картинка карусели недоступна. Загрузите её снова.')
    paths = [str(PATH)]
    created = []
    try:
        for image, data in assets.items():
            path = Path('public', image[2:])
            if path.exists():
                if path.read_bytes() != data:
                    raise ValueError('Картинка повреждена.')
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            created.append(path)
            paths.append(str(path))
        PATH.write_text(encode(entries))
        git('add', '--', *paths)
        git('commit', '-m', 'Обновление карусели', '-m', f'Telegram-update: {update_id}')
    except Exception:
        git('restore', '--staged', '--worktree', str(PATH))
        if created:
            git('reset', '--', *(str(p) for p in created))
            for path in created:
                path.unlink(missing_ok=True)
        raise
    try:
        git('push', 'origin', 'HEAD:main')
    except Exception:
        git('reset', '--hard', 'HEAD^')
        raise ValueError('Не удалось сохранить карусель. Обновите данные через /start.') from None
    return True
