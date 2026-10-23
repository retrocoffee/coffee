"""Render footer and glass dialogs from the editable cafe profile."""
from html import escape
import re
from bot.site import validate


def lines(value):
    return '<br>'.join(escape(line) for line in value.splitlines())


def render_site(profile):
    validate(profile)
    arrow = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true"><path d="M6 18 18 6M6 6h12v12" stroke="currentColor" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    close = '<a class="dialog-close" href="#site-footer" data-close-dialog aria-label="Закрыть окно" autofocus><svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/></svg></a>'
    links = []
    for social in profile['socials']:
        url = escape(social['url'], quote=True)
        name = escape(social['name'])
        monogram = 'vk' if 'vk.com/' in social['url'] else 'tg' if 't.me/' in social['url'] else '@'
        links.append(f'<a class="social-link" href="{url}" target="_blank" rel="noopener noreferrer"><span class="social-monogram" aria-hidden="true">{monogram}</span><span>{name}</span>{arrow}</a>')
    social_links = '\n'.join(links) or '<p class="dialog-copy">Скоро здесь появятся наши соцсети.</p>'
    about = '<p class="dialog-copy">' + lines(profile['about_text']) + '</p>' if profile['about_text'] else ''
    if profile['owner_text']:
        about += '<section class="owner-note"><h3>От владельца</h3><p>' + lines(profile['owner_text']) + '</p></section>'
    contact_rows = []
    for key, label in [('address', 'Адрес'), ('hours', 'Режим работы')]:
        if profile[key]:
            contact_rows.append(f'<div><dt>{label}</dt><dd>{lines(profile[key])}</dd></div>')
    if profile['phone']:
        number = re.sub(r'[^+\d]', '', profile['phone'])
        contact_rows.append(f'<div><dt>Телефон</dt><dd><a href="tel:{escape(number)}">{escape(profile["phone"])}</a></dd></div>')
    if profile['email']:
        mail = escape(profile['email'], quote=True)
        contact_rows.append(f'<div><dt>Для обращений</dt><dd><a href="mailto:{mail}">{mail}</a></dd></div>')
    about += '<dl class="business-info">' + ''.join(contact_rows) + '</dl>'
    legal_rows = []
    for key, label in [('legal_name', 'Исполнитель'), ('inn', 'ИНН'), ('ogrn', 'ОГРН / ОГРНИП'),
                       ('registered_on', 'Дата регистрации'), ('registrar', 'Регистрирующий орган'),
                       ('legal_address', 'Адрес для обращений')]:
        if profile[key]:
            legal_rows.append(f'<div><dt>{label}</dt><dd>{lines(profile[key])}</dd></div>')
    if legal_rows:
        about += '<section class="legal-info"><h3>Реквизиты</h3><dl class="business-info">' + ''.join(legal_rows) + '</dl>'
        if profile['registry_url']:
            about += f'<a class="registry-link" href="{escape(profile["registry_url"], quote=True)}" target="_blank" rel="noopener noreferrer">Карточка предприятия {arrow}</a>'
        about += '</section>'
    return f'''<footer class="site-footer" id="site-footer">
<div class="end-line" aria-hidden="true"></div>
<nav class="footer-nav" aria-label="Информация о кофейне">
<a class="footer-link" href="#social-dialog" data-open-dialog="social-dialog" aria-haspopup="dialog" aria-controls="social-dialog">{escape(profile['handle'])}</a>
<a class="footer-link" href="{escape(profile['review_url'], quote=True)}" target="_blank" rel="noopener noreferrer">Оставить отзыв</a>
<a class="footer-link" href="#about-dialog" data-open-dialog="about-dialog" aria-haspopup="dialog" aria-controls="about-dialog">О кофейне</a>
</nav>
</footer>
<dialog class="info-dialog" id="social-dialog" aria-labelledby="social-title">
{close}
<p class="dialog-eyebrow">{escape(profile['handle'])}</p>
<h2 id="social-title">Мы на связи</h2>
<div class="social-links">{social_links}</div>
</dialog>
<dialog class="info-dialog" id="about-dialog" aria-labelledby="about-title">
{close}
<p class="dialog-eyebrow">{escape(profile['name'])}</p>
<h2 id="about-title">О кофейне</h2>
{about}
</dialog>'''
