"""Small seasonal decorations, independent of menu and carousel selections."""
import json
from pathlib import Path

CONFIG_PATH = Path('config/seasonal.json')


def enabled():
    try:
        config = json.loads(CONFIG_PATH.read_text())
    except FileNotFoundError:
        return False
    if not isinstance(config, dict) or type(config.get('halloween')) is not bool:
        raise ValueError('config/seasonal.json: halloween должен быть true или false.')
    return config['halloween']


def image_for(entry, image):
    # Keep the original menu image intact. A new picture chosen in Telegram
    # takes priority over the temporary decoration.
    original = './assets/menu-images/7f4d43cf8f71d72389e7879ec507a2b73c2d2d13cb256ce971d76df2ab45d72e.webp'
    if (enabled() and not entry.get('custom_image') and image == original
            and (entry['section'], entry['group'], entry['item']) == ('seasonal', 'g1', 'i3')):
        return './assets/products/pumpkin-raf-blinking.svg'
    return image
