"""Build small raster variants without modifying administrator-uploaded originals."""
import hashlib
from pathlib import Path

ROOT = Path('public/assets/resized')


def variant(source, width):
    content = Path('public', source[2:]).read_bytes()
    digest = hashlib.sha256(content + str(width).encode()).hexdigest()
    return ROOT / (digest + '.webp')


def attributes(source, sizes):
    if not source.endswith('.webp') or not Path('public', source[2:]).is_file(): return ''
    from PIL import Image
    candidates = []
    widths = set()
    for width in (96, 320):
        path = variant(source, width)
        if path.exists():
            with Image.open(path) as image: actual_width = image.width
            if actual_width in widths: continue
            widths.add(actual_width)
            candidates.append('./' + str(path.relative_to('public')) + f' {actual_width}w')
    if not candidates: return ''
    return ' srcset="' + ', '.join(candidates) + '" sizes="' + sizes + '"'


def build():
    from PIL import Image
    ROOT.mkdir(parents=True, exist_ok=True)
    for source in [*Path('public/assets/menu-images').glob('*.webp'), *Path('public/assets/products').glob('*.webp')]:
        for width in (96, 320):
            target = variant('./' + str(source.relative_to('public')), width)
            if target.exists(): continue
            with Image.open(source) as image:
                # All inputs are bounded, normalized repository artwork.
                image.thumbnail((width, width), Image.Resampling.LANCZOS)
                image.save(target, format='WEBP', quality=82, method=6)
