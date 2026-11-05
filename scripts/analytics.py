"""Optional Yandex Metrica setup; empty ID leaves the original network policy intact."""
import json
import re
from pathlib import Path

PATH = Path('config/analytics.json')
DOMAINS = ['https://mc.yandex.' + suffix for suffix in (
    'ru', 'az', 'by', 'co.il', 'com', 'com.am', 'com.ge', 'com.tr', 'ee', 'fr',
    'kg', 'kz', 'lt', 'lv', 'md', 'tj', 'tm', 'uz')]
NETWORK = ' '.join(DOMAINS + ['https://mc.webvisor.com', 'https://mc.webvisor.org'])
BASE_POLICY = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"


def load():
    config = json.loads(PATH.read_text()) if PATH.exists() else {}
    counter = config.get('yandex_counter_id')
    if counter is not None and (type(counter) is not int or not 1 <= counter <= 999999999999999):
        raise ValueError('yandex_counter_id: номер счётчика положительным целым числом или null.')
    return counter


def apply(html):
    counter = load()
    html = re.sub(r'<!-- analytics-info:start -->[\s\S]*?<!-- analytics-info:end -->', '', html)
    html = re.sub(r'\n?<meta name="yandex-metrika-id"[^>]*>', '', html)
    html = re.sub(r'\n?<script src="\./analytics\.js[^>]*></script>', '', html)
    policy = BASE_POLICY
    if counter:
        policy = policy.replace("script-src 'self'", "script-src 'self' " + NETWORK + ' https://yastatic.net')
        policy = policy.replace("img-src 'self' data:", "img-src 'self' data: " + NETWORK)
        policy = policy.replace("connect-src 'none'", 'connect-src ' + NETWORK)
        html = html.replace('</head>', f'<meta name="yandex-metrika-id" content="{counter}">\n</head>')
        html = html.replace('<script src="./app.js', '<script src="./analytics.js?v=20261105" defer></script>\n<script src="./app.js', 1)
        disclosure = '<!-- analytics-info:start --><section class="legal-info"><h3>Статистика посещений</h3><p class="dialog-copy">Мы используем Яндекс Метрику для анализа посещаемости и нажатий в меню. Сервис может обрабатывать IP-адрес, cookies, сведения об устройстве и посещениях. Запись сессий (Вебвизор) в коде сайта отключена. <a href="https://yandex.ru/legal/confidential/" target="_blank" rel="noopener noreferrer">Политика конфиденциальности Яндекса</a>.</p></section><!-- analytics-info:end -->'
        html = html.replace('\n</dialog>\n<!-- site-info:end -->', disclosure + '\n</dialog>\n<!-- site-info:end -->', 1)
    return re.sub(r'(<meta http-equiv="Content-Security-Policy" content=")[^"]*(">)', lambda m: m[1]+policy+m[2], html, count=1)
