"""Self-contained, private HTML draft for download from Telegram."""
import base64
import mimetypes
import re
from pathlib import Path
from bot.menu import render


def document(menu, assets=None):
    assets = assets or {}
    def uri(path):
        data = assets.get('./' + path) or Path('public', path).read_bytes()
        mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        return 'data:' + mime + ';base64,' + base64.b64encode(data).decode()
    css = Path('public/style.css').read_text()
    css = re.sub(r"url\('\./([^']+)'\)", lambda m: "url('" + uri(m[1].split('?')[0]) + "')", css)
    html = render(menu)
    html = re.sub(r'src="\./([^"]+)"', lambda m: 'src="' + uri(m[1]) + '"', html)
    return ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Предпросмотр меню</title><style>' + css + '</style><body><main class="menu">' + html + '</main></body></html>').encode()
