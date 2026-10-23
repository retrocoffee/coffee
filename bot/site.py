"""Public cafe profile, separate from menu data and design."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime

SITE_PATH = Path('data/site.json')
FIELDS = {
    'handle': ('Подпись соцсетей', 'texts', 40),
    'name': ('Название кофейни', 'texts', 100),
    'about_text': ('Описание кофейни', 'texts', 1500),
    'owner_text': ('Слова владельца', 'texts', 1500),
    'address': ('Адрес кофейни', 'contacts', 400),
    'hours': ('Часы работы', 'contacts', 200),
    'phone': ('Телефон', 'contacts', 40),
    'email': ('Почта для обращений', 'contacts', 150),
    'legal_name': ('Полное название ИП / ООО', 'legal', 300),
    'inn': ('ИНН', 'legal', 12),
    'ogrn': ('ОГРН / ОГРНИП', 'legal', 15),
    'registered_on': ('Дата регистрации', 'legal', 10),
    'registrar': ('Регистрирующий орган', 'legal', 400),
    'legal_address': ('Адрес предприятия для обращений', 'legal', 500),
    'registry_url': ('Карточка ИП / ООО', 'legal', 500),
    'review_url': ('Ссылка для отзыва', 'contacts', 500),
}
GROUPS = {'texts': 'Тексты', 'contacts': 'Контакты', 'legal': 'Реквизиты'}


def https_url(value):
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return bool(url.scheme == 'https' and url.hostname and '.' in url.hostname
                    and not url.username and not url.password and not any(c.isspace() for c in value))
    except (ValueError, TypeError):
        return False


def validate(profile):
    if not isinstance(profile, dict) or profile.get('version') != 1:
        raise ValueError('Неподдерживаемый формат сведений о кофейне.')
    for key, (label, _, limit) in FIELDS.items():
        value = profile.get(key)
        if not isinstance(value, str) or len(value) > limit or '\x00' in value:
            raise ValueError(f'{label}: максимум {limit} символов.')
        if key in ('name', 'handle', 'review_url') and not value.strip():
            raise ValueError(f'Заполните поле «{label}».')
        if value and key.endswith('_url') and not https_url(value):
            raise ValueError(f'{label}: нужна полная HTTPS-ссылка.')
    for key, pattern in [('inn', r'(?:\d{10}|\d{12})'), ('ogrn', r'(?:\d{13}|\d{15})'),
                         ('registered_on', r'\d{2}\.\d{2}\.\d{4}'), ('phone', r'\+?[\d ()-]{5,40}'),
                         ('email', r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+')]:
        if profile[key] and not re.fullmatch(pattern, profile[key]):
            raise ValueError(f'Проверьте поле «{FIELDS[key][0]}».')
    if profile['registered_on']:
        try:
            datetime.strptime(profile['registered_on'], '%d.%m.%Y')
        except ValueError:
            raise ValueError('Укажите существующую дату регистрации в формате ДД.ММ.ГГГГ.') from None
    socials = profile.get('socials')
    if not isinstance(socials, list) or len(socials) > 12:
        raise ValueError('Можно добавить до 12 соцсетей.')
    ids = set()
    for social in socials:
        if not isinstance(social, dict):
            raise ValueError('Некорректная соцсеть.')
        ident, name, url = social.get('id', ''), social.get('name', ''), social.get('url', '')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,36}', ident) or ident in ids:
            raise ValueError('Повторяющийся или некорректный ID соцсети.')
        ids.add(ident)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or not isinstance(url, str) or len(url) > 500 or not https_url(url):
            raise ValueError('Для соцсети нужны название до 60 символов и HTTPS-ссылка.')
    return profile


def load():
    return validate(json.loads(SITE_PATH.read_text()))


def encode(profile):
    return json.dumps(validate(profile), ensure_ascii=False, indent=2) + '\n'


def missing(profile):
    fields = ['legal_name', 'inn', 'ogrn', 'registered_on', 'registrar', 'legal_address', 'address', 'hours', 'owner_text']
    result = [FIELDS[key][0] for key in fields if not profile[key]]
    if not profile['phone'] and not profile['email']:
        result.append('Телефон или почта для обращений')
    return result
