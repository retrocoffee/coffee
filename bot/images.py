"""Local menu illustrations and bounded, metadata-free administrator uploads."""
import hashlib
import io
import re
import warnings

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


def valid_image_path(value):
    return isinstance(value, str) and re.fullmatch(r'\./assets/menu-images/[a-f0-9]{64}\.webp', value) is not None


def menu_image(section, group, item):
    if item.get('image'):
        return item['image']
    name = item['name'].lower()
    if group['kind'] == 'extras': icon = 'extras'
    elif 'глинтвейн' in name: icon = 'mulled-wine'
    elif 'сбитень' in name: icon = 'sbiten'
    elif 'тыкв' in name: icon = 'pumpkin'
    elif 'какао' in name or 'шоколад' in name: icon = 'cocoa'
    elif 'матча' in name: icon = 'matcha'
    elif 'чай' in name or 'чайник' in name: icon = 'tea'
    elif 'бабл' in name or section['id'] == 'bubble': icon = 'bubble'
    elif 'коктейль' in name: icon = 'milkshake'
    elif 'лимонад' in name or section['id'] == 'cold-drinks': icon = 'lemonade'
    elif section['id'] == 'iced-milk' or 'айс' in name: icon = 'iced'
    else: icon = 'coffee'
    return f'./assets/menu-icons/{icon}.svg'


def prepare_image(content):
    """Decode actual pixels, strip metadata and normalize to a small static WebP."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError('Отправьте картинку до 5 МБ.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as source:
                if source.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise ValueError('Поддерживаются только JPG, PNG и WebP.')
                if getattr(source, 'is_animated', False):
                    raise ValueError('Отправьте неподвижную картинку, без анимации.')
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError('Разрешение картинки — не более 16 мегапикселей.')
                image = ImageOps.exif_transpose(source).convert('RGBA')
                bounds = image.getbbox()
                if not bounds:
                    raise ValueError('Картинка полностью прозрачная.')
                image = image.crop(bounds)
                image.thumbnail((640, 640), Image.Resampling.LANCZOS)
                clean = Image.new('RGBA', image.size)
                clean.paste(image)
                result = io.BytesIO()
                clean.save(result, format='WEBP', quality=85, method=4)
                data = result.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('Не удалось прочитать картинку. Отправьте исправный JPG, PNG или WebP до 16 мегапикселей.') from None
    digest = hashlib.sha256(data).hexdigest()
    return f'./assets/menu-images/{digest}.webp', data
