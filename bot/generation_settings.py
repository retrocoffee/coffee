"""Shared, versioned image preferences; credentials never belong in this file."""
import json
from pathlib import Path

SETTINGS_PATH = Path('config/generation.json')
COLORS = {
    'color': ('Цветная', 'Full color, natural appealing colors.'),
    'monochrome': ('Чёрно-белая', 'Strictly monochrome, black and gray only, no color.'),
}
STYLES = {
    'illustration': ('Иллюстрация', 'Minimalist illustration, clean shapes and soft shading.'),
    'photo': ('Фотореализм', 'Studio product photography, realistic materials and appetizing lighting.'),
    'watercolor': ('Акварель', 'Delicate watercolor illustration with soft pigment edges.'),
    '3d': ('3D', 'Minimalist 3D product render, smooth materials and soft lighting.'),
    'line': ('Контурный рисунок', 'Clean ink line drawing, simple shapes and fine outlines.'),
}
MODELS = {
    'flux-schnell': {'name': 'FLUX.1 schnell', 'id': '@cf/black-forest-labs/flux-1-schnell',
                     'payload': {'steps': 4}, 'binary': False},
    'sdxl-lightning': {'name': 'SDXL Lightning', 'id': '@cf/bytedance/stable-diffusion-xl-lightning',
                       'payload': {'num_steps': 4, 'width': 1024, 'height': 1024}, 'binary': True},
}
FIELDS = {'color': 'Цветность', 'style': 'Стиль', 'model': 'Модель'}
DEFAULTS = {'version': 1, 'color': 'color', 'style': 'illustration', 'model': 'flux-schnell'}


def choices(field):
    if field == 'color':
        return {key: value[0] for key, value in COLORS.items()}
    if field == 'style':
        return {key: value[0] for key, value in STYLES.items()}
    if field == 'model':
        return {key: value['name'] for key, value in MODELS.items()}
    raise ValueError('Неизвестная настройка генерации.')


def validate(settings):
    if (not isinstance(settings, dict) or set(settings) != set(DEFAULTS)
            or type(settings.get('version')) is not int or settings['version'] != 1):
        raise ValueError('Неподдерживаемый формат настроек генерации.')
    for field, label in FIELDS.items():
        if not isinstance(settings[field], str) or settings[field] not in choices(field):
            raise ValueError(f'{label}: выберите один из вариантов в настройках генерации.')
    return {key: settings[key] for key in DEFAULTS}


def encode(settings):
    return json.dumps(validate(settings), ensure_ascii=False, indent=2) + '\n'


def load():
    try:
        return validate(json.loads(SETTINGS_PATH.read_text()))
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError):
        raise ValueError('Не удалось прочитать настройки генерации. Проверьте config/generation.json в GitHub.') from None


def summary(settings):
    settings = validate(settings)
    return '\n'.join(f'{label}: {choices(field)[settings[field]]}' for field, label in FIELDS.items())
