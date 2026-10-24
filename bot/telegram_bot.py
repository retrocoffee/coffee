"""Private Telegram menu admin. Run only in the dedicated Actions checkout."""
import copy
import hashlib
import io
import json
import logging
import os
import secrets
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from bot.bulk import MAX_BYTES, apply_csv, export_csv
from bot.menu import MENU_PATH, encode, load, prices, validate
from bot.images import MAX_IMAGE_BYTES, prepare_image, valid_image_path
from bot.generation import SETUP_MESSAGE, available, description_text, generate_image
from bot import site as cafe
from bot import generation_settings as artwork
from bot import carousel as slides

LOG = logging.getLogger('menu-bot')

REPLY_ACTIONS = {
    '🏠 Главное меню': 'home',
    '📥 Массовая загрузка': 'bulk-mode',
    '🕘 История меню': 'history',
    '✖ Отмена': 'home',
    'ℹ О кофейне': 'site',
    '⚙ Генерация': 'generation-settings',
    '🎠 Карусель': 'carousel',
}


class TelegramError(RuntimeError):
    def __init__(self, code, description=''):
        super().__init__(f'Telegram HTTP {code}')
        self.code = code
        self.not_modified = 'message is not modified' in description.lower()


def git(*args):
    return subprocess.check_output(['git', *args], text=True, stderr=subprocess.PIPE).strip()


def fingerprint(menu):
    return hashlib.sha256(encode(menu).encode()).hexdigest()


def site_fingerprint(profile):
    return hashlib.sha256(cafe.encode(profile).encode()).hexdigest()


def generation_fingerprint(settings):
    return hashlib.sha256(artwork.encode(settings).encode()).hexdigest()


def publish_generation_settings(settings, baseline, update_id):
    """Commit just shared preferences, preserving concurrent menu and site edits."""
    artwork.validate(settings)
    git('fetch', 'origin', 'main')
    remote = artwork.validate(json.loads(git('show', f'FETCH_HEAD:{artwork.SETTINGS_PATH}')))
    if generation_fingerprint(remote) != baseline:
        raise ValueError('Настройки генерации уже изменились. Откройте /start → «Настройки генерации» и повторите правку.')
    git('merge', '--ff-only', 'FETCH_HEAD')
    if generation_fingerprint(settings) == generation_fingerprint(remote):
        return False
    try:
        artwork.SETTINGS_PATH.write_text(artwork.encode(settings))
        git('add', '--', str(artwork.SETTINGS_PATH))
        git('commit', '-m', 'Обновление настроек генерации', '-m', f'Telegram-update: {update_id}')
    except Exception:
        git('restore', '--staged', '--worktree', str(artwork.SETTINGS_PATH))
        raise
    try:
        git('push', 'origin', 'HEAD:main')
    except Exception:
        git('reset', '--hard', 'HEAD^')
        raise ValueError('Не удалось сохранить настройки в GitHub. Откройте /start и повторите правку.') from None
    return True


def publish_site(profile, baseline, update_id, description):
    """Publish only the cafe profile; concurrent menu edits remain intact."""
    cafe.validate(profile)
    git('fetch', 'origin', 'main')
    remote = cafe.validate(json.loads(git('show', 'FETCH_HEAD:data/site.json')))
    if site_fingerprint(remote) != baseline:
        raise ValueError('Сведения о кофейне уже изменились. Откройте /start и повторите правку.')
    git('merge', '--ff-only', 'FETCH_HEAD')
    if site_fingerprint(profile) == site_fingerprint(remote):
        return False
    try:
        cafe.SITE_PATH.write_text(cafe.encode(profile))
        git('add', '--', str(cafe.SITE_PATH))
        git('commit', '-m', description, '-m', f'Telegram-update: {update_id}')
    except Exception:
        git('restore', '--staged', '--worktree', str(cafe.SITE_PATH))
        raise
    try:
        git('push', 'origin', 'HEAD:main')
    except Exception:
        git('reset', '--hard', 'HEAD^')
        raise ValueError('Не удалось сохранить сведения в GitHub. Обновите данные через /start и повторите.') from None
    return True


def dispatch():
    repo = os.environ['GITHUB_REPOSITORY']
    req = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/actions/workflows/deploy.yml/dispatches',
        data=json.dumps({'ref': 'main'}).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status


def publish(menu, baseline, update_id, description, assets=None):
    """Optimistic concurrency: never overwrite a menu changed by another editor."""
    validate(menu)
    git('fetch', 'origin', 'main')
    remote = validate(json.loads(git('show', 'FETCH_HEAD:data/menu.json')))
    if fingerprint(remote) != baseline:
        raise ValueError('Меню уже изменилось. Откройте /start и повторите правку в актуальной версии.')
    git('merge', '--ff-only', 'FETCH_HEAD')
    if fingerprint(menu) == fingerprint(remote):
        return False
    assets = assets or {}
    references = {i['image'] for s in menu['sections'] for g in s['groups'] for i in g['items'] if 'image' in i}
    for image, content in assets.items():
        if (not valid_image_path(image) or image not in references or len(content) > MAX_IMAGE_BYTES
                or Path(image).stem != hashlib.sha256(content).hexdigest()):
            raise ValueError('Некорректная картинка. Загрузите её заново.')
    for image in references:
        if image not in assets and not Path('public', image[2:]).is_file():
            raise ValueError('Картинка этой версии недоступна. Загрузите её заново.')
    paths = [str(MENU_PATH)]
    new_paths = []
    try:
        for image, content in assets.items():
            path = Path('public', image[2:])
            if path.exists():
                if path.read_bytes() != content:
                    raise ValueError('Картинка повреждена. Загрузите её заново.')
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            new_paths.append(path)
            path.write_bytes(content)
            paths.append(str(path))
        MENU_PATH.write_text(encode(menu))
        git('add', '--', *paths)
        git('commit', '-m', description, '-m', f'Telegram-update: {update_id}')
    except Exception:
        git('restore', '--staged', '--worktree', 'data/menu.json')
        if new_paths:
            git('reset', '--', *(str(p) for p in new_paths))
            for path in new_paths:
                path.unlink(missing_ok=True)
        raise
    try:
        git('push', 'origin', 'HEAD:main')
    except Exception:
        # Roll back only the bot's unpublished commit in this disposable checkout.
        git('reset', '--hard', 'HEAD^')
        raise ValueError('Не удалось сохранить в GitHub. Возможно, меню изменилось. Откройте /start и повторите.')
    return True


class Bot:
    def __init__(self):
        self.token = os.environ['TELEGRAM_BOT_TOKEN']
        self.admins = {int(x.strip()) for x in os.environ['TELEGRAM_ADMIN_IDS'].split(',') if x.strip()}
        if not self.admins:
            raise ValueError('TELEGRAM_ADMIN_IDS must not be empty')
        self.sessions = {}
        self.callbacks = {}
        self.message_file = Path('.bot-state/messages.json')
        try:
            self.messages = json.loads(self.message_file.read_text())
        except (OSError, ValueError):
            self.messages = {}
        self.documents = {}
        self.reply_users = set()
        self.generation_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='menu-image')
        self.generations = {}
        self.running = True

    def api(self, method, payload):
        return self.telegram_request(method, json.dumps(payload).encode(), 'application/json')

    def telegram_request(self, method, data, content_type):
        request = urllib.request.Request(
            f'https://api.telegram.org/bot{self.token}/{method}',
            data=data, headers={'Content-Type': content_type})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                data = json.load(response)
            if not data.get('ok'):
                raise TelegramError(data.get('error_code', 0), data.get('description', ''))
            return data['result']
        except urllib.error.HTTPError as error:
            # Do not print exception URLs: Telegram embeds its token in the URL.
            try:
                description = json.loads(error.read()).get('description', '')
            except (ValueError, OSError):
                description = ''
            raise TelegramError(error.code, description) from None
        except urllib.error.URLError:
            raise RuntimeError('Telegram network error') from None

    def ensure_reply_keyboard(self, uid, force=False):
        if uid in self.reply_users and not force:
            return
        result = self.api('sendMessage', {
            'chat_id': uid,
            'text': 'Панель управления. Основные действия — на клавиатуре внизу.',
            'reply_markup': {
                'keyboard': [
                    [{'text': '🏠 Главное меню'}, {'text': '📥 Массовая загрузка'}],
                    [{'text': '🕘 История меню'}, {'text': '✖ Отмена'}],
                    [{'text': 'ℹ О кофейне'}, {'text': '⚙ Генерация'}],
                    [{'text': '🎠 Карусель'}],
                ],
                'resize_keyboard': True,
                'is_persistent': True,
                'one_time_keyboard': False,
                'input_field_placeholder': 'Выберите действие или введите данные',
            },
        })
        if result:
            key = f'keyboard:{uid}'
            previous = self.messages.get(key)
            self.messages[key] = result['message_id']
            if previous and previous != result['message_id']:
                self.delete_message(uid, previous)
        self.reply_users.add(uid)

    def send(self, uid, text, buttons=()):
        # Edit the current screen; activate its new callbacks only after delivery.
        callbacks = {}
        rows = []
        for label, action in buttons:
            key = secrets.token_hex(10)
            callbacks[key] = (uid, action)
            rows.append([{'text': label[:64], 'callback_data': key}])
        payload = {'chat_id': uid, 'text': text[:4000], 'reply_markup': {'inline_keyboard': rows}}
        previous = self.messages.get(str(uid))
        if previous:
            try:
                self.api('editMessageText', dict(payload, message_id=previous))
            except TelegramError as error:
                if error.not_modified:
                    pass
                elif error.code == 400:
                    result = self.api('sendMessage', payload)
                    self.messages[str(uid)] = result['message_id']
                    self.delete_message(uid, previous)
                else:
                    raise
        else:
            result = self.api('sendMessage', payload)
            if result:
                self.messages[str(uid)] = result['message_id']
        self.callbacks = {k: v for k, v in self.callbacks.items() if v[0] != uid}
        self.callbacks.update(callbacks)
        preview_id = self.messages.pop(f'preview:{uid}', None)
        if preview_id:
            self.delete_message(uid, preview_id)
        self.message_file.parent.mkdir(exist_ok=True)
        self.message_file.write_text(json.dumps(self.messages))
        for message_id in self.documents.pop(uid, []):
            self.delete_message(uid, message_id)

    def delete_message(self, uid, message_id):
        try:
            self.api('deleteMessage', {'chat_id': uid, 'message_id': message_id})
        except RuntimeError:
            pass  # Already deleted or outside Telegram's deletion window.

    def send_template(self, uid):
        state = self.sessions[uid]
        _, scope = state['bulk']
        content = export_csv(state['menu'], scope).encode('utf-8')
        boundary = secrets.token_hex(16)
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{uid}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="retrocoffe-menu.csv"\r\n'
                'Content-Type: text/csv; charset=utf-8\r\n\r\n').encode()
        data += content + f'\r\n--{boundary}--\r\n'.encode()
        result = self.telegram_request('sendDocument', data, f'multipart/form-data; boundary={boundary}')
        self.documents.setdefault(uid, []).append(result['message_id'])

    def read_document(self, document):
        if document.get('file_size', MAX_BYTES + 1) > MAX_BYTES:
            raise ValueError('Максимальный размер CSV — 512 КБ.')
        if Path(document.get('file_name', '')).suffix.lower() not in ('.csv', '.tsv', '.txt'):
            raise ValueError('Отправьте CSV-файл из шаблона (UTF-8).')
        content = self.download_file(document['file_id'], MAX_BYTES)
        if len(content) > MAX_BYTES:
            raise ValueError('Максимальный размер CSV — 512 КБ.')
        try:
            return content.decode('utf-8-sig')
        except UnicodeError:
            raise ValueError('Сохраните файл в кодировке CSV UTF-8 и отправьте заново.') from None

    def download_file(self, file_id, limit):
        info = self.api('getFile', {'file_id': file_id})
        path = info['file_path']
        if not path or path.startswith('/') or '..' in path or not all(c.isalnum() or c in '_./-' for c in path):
            raise ValueError('Не удалось получить файл Telegram.')
        try:
            with urllib.request.urlopen(f'https://api.telegram.org/file/bot{self.token}/{path}', timeout=40) as response:
                return response.read(limit + 1)
        except (urllib.error.URLError, OSError):
            raise RuntimeError('Telegram file download failed') from None

    def image_input(self, uid, msg):
        state = self.sessions[uid]
        _, ids = state['input']
        photos = msg.get('photo', [])
        upload = photos[-1] if photos else msg.get('document')
        if not upload:
            raise ValueError('Отправьте фото или файл JPG, PNG, WebP. Для прозрачного фона отправляйте PNG/WebP как файл.')
        if upload.get('file_size', 0) > MAX_IMAGE_BYTES:
            raise ValueError('Отправьте картинку до 5 МБ.')
        image, content = prepare_image(self.download_file(upload['file_id'], MAX_IMAGE_BYTES))
        menu = copy.deepcopy(state['menu'])
        _, _, item = self.find(uid, *ids, menu=menu)
        item['image'] = image
        self.preview(uid, menu, 'Картинка позиции: ' + item['name'] + '\nНовая картинка появится слева от названия и в карусели, если позиция в ней есть.',
                     assets={image: content})

    def refresh(self, uid):
        self.cancel_generation(uid)
        git('fetch', 'origin', 'main')
        git('merge', '--ff-only', 'FETCH_HEAD')
        menu = load()
        self.sessions[uid] = {'menu': menu, 'base': fingerprint(menu)}

    def home(self, uid, page=0):
        self.refresh(uid)
        menu = self.sessions[uid]['menu']
        sections = menu['sections']
        buttons = [(('🙈 ' if s['hidden'] else '') + s['name'], ('section', s['id'])) for s in sections[page*15:(page+1)*15]]
        if page: buttons.append(('← Предыдущие разделы', ('home', page-1)))
        if (page+1)*15 < len(sections): buttons.append(('Следующие разделы →', ('home', page+1)))
        buttons += [('🎠 Карусель', ('carousel',)), ('⚙ Настройки генерации', ('generation-settings',)),
                    ('ℹ Сведения о кофейне', ('site',)), ('📥 Массовая загрузка', ('bulk-mode',)),
                    ('＋ Раздел', ('input', 'section-add')), ('🕘 История меню', ('history', 0)),
                    ('↻ Опубликовать повторно', ('deploy',))]
        self.send(uid, 'Управление меню\nВыберите раздел. 🙈 — скрыт с сайта.\n/start — обновить меню, /cancel — отменить ввод.', buttons)

    def carousel_open(self, uid):
        self.cancel_generation(uid)
        state = self.sessions[uid]
        state.pop('input', None)
        state['carousel_draft'] = slides.load()
        state['carousel_base'] = slides.fingerprint(state['carousel_draft'])
        state['carousel_assets'] = {}
        self.carousel_show(uid)

    def carousel_show(self, uid, index=None, page=0):
        state = self.sessions[uid]
        state.pop('input', None)
        entries = state['carousel_draft']
        def name(entry):
            found = slides.lookup(state['menu'], entry)
            return found[2]['name'] if found else 'Удалена из меню: ' + entry['item']
        if index is not None:
            entry = entries[index]
            found = slides.lookup(state['menu'], entry)
            text = name(entry) + ('\nСкрыта из карусели.' if entry.get('hidden') else '')
            if found:
                text += '\nЦена: ' + '; '.join(found[2]['prices']) + '\nОбъёмы: ' + ('; '.join(found[1]['columns']) or 'из меню')
            description = entry.get('description_override', (' '.join(found[2]['notes']) if found else '') or entry.get('description', ''))
            text += '\nОписание: ' + (description or 'без описания')
            text += '\nКартинка: ' + ('отдельная для карусели' if entry.get('custom_image') else 'из меню или исходной подборки')
            text += '\nНазвание, цена и объём связаны с меню. Своя картинка и описание меняют только карусель.'
            buttons = []
            if index: buttons.append(('↑ Выше', ('carousel-move', index, -1)))
            if index + 1 < len(entries): buttons.append(('↓ Ниже', ('carousel-move', index, 1)))
            buttons += [('Показать' if entry.get('hidden') else 'Скрыть', ('carousel-hide', index)),
                        ('🖼 Заменить картинку', ('carousel-image', index)),
                        ('↺ Картинка из меню', ('carousel-image-reset', index)),
                        ('✎ Описание для карусели', ('carousel-description', index)),
                        ('↺ Описание из меню', ('carousel-description-reset', index)),
                        ('Удалить из карусели', ('carousel-remove-ask', index)),
                        ('← Подборка', ('carousel-list', index // 8))]
            return self.send(uid, text + '\n\nИзменения применятся после сохранения всей подборки.', buttons)
        buttons = [(f'{i+1}. ' + ('🙈 ' if e.get('hidden') else '') + name(e), ('carousel-entry', i))
                   for i, e in enumerate(entries) if page*8 <= i < (page+1)*8]
        if page: buttons.append(('← Назад', ('carousel-list', page-1)))
        if (page+1)*8 < len(entries): buttons.append(('Далее →', ('carousel-list', page+1)))
        buttons += [('＋ Позиция из меню', ('carousel-add',)), ('✓ Сохранить подборку', ('carousel-save-ask',)),
                    ('Отмена изменений', ('home',))]
        pending = slides.fingerprint(entries) != state['carousel_base']
        self.send(uid, f'Карусель: {len(entries)} позиций\nВыберите карточку для изменения порядка, картинки или описания.'
                  + ('\nЕсть несохранённые изменения.' if pending else '')
                  + '\nУдаление из подборки не удаляет позицию из меню. Пустая подборка убирает карусель с сайта.', buttons)

    def carousel_upload(self, uid, msg):
        state = self.sessions[uid]
        _, (index,) = state['input']
        photos = msg.get('photo', [])
        upload = photos[-1] if photos else msg.get('document')
        if not upload or upload.get('file_size', 0) > MAX_IMAGE_BYTES:
            raise ValueError('Отправьте фото или файл JPG, PNG, WebP до 5 МБ.')
        image, data = prepare_image(self.download_file(upload['file_id'], MAX_IMAGE_BYTES))
        state['carousel_draft'][index].update(image=image, custom_image=True)
        state['carousel_assets'][image] = data
        self.carousel_show(uid, index)

    def carousel_action(self, uid, op, args, update_id):
        if op == 'carousel': return self.carousel_open(uid)
        state = self.sessions[uid]
        entries = state['carousel_draft']
        if op == 'carousel-list': return self.carousel_show(uid, page=args[0])
        if op == 'carousel-entry': return self.carousel_show(uid, args[0])
        if op == 'carousel-add':
            if len(entries) >= slides.MAX_ITEMS: raise ValueError('В карусели уже 50 позиций.')
            if not args:
                return self.send(uid, 'Добавить из раздела:', [(s['name'], ('carousel-add', s['id'])) for s in state['menu']['sections']]
                                 + [('← Подборка', ('carousel-list', 0))])
            section = next(s for s in state['menu']['sections'] if s['id'] == args[0])
            if len(args) == 1:
                return self.send(uid, 'Выберите группу:', [(g['heading'] or g['caption'], ('carousel-add', section['id'], g['id'])) for g in section['groups']]
                                 + [('← Разделы', ('carousel-add',))])
            group = next(g for g in section['groups'] if g['id'] == args[1])
            page = args[2] if len(args) > 2 else 0
            keys = {slides.key(e) for e in entries}
            buttons = [(('✓ ' if (section['id'], group['id'], i['id']) in keys else '') + i['name'],
                        ('carousel-pick', section['id'], group['id'], i['id'])) for i in group['items'][page*10:(page+1)*10]]
            if page: buttons.append(('← Назад', ('carousel-add', section['id'], group['id'], page-1)))
            if (page+1)*10 < len(group['items']): buttons.append(('Далее →', ('carousel-add', section['id'], group['id'], page+1)))
            return self.send(uid, 'Выберите позицию. ✓ — уже в подборке. Скрытые разделы и недоступные позиции не показываются на сайте.',
                             buttons + [('← Подборка', ('carousel-list', 0))])
        if op == 'carousel-pick':
            if len(entries) >= slides.MAX_ITEMS: raise ValueError('В карусели уже 50 позиций.')
            if tuple(args) in {slides.key(e) for e in entries}: raise ValueError('Эта позиция уже есть в карусели.')
            entries.append(slides.new_entry(*self.find(uid, *args)))
            return self.carousel_show(uid, len(entries)-1)
        if op == 'carousel-save-ask':
            names = []
            for i, entry in enumerate(entries):
                found = slides.lookup(state['menu'], entry)
                names.append(f'{i+1}. ' + ('🙈 ' if entry.get('hidden') else '') + (found[2]['name'] if found else 'Удалена из меню'))
            summary = '\n'.join(names[:15]) + (f'\nИ ещё {len(names)-15} — см. список подборки.' if len(names)>15 else '')
            return self.send(uid, f'Сохранить карусель ({len(entries)} позиций) и опубликовать сайт?\n\n' + (summary or 'Пустая подборка — карусель будет скрыта.'),
                             [('✓ Сохранить и опубликовать', ('carousel-save',)), ('← Вернуться', ('carousel-list', 0))])
        if op == 'carousel-save':
            changed = slides.publish(entries, state['carousel_base'], state['carousel_assets'], update_id, git)
            state['carousel_base'] = slides.fingerprint(entries)
            state['carousel_assets'] = {}
            message = 'Изменений нет.'
            if changed:
                try:
                    dispatch()
                    message = 'Карусель сохранена. Публикация сайта запущена.'
                except Exception:
                    message = 'Карусель сохранена, но публикация не запущена. Нажмите «Опубликовать повторно» в главном меню.'
            return self.send(uid, message, [('← Подборка', ('carousel-list', 0)), ('← Главное меню', ('home',))])
        index = args[0]
        entry = entries[index]
        if op == 'carousel-move':
            target = index + args[1]
            if not 0 <= target < len(entries): raise ValueError('Нельзя переместить дальше края подборки.')
            entries[index], entries[target] = entries[target], entries[index]
            index = target
        elif op == 'carousel-hide': entry['hidden'] = not entry.get('hidden', False)
        elif op == 'carousel-image':
            state['input'] = ('carousel-image', (index,))
            return self.send(uid, 'Отправьте фото или файл JPG, PNG, WebP до 5 МБ. Картинка изменится только в карусели, после сохранения подборки.',
                             [('Отмена загрузки', ('carousel-entry', index))])
        elif op == 'carousel-image-reset':
            found = slides.lookup(state['menu'], entry)
            if not found: raise ValueError('Позиция удалена из меню. Удалите карточку из подборки.')
            entry['image'] = slides.new_entry(*found)['image']
            entry.pop('custom_image', None)
        elif op == 'carousel-description':
            state['input'] = ('carousel-description', (index,))
            return self.send(uid, 'Отправьте описание для карусели до 1000 символов. «-» — без описания. Меню останется прежним.', [('Отмена ввода', ('carousel-entry', index))])
        elif op == 'carousel-description-reset': entry.pop('description_override', None)
        elif op == 'carousel-remove-ask':
            return self.send(uid, 'Удалить карточку только из подборки? Позиция в меню сохранится.',
                             [('Удалить из подборки', ('carousel-remove', index)), ('Отмена', ('carousel-entry', index))])
        elif op == 'carousel-remove':
            entries.pop(index)
            return self.carousel_show(uid, page=min(index//8, max(0, (len(entries)-1)//8)))
        else: raise ValueError('Неизвестное действие карусели.')
        self.carousel_show(uid, index)

    def section(self, uid, sid):
        s = self.find(uid, sid)[0]
        buttons = [(g['heading'] or g['caption'], ('group', sid, g['id'])) for g in s['groups']]
        buttons += [('✎ Название раздела', ('input', 'section-name', sid)),
                    ('Показать на сайте' if s['hidden'] else 'Скрыть с сайта', ('toggle', sid)),
                    ('＋ Группа позиций', ('input', 'group-add', sid)),
                    ('Удалить раздел', ('delete-section', sid)), ('← Все разделы', ('home',))]
        self.send(uid, s['name'] + ('\nСкрыт с сайта; все позиции сохранены.' if s['hidden'] else '\nВыберите группу позиций.'), buttons)

    def site(self, uid, group=None):
        self.cancel_generation(uid)
        state = self.sessions[uid]
        for key in ('input', 'draft', 'draft_assets', 'site_draft'):
            state.pop(key, None)
        if 'site' not in state:
            state['site'] = cafe.load()
            state['site_base'] = site_fingerprint(state['site'])
        profile = state['site']
        if group:
            if group not in cafe.GROUPS:
                raise ValueError('Неизвестный раздел сведений.')
            buttons = [(label + (' · заполнено' if profile[key] else ' · пусто'), ('site-ask', key))
                       for key, (label, section, _) in cafe.FIELDS.items() if section == group]
            buttons.append(('← О кофейне', ('site',)))
            return self.send(uid, cafe.GROUPS[group] + '\nВыберите поле для редактирования. Публикация — после подтверждения.', buttons)
        missing = cafe.missing(profile)
        status = '\n\nЕщё не заполнено:\n' + '\n'.join('• ' + label for label in missing) if missing else '\n\nВсе поля заполнены.'
        self.send(uid, 'Сведения о кофейне\nКонтакты, реквизиты, соцсети и слова владельца для окон на сайте.' + status,
                  [(name, ('site', key)) for key, name in cafe.GROUPS.items()]
                  + [('Соцсети', ('site-socials',)), ('🕘 История сведений', ('site-history', 0)), ('← Главное меню', ('home',))])

    def site_ask(self, uid, key):
        if key not in cafe.FIELDS:
            raise ValueError('Неизвестное поле.')
        label, group, limit = cafe.FIELDS[key]
        self.sessions[uid]['input'] = ('site-field', (key,))
        current = self.sessions[uid]['site'][key]
        self.send(uid, label + '\nСейчас: ' + (current or 'не заполнено') + f'\n\nВведите новое значение, до {limit} символов. «-» — очистить. '
                  'Текст будет опубликован на сайте и сохранён в истории.', [('Отмена', ('site', group))])

    def site_preview(self, uid, profile, description, back):
        cafe.validate(profile)
        state = self.sessions[uid]
        state.pop('input', None)
        state['site_draft'] = (profile, description)
        self.send(uid, description + '\n\nСохранить и опубликовать? Предыдущая версия останется в истории сведений.',
                  [('✓ Сохранить', ('site-save',)), ('Отмена', back)])

    def site_input(self, uid, raw):
        kind, ids = self.sessions[uid]['input']
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 3500:
            raise ValueError('Введите текст от 1 до 3500 символов.')
        profile = copy.deepcopy(self.sessions[uid]['site'])
        raw = raw.strip()
        if kind == 'site-field':
            key = ids[0]
            profile[key] = '' if raw == '-' else raw
            label, group, _ = cafe.FIELDS[key]
            return self.site_preview(uid, profile, label + '\n' + (profile[key] or 'Поле будет очищено.'), ('site', group))
        lines = raw.splitlines()
        if len(lines) != 2:
            raise ValueError('Нужны две строки: название соцсети и полная HTTPS-ссылка.')
        ident = ids[0] if ids else secrets.token_hex(6)
        social = {'id': ident, 'name': lines[0].strip(), 'url': lines[1].strip()}
        if ids:
            index = next(n for n, value in enumerate(profile['socials']) if value['id'] == ident)
            profile['socials'][index] = social
        else:
            profile['socials'].append(social)
        self.site_preview(uid, profile, 'Соцсеть: ' + social['name'] + '\n' + social['url'], ('site-socials',))

    def site_socials(self, uid):
        self.sessions[uid].pop('input', None)
        self.sessions[uid].pop('site_draft', None)
        links = self.sessions[uid]['site']['socials']
        self.send(uid, 'Соцсети\nЭти ссылки появляются в окне по нажатию подписи внизу сайта.',
                  [(s['name'], ('site-social', s['id'])) for s in links]
                  + [('＋ Соцсеть', ('site-social-ask',)), ('← О кофейне', ('site',))])

    def site_action(self, uid, op, args, update_id):
        state = self.sessions[uid]
        if op == 'site': return self.site(uid, *args)
        if 'site' not in state:
            return self.site(uid)
        if op == 'site-ask': return self.site_ask(uid, *args)
        if op == 'site-socials': return self.site_socials(uid)
        if op == 'site-social':
            social = next(s for s in state['site']['socials'] if s['id'] == args[0])
            return self.send(uid, social['name'] + '\n' + social['url'],
                             [('✎ Название и ссылка', ('site-social-ask', *args)), ('Удалить ссылку', ('site-social-delete', *args)),
                              ('← Соцсети', ('site-socials',))])
        if op == 'site-social-ask':
            state['input'] = ('site-social', tuple(args))
            return self.send(uid, 'Отправьте название соцсети и ссылку двумя строками.\nНапример:\nTelegram\nhttps://t.me/your_cafe',
                             [('Отмена', ('site-socials',))])
        if op == 'site-social-delete':
            profile = copy.deepcopy(state['site'])
            social = next(s for s in profile['socials'] if s['id'] == args[0])
            profile['socials'].remove(social)
            return self.site_preview(uid, profile, 'Удаление ссылки: ' + social['name'], ('site-socials',))
        if op == 'site-history':
            page = args[0]
            rows = git('log', '--format=%H|%cs|%s', '-n', '9', f'--skip={page*8}', '--', 'data/site.json').splitlines()
            buttons = []
            for row in rows[:8]:
                sha, date, subject = row.split('|', 2)
                buttons.append((date + ' ' + subject, ('site-version', sha)))
            if page: buttons.append(('← Новее', ('site-history', page-1)))
            if len(rows) > 8: buttons.append(('Старее →', ('site-history', page+1)))
            buttons.append(('← О кофейне', ('site',)))
            return self.send(uid, 'История сведений о кофейне. Восстановление не меняет меню и цены.', buttons)
        if op in ('site-version', 'site-restore'):
            profile = cafe.validate(json.loads(git('show', f'{args[0]}:data/site.json')))
            if op == 'site-restore':
                return self.site_preview(uid, profile, 'Восстановление сведений из версии ' + args[0][:8], ('site-history', 0))
            summary = '\n\n'.join(label + ': ' + profile[key] for key, (label, _, _) in cafe.FIELDS.items() if profile[key])
            summary += '\n\nСоцсети:\n' + '\n'.join(s['name'] + ': ' + s['url'] for s in profile['socials'])
            return self.send(uid, 'Версия ' + args[0][:8] + '\n\n' + summary[:3600],
                             [('Восстановить', ('site-restore', args[0])), ('← История', ('site-history', 0))])
        if op == 'site-save':
            profile, description = state['site_draft']
            changed = publish_site(profile, state['site_base'], update_id, 'Сведения о кофейне — ' + description.splitlines()[0][:120])
            state['site'], state['site_base'] = profile, site_fingerprint(profile)
            state.pop('site_draft', None)
            message = 'Изменений нет.'
            if changed:
                try:
                    dispatch()
                    message = 'Сведения сохранены в истории. Публикация сайта запущена.'
                except Exception:
                    message = 'Сведения сохранены. Не удалось запустить публикацию; нажмите «Опубликовать повторно» в главном меню.'
            return self.send(uid, message, [('← О кофейне', ('site',))])
        raise ValueError('Неизвестное действие со сведениями о кофейне.')

    def group(self, uid, sid, gid, page=0):
        _, g, _ = self.find(uid, sid, gid)
        items = g['items']
        page = max(0, min(page, max(0, (len(items)-1)//10)))
        buttons = [(i['name'], ('item', sid, gid, i['id'])) for i in items[page*10:(page+1)*10]]
        if page: buttons.append(('← Предыдущие', ('group', sid, gid, page-1)))
        if (page+1)*10 < len(items): buttons.append(('Следующие →', ('group', sid, gid, page+1)))
        buttons += [('＋ Позиция', ('input', 'item-add', sid, gid)),
                    ('📥 Массовая загрузка группы', ('bulk-mode', sid, gid)),
                    ('✎ Объёмы группы', ('input', 'columns', sid, gid)),
                    ('← Раздел', ('section', sid))]
        self.send(uid, g['caption'] + '\nОбъёмы: ' + ('; '.join(g['columns']) or 'не указаны'), buttons)

    def item(self, uid, sid, gid, iid):
        _, g, i = self.find(uid, sid, gid, iid)
        details = '\n'.join(f'{c}: {p}' for c,p in zip(g['columns'] or ['Цена'], i['prices']))
        self.send(uid, i['name'] + '\n' + details + '\n' + '\n'.join(i['notes']), [
            ('₽ Изменить цены', ('input', 'prices', sid, gid, iid)),
            ('✎ Переименовать', ('input', 'item-name', sid, gid, iid)),
            ('✎ Описание', ('input', 'notes', sid, gid, iid)),
            ('🖼 Картинка', ('image', sid, gid, iid)),
            ('Удалить позицию', ('delete-item', sid, gid, iid)),
            ('← Позиции', ('group', sid, gid))])

    def find(self, uid, sid, gid=None, iid=None, menu=None):
        m = menu if menu is not None else self.sessions[uid]['menu']
        s = next(s for s in m['sections'] if s['id'] == sid)
        g = next(g for g in s['groups'] if g['id'] == gid) if gid else None
        i = next(i for i in g['items'] if i['id'] == iid) if iid else None
        return s,g,i

    def image(self, uid, sid, gid, iid):
        self.cancel_generation(uid)
        state = self.sessions[uid]
        for key in ('input', 'draft', 'draft_assets', 'generated', 'generation_request', 'created_item'):
            state.pop(key, None)
        _, _, item = self.find(uid, sid, gid, iid)
        buttons = [('🖼 Заменить картинку' if item.get('image') else '＋ Добавить картинку', ('input', 'image', sid, gid, iid))]
        buttons.append(('✨ Сгенерировать по описанию', ('generation-ask', sid, gid, iid)))
        if item.get('image'):
            buttons.append(('↺ Стандартная иконка', ('image-reset', sid, gid, iid)))
        buttons.append(('← Позиция', ('item', sid, gid, iid)))
        self.send(uid, item['name'] + '\n\n' + ('Загружена своя картинка.' if item.get('image') else 'Сейчас показана стандартная иконка.')
                  + '\nМожно загрузить фото или файл JPG, PNG, WebP до 5 МБ.', buttons)

    def preview(self, uid, menu, description, changes=None, assets=None, generated=None, created_item=None):
        validate(menu)
        self.sessions[uid]['draft'] = (menu, description)
        self.sessions[uid]['draft_assets'] = assets or {}
        self.sessions[uid]['generated'] = generated
        self.sessions[uid]['created_item'] = created_item
        self.sessions[uid].pop('input', None)
        self.sessions[uid]['bulk_changes'] = changes or []
        self.show_preview(uid)

    def show_preview(self, uid):
        state = self.sessions[uid]
        _, description = state['draft']
        buttons = [('✓ Сохранить', ('save',))]
        if state.get('generated'):
            buttons += [('🖼 Показать картинку', ('generation-preview',)),
                        ('↻ Ещё вариант', ('generation-again',))]
        if state.get('bulk_changes'):
            buttons.insert(0, ('📋 Все изменения', ('bulk-preview', 0)))
        buttons.append(('Отмена', ('home',)))
        self.send(uid, description + '\n\nСохранить и опубликовать? Предыдущая версия останется в истории.',
                  buttons)

    def cancel_generation(self, uid):
        job = self.generations.get(uid)
        if job:
            job['cancelled'] = True
            job['future'].cancel()

    def generation_settings_open(self, uid, *ids):
        self.cancel_generation(uid)
        state = self.sessions[uid]
        state.pop('input', None)
        settings = artwork.load()
        state['artwork_draft'] = settings
        state['artwork_base'] = generation_fingerprint(settings)
        state['artwork_context'] = tuple(ids)
        self.generation_settings_show(uid)

    def generation_settings_show(self, uid, field=None, notice=''):
        state = self.sessions[uid]
        settings = state['artwork_draft']
        if field:
            buttons = [(('✓ ' if settings[field] == key else '') + name,
                        ('generation-option', field, key)) for key, name in artwork.choices(field).items()]
            buttons.append(('← Настройки генерации', ('generation-settings-show',)))
            note = ('\nДоступность моделей и расход квоты зависят от аккаунта Cloudflare.' if field == 'model' else '')
            return self.send(uid, artwork.FIELDS[field] + '\nВыберите вариант, затем сохраните настройки.' + note, buttons)
        pending = generation_fingerprint(settings) != state['artwork_base']
        self.send(uid, 'Настройки генерации\n\n' + artwork.summary(settings)
                  + '\n\nОбщие для всех администраторов. Применяются к новым запросам после сохранения. '
                    'Белый фон и отсутствие надписей сохраняются. '
                    'Смена настроек не запускает генерацию и не меняет готовые картинки.'
                  + ('\n\nЕсть несохранённые изменения.' if pending else '')
                  + ('\n\n' + notice if notice else ''),
                  [(label + ': ' + artwork.choices(key)[settings[key]], ('generation-settings-show', key))
                   for key, label in artwork.FIELDS.items()]
                  + [('✓ Сохранить настройки', ('generation-settings-save',)),
                     ('← Вернуться' if not pending else 'Отмена изменений', ('generation-settings-back',))])

    def generation_settings_back(self, uid):
        state = self.sessions[uid]
        ids = state.pop('artwork_context', ())
        state.pop('artwork_draft', None)
        state.pop('artwork_base', None)
        if ids:
            request = state.get('generation_request')
            if request and request['ids'] == ids:
                return self.generation_review(uid, ids, request['prompt'])
            return self.generation_ask(uid, *ids)
        return self.home(uid)

    def generation_ask(self, uid, *ids):
        self.cancel_generation(uid)
        _, _, item = self.find(uid, *ids)
        if not available():
            return self.send(uid, SETUP_MESSAGE, [('← Картинка позиции', ('image', *ids))])
        state = self.sessions[uid]
        state['input'] = ('generation', ids)
        state.pop('generation_request', None)
        current = '\n'.join(item['notes']) or item['name']
        self.send(uid, item['name'] + '\n\nОпишите, что нарисовать: напиток, посуду, ингредиенты. '
                  'Цветность, стиль и модель можно выбрать в настройках ниже. '
                  'Описание позиции можно использовать без изменений.\n\n'
                  'Текущее описание:\n' + current[:1500] + '\n\n' + artwork.summary(artwork.load())
                  + '\n\nГенерация через Cloudflare Workers AI. '
                  'Запрос будет отправлен после нажатия «Сгенерировать».',
                  [('Использовать описание позиции', ('generation-current', *ids)),
                   ('⚙ Настройки генерации', ('generation-settings', *ids)),
                   ('← Картинка позиции', ('image', *ids))])

    def generation_review(self, uid, ids, raw, settings=None):
        prompt = description_text(raw)
        settings = artwork.validate(settings if settings is not None else artwork.load())
        _, _, item = self.find(uid, *ids)
        state = self.sessions[uid]
        state.pop('input', None)
        state['generation_request'] = {'ids': tuple(ids), 'prompt': prompt, 'settings': settings}
        self.send(uid, 'Генерация: ' + item['name'] + '\n\n' + prompt
                  + '\n\n' + artwork.summary(settings)
                  + '\n\nОдин вариант через Cloudflare. На Workers Free действует бесплатная дневная квота. '
                    'Картинка появится на сайте только после «Сохранить».',
                  [('✨ Сгенерировать', ('generation-start',)),
                   ('⚙ Настройки генерации', ('generation-settings', *ids)),
                   ('✎ Изменить описание', ('generation-ask', *ids)),
                   ('← Картинка позиции', ('image', *ids))])

    def generation_start(self, uid):
        if not available():
            raise ValueError(SETUP_MESSAGE)
        # One active HTTP call for the bot; no duplicate submissions or unbounded queue.
        self.poll_generations()
        if self.generations:
            request = self.sessions[uid].get('generation_request', {})
            return self.send(uid, 'Сейчас уже создаётся картинка. Дождитесь окончания и нажмите «Повторить запуск».',
                             [('Повторить запуск', ('generation-start',)),
                              ('← Картинка позиции', ('image', *request['ids']))])
        state = self.sessions[uid]
        request = copy.deepcopy(state['generation_request'])
        _, _, item = self.find(uid, *request['ids'])
        self.send(uid, 'Создаю картинку для «' + item['name'] + '». Обычно это занимает до нескольких минут. '
                  + '\n\n' + artwork.summary(request['settings']) + '\n\n'
                  'Меню бота остаётся доступным. Отмена уберёт результат; запрос уже может израсходовать квоту Cloudflare.',
                  [('Отмена', ('image', *request['ids']))])
        future = self.generation_pool.submit(generate_image, item['name'], request['prompt'], request['settings'])
        self.generations[uid] = {'future': future, 'state': state, 'request': request, 'cancelled': False}

    def poll_generations(self):
        for uid, job in list(self.generations.items()):
            if not job['future'].done():
                continue
            del self.generations[uid]
            if job['cancelled'] or self.sessions.get(uid) is not job['state']:
                continue
            request = job['request']
            try:
                image, content = job['future'].result()
                menu = copy.deepcopy(job['state']['menu'])
                _, _, item = self.find(uid, *request['ids'], menu=menu)
                item['image'] = image
                self.preview(uid, menu, 'Сгенерированная картинка позиции: ' + item['name'],
                             assets={image: content}, generated=request)
            except ValueError as error:
                self.send(uid, str(error), [('Повторить запуск', ('generation-start',)),
                                           ('⚙ Настройки генерации', ('generation-settings', *request['ids'])),
                                           ('Изменить описание', ('generation-ask', *request['ids'])),
                                           ('← Картинка позиции', ('image', *request['ids']))])
                continue
            except Exception:
                LOG.warning('Image generation did not finish; details omitted')
                self.send(uid, 'Не удалось подготовить картинку. Попробуйте ещё раз.',
                          [('Повторить запуск', ('generation-start',)),
                           ('← Картинка позиции', ('image', *request['ids']))])
                continue
            try:
                self.send_generated_preview(uid, refresh=False)
            except RuntimeError:
                # Keep the draft even if Telegram temporarily cannot show its photo.
                LOG.warning('Generated image preview delivery failed; draft retained')

    def send_generated_preview(self, uid, refresh=True):
        from PIL import Image
        state = self.sessions[uid]
        if not state.get('generated') or not state.get('draft_assets'):
            raise ValueError('Предпросмотр устарел. Откройте картинку позиции заново.')
        if refresh:
            self.show_preview(uid)  # Cleans up the previous photo before replacing it.
        content = next(iter(state['draft_assets'].values()))
        with Image.open(io.BytesIO(content)) as source:
            rgba = source.convert('RGBA')
            photo = Image.new('RGB', rgba.size, 'white')
            photo.paste(rgba, mask=rgba.getchannel('A'))
            stream = io.BytesIO()
            photo.save(stream, 'JPEG', quality=90)
        boundary = secrets.token_hex(16)
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{uid}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="preview.jpg"\r\n'
                'Content-Type: image/jpeg\r\n\r\n').encode()
        body += stream.getvalue() + f'\r\n--{boundary}--\r\n'.encode()
        result = self.telegram_request('sendPhoto', body, f'multipart/form-data; boundary={boundary}')
        self.messages[f'preview:{uid}'] = result['message_id']
        self.message_file.parent.mkdir(exist_ok=True)
        self.message_file.write_text(json.dumps(self.messages))

    def bulk_start(self, uid, mode, scope=None):
        self.cancel_generation(uid)
        state = self.sessions[uid]
        state['bulk'] = (mode, scope)
        state['input'] = ('bulk', ())
        state.pop('draft', None)
        state.pop('draft_assets', None)
        target = 'Всё меню' if scope is None else self.find(uid, *scope)[1]['caption']
        self.send(uid, f'Массовая загрузка: {target}\n'
                  + ('Режим: только цены.\n' if mode == 'prices' else 'Режим: позиции и цены.\n')
                  + '\nСкачайте CSV-шаблон с текущими позициями, отредактируйте и отправьте файл сюда. '
                    'Можно вставить строки текстом вместе с заголовком.\n\n'
                    'раздел;группа;id;название;цены;описание\n\n'
                    'Несколько цен: 220 | 260 — в порядке объёмов на сайте. '
                  + ('Пустой id ищет позицию по названию; новый товар не создаётся. '
                     if mode == 'prices' else 'Для новой позиции оставьте id пустым. '
                     'Заполненный id позволяет переименовать существующую позицию. ')
                  +
                    'Раздел и группу копируйте из шаблона.\n'
                    'Пустое описание сохраняет прежнее; «-» удаляет описание. '
                    'Строки, пропущенные в файле, сохраняются.\n'
                    'CSV UTF-8, до 512 КБ и 1000 строк. Сначала будет показан предпросмотр.',
                  [('📄 Скачать CSV-шаблон', ('bulk-template',)), ('Отмена', ('home',))])

    def bulk_input(self, uid, raw):
        state = self.sessions[uid]
        mode, scope = state['bulk']
        menu, summary, changes = apply_csv(state['menu'], raw, mode, scope)
        if not changes:
            return self.send(uid, summary + '\nИзменений нет. Можно отправить другой файл.',
                             [('📄 Скачать шаблон', ('bulk-template',)), ('Отмена', ('home',))])
        self.preview(uid, menu, 'Массовая загрузка меню\n' + summary + '\n\n' + '\n\n'.join(changes[:3])
                     + ('\n\nОстальные правки — в «Все изменения».' if len(changes) > 3 else ''), changes)

    def ask(self, uid, kind, *ids):
        self.cancel_generation(uid)
        prompts = {
            'image': 'Отправьте фото или файл JPG, PNG, WebP до 5 МБ и 16 мегапикселей. Для сохранения прозрачности отправляйте PNG/WebP как файл. Затем подтвердите замену кнопкой «Сохранить».',
            'section-add': 'Введите название нового раздела.',
            'section-name': 'Введите новое название раздела.',
            'group-add': 'Введите название группы (например, «Добавки»).',
            'item-name': 'Введите новое название позиции.',
            'notes': 'Введите описание. Каждая строка — отдельная строка описания. «-» — убрать описание.',
            'columns': 'Введите подписи объёмов через «;», например: 200 мл; 300 мл. Количество колонок должно остаться прежним. «-» — убрать единственную подпись.',
            'item-add': 'Введите название позиции первой строкой, цены через «;» второй строкой. Затем можно добавить описание.\nПример:\nКапучино\n220; 260\nНа цельном молоке',
            'prices': 'Введите цены через «;» в порядке объёмов, например: 220; 260. Можно указать «бесплатно» или «—».'}
        prompt = prompts[kind]
        if kind in ('prices', 'item-add'):
            _, g, _ = self.find(uid, *ids)
            prompt += '\nНужно цен: ' + str(max(1, len(g['columns']))) + '. Объёмы: ' + ('; '.join(g['columns']) or 'не указаны')
        self.sessions[uid]['input'] = (kind, ids)
        self.sessions[uid].pop('draft', None)
        self.sessions[uid].pop('draft_assets', None)
        self.send(uid, prompt, [('Отмена', ('home',))])

    def input(self, uid, raw):
        state = self.sessions[uid]
        kind, ids = state['input']
        if kind == 'carousel-description':
            if not isinstance(raw, str) or not raw.strip() or len(raw) > 1000:
                raise ValueError('Описание — от 1 до 1000 символов; «-» убирает описание.')
            state['carousel_draft'][ids[0]]['description_override'] = '' if raw.strip() == '-' else raw.strip()
            return self.carousel_show(uid, ids[0])
        if kind == 'bulk':
            return self.bulk_input(uid, raw)
        if kind in ('site-field', 'site-social'):
            return self.site_input(uid, raw)
        if kind == 'generation':
            return self.generation_review(uid, ids, raw)
        menu = copy.deepcopy(state['menu'])
        raw = raw.strip()
        if not raw or len(raw) > 3500:
            raise ValueError('Введите текст длиной от 1 до 3500 символов.')
        s,g,i = self.find(uid, *ids, menu=menu) if ids else (None,None,None)
        if kind == 'section-add':
            menu['sections'].append({'id': secrets.token_hex(6), 'name': raw, 'hidden': False,
                'groups': [{'id': secrets.token_hex(6), 'kind': 'drinks', 'heading': '', 'caption': raw, 'columns': [], 'items': []}]})
        elif kind == 'section-name': s['name'] = raw
        elif kind == 'group-add':
            s['groups'].append({'id': secrets.token_hex(6), 'kind': 'drinks', 'heading': raw, 'caption': raw, 'columns': [], 'items': []})
        elif kind == 'item-name': i['name'] = raw
        elif kind == 'notes': i['notes'] = [] if raw == '-' else raw.splitlines()
        elif kind == 'prices': i['prices'] = prices(raw, max(1, len(g['columns'])))
        elif kind == 'columns':
            cols = [] if raw == '-' else [x.strip() for x in raw.split(';')]
            if max(1, len(cols)) != max(1, len(g['columns'])) and g['items']:
                raise ValueError('В группе уже есть позиции. Количество объёмов менять нельзя; создайте отдельную группу.')
            g['columns'] = cols
        elif kind == 'item-add':
            lines = raw.splitlines()
            if len(lines) < 2: raise ValueError('Нужны название и цены на отдельных строках.')
            g['items'].append({'id': secrets.token_hex(6), 'name': lines[0], 'prices': prices(lines[1], max(1, len(g['columns']))), 'notes': lines[2:]})
        names = {'section-add':'Добавление раздела', 'section-name':'Название раздела', 'group-add':'Добавление группы',
                 'item-name':'Название позиции', 'notes':'Описание позиции', 'prices':'Цены позиции', 'columns':'Объёмы группы', 'item-add':'Добавление позиции'}
        target = (i or g or s or {}).get('name', '')
        created = (*ids, g['items'][-1]['id']) if kind == 'item-add' else None
        self.preview(uid, menu, f'{names[kind]}{": " + target if target else ""}\n{raw}', created_item=created)

    def history(self, uid, page):
        entries = git('log', '--format=%H|%cs|%s', '-n', '9', f'--skip={page*8}', '--', 'data/menu.json').splitlines()
        buttons=[]
        for line in entries[:8]:
            sha, date, subject = line.split('|', 2)
            buttons.append((date+' '+subject, ('version', sha)))
        if page: buttons.append(('← Новее', ('history', page-1)))
        if len(entries)>8: buttons.append(('Старее →', ('history', page+1)))
        buttons.append(('← Главное меню', ('home',)))
        self.send(uid, 'История меню. Выберите версию, чтобы посмотреть или восстановить её.', buttons)

    def action(self, uid, action, update_id):
        op, *args = action
        if op == 'home': return self.home(uid, *args)
        if uid not in self.sessions:
            return self.send(uid, 'Бот перезапущен. Откройте /start.')
        if op == 'carousel' or op.startswith('carousel-'):
            return self.carousel_action(uid, op, args, update_id)
        if op == 'site' or op.startswith('site-'):
            return self.site_action(uid, op, args, update_id)
        if op == 'section': return self.section(uid, *args)
        if op == 'group': return self.group(uid, *args)
        if op == 'item': return self.item(uid, *args)
        if op == 'image': return self.image(uid, *args)
        if op == 'generation-settings': return self.generation_settings_open(uid, *args)
        if op == 'generation-settings-show': return self.generation_settings_show(uid, *args)
        if op == 'generation-option':
            field, value = args
            if value not in artwork.choices(field):
                raise ValueError('Неизвестный вариант настройки генерации.')
            self.sessions[uid]['artwork_draft'][field] = value
            return self.generation_settings_show(uid)
        if op == 'generation-settings-back': return self.generation_settings_back(uid)
        if op == 'generation-settings-save':
            state = self.sessions[uid]
            changed = publish_generation_settings(state['artwork_draft'], state['artwork_base'], update_id)
            state['artwork_base'] = generation_fingerprint(state['artwork_draft'])
            return self.generation_settings_show(uid, notice='Сохранено в GitHub. Новые генерации используют эти настройки.' if changed else 'Изменений нет.')
        if op == 'generation-ask': return self.generation_ask(uid, *args)
        if op == 'generation-current':
            _, _, item = self.find(uid, *args)
            return self.generation_review(uid, args, '\n'.join(item['notes']) or item['name'])
        if op == 'generation-start': return self.generation_start(uid)
        if op == 'generation-preview': return self.send_generated_preview(uid)
        if op == 'generation-again':
            request = self.sessions[uid]['generated']
            return self.generation_review(uid, request['ids'], request['prompt'], request['settings'])
        if op == 'input': return self.ask(uid, *args)
        if op == 'bulk-mode':
            return self.send(uid, 'Выберите режим массовой загрузки. Все изменения сохраняются одним подтверждением.',
                             [('₽ Только цены', ('bulk', 'prices', *args)),
                              ('＋ Позиции и цены', ('bulk', 'items', *args)), ('Отмена', ('home',))])
        if op == 'bulk': return self.bulk_start(uid, args[0], tuple(args[1:]) or None)
        if op == 'bulk-template':
            mode, scope = self.sessions[uid]['bulk']
            self.bulk_start(uid, mode, scope)
            return self.send_template(uid)
        if op == 'bulk-confirm': return self.show_preview(uid)
        if op == 'bulk-preview':
            page = args[0]
            changes = self.sessions[uid]['bulk_changes']
            buttons = []
            if page: buttons.append(('← Назад', ('bulk-preview', page-1)))
            if (page+1)*3 < len(changes): buttons.append(('Далее →', ('bulk-preview', page+1)))
            buttons.append(('← Подтверждение', ('bulk-confirm',)))
            return self.send(uid, '\n\n'.join(changes[page*3:(page+1)*3]), buttons)
        if op == 'history': return self.history(uid, *args)
        if op == 'version-menu':
            sha, page = args
            menu = validate(json.loads(git('show', f'{sha}:data/menu.json')))
            lines = [s['name'] + (' (скрыт)' if s['hidden'] else '') + '\n' + i['name'] + '\n' +
                     '; '.join(f'{c}: {p}' for c, p in zip(g['columns'] or ['Цена'], i['prices']))
                     for s in menu['sections'] for g in s['groups'] for i in g['items']]
            buttons = []
            if page: buttons.append(('← Назад', ('version-menu', sha, page-1)))
            if (page+1)*6 < len(lines): buttons.append(('Далее →', ('version-menu', sha, page+1)))
            buttons.append(('← Версия', ('version', sha)))
            return self.send(uid, '\n\n'.join(lines[page*6:(page+1)*6]) or 'Позиции отсутствуют.', buttons)
        if op == 'deploy':
            dispatch()
            return self.send(uid, 'Публикация сайта запущена.', [('← Меню', ('home',))])
        if op == 'save':
            state = self.sessions[uid]
            menu, description = state['draft']
            changed = publish(menu, state['base'], update_id, ' — '.join(description.splitlines()[:2])[:180], state.get('draft_assets'))
            state.pop('draft', None)
            state.pop('draft_assets', None)
            state['menu'], state['base'] = menu, fingerprint(menu)
            state.pop('generated', None)
            state.pop('generation_request', None)
            if changed:
                try:
                    dispatch()
                    message = 'Сохранено в истории GitHub. Публикация сайта запущена; обычно она занимает несколько минут.'
                except Exception:
                    message = 'Меню сохранено в GitHub, но запуск публикации не удался. Нажмите «Опубликовать повторно» в главном меню.'
            else: message = 'Изменений нет.'
            buttons = [('← Меню', ('home',))]
            created = state.pop('created_item', None)
            if created:
                buttons.insert(0, ('🖼 Картинка новой позиции', ('image', *created)))
            self.send(uid, message, buttons)
            return
        if op == 'version':
            sha = args[0]
            menu = validate(json.loads(git('show', f'{sha}:data/menu.json')))
            details = '\n'.join(('🙈 ' if s['hidden'] else '') + s['name'] + f' — {sum(len(g["items"]) for g in s["groups"])} поз.' for s in menu['sections'])
            self.send(uid, f'Версия {sha[:8]}\n{details}', [('Позиции и цены', ('version-menu', sha, 0)), ('Восстановить эту версию', ('restore', sha)), ('← История', ('history', 0))])
            return
        if op == 'restore':
            menu = validate(json.loads(git('show', f'{args[0]}:data/menu.json')))
            return self.preview(uid, menu, 'Восстановление меню из версии ' + args[0][:8])
        menu = copy.deepcopy(self.sessions[uid]['menu'])
        s,g,i = self.find(uid, *args, menu=menu)
        if op == 'toggle':
            s['hidden'] = not s['hidden']
            description = ('Скрытие раздела: ' if s['hidden'] else 'Показ раздела: ') + s['name']
        elif op == 'delete-section':
            menu['sections'].remove(s)
            description = 'Удаление раздела со всеми позициями: ' + s['name']
        elif op == 'delete-item':
            g['items'].remove(i)
            description = 'Удаление позиции: ' + i['name']
        elif op == 'image-reset':
            i.pop('image', None)
            description = 'Стандартная иконка позиции: ' + i['name']
        else: raise ValueError('Неизвестная команда. Откройте /start.')
        self.preview(uid, menu, description)

    def handle(self, update):
        callback = update.get('callback_query')
        msg = callback.get('message', {}) if callback else update.get('message', {})
        actor = callback.get('from', {}) if callback else msg.get('from', {})
        uid = actor.get('id')
        authorized = uid in self.admins and msg.get('chat', {}).get('type') == 'private' and msg.get('chat', {}).get('id') == uid
        if callback:
            self.api('answerCallbackQuery', {'callback_query_id': callback['id'], 'text': '' if authorized else 'Нет доступа'})
        if not authorized: return
        try:
            self.ensure_reply_keyboard(uid, force=msg.get('text', '').strip() == '/start' and not callback)
            if callback:
                message_id = msg.get('message_id')
                current = self.messages.get(str(uid))
                if message_id and not current:
                    self.messages[str(uid)] = message_id
                elif message_id and current != message_id:
                    self.delete_message(uid, message_id)
                entry = self.callbacks.get(callback.get('data'))
                if not entry or entry[0] != uid:
                    return self.send(uid, 'Кнопка устарела. Откройте /start.')
                self.action(uid, entry[1], update['update_id'])
            else:
                text = msg.get('text', '')
                if text in REPLY_ACTIONS:
                    action = REPLY_ACTIONS[text]
                    if action == 'home':
                        self.home(uid)
                    else:
                        self.refresh(uid)
                        self.action(uid, (action, 0) if action == 'history' else (action,), update['update_id'])
                    if msg.get('message_id'):
                        self.delete_message(uid, msg['message_id'])
                elif text.split('@')[0] in ('/start', '/cancel'):
                    self.home(uid)
                elif uid in self.sessions and 'input' in self.sessions[uid]:
                    if self.sessions[uid]['input'][0] == 'carousel-image':
                        return self.carousel_upload(uid, msg)
                    if self.sessions[uid]['input'][0] == 'image':
                        return self.image_input(uid, msg)
                    if 'document' in msg:
                        if self.sessions[uid]['input'][0] != 'bulk':
                            raise ValueError('Для картинки откройте позицию → «Картинка». Для CSV — «Массовая загрузка».')
                        text = self.read_document(msg['document'])
                    self.input(uid, text)
                else:
                    self.send(uid, 'Откройте /start для управления меню.')
        except ValueError as error:
            buttons = [('← Главное меню', ('home',))]
            if 'carousel_draft' in self.sessions.get(uid, {}):
                buttons.insert(0, ('← Карусель', ('carousel-list', 0)))
            if self.sessions.get(uid, {}).get('input', (None,))[0] == 'bulk':
                buttons.insert(0, ('📄 Скачать шаблон', ('bulk-template',)))
            hint = '\nИсправьте файл и отправьте его снова.' if len(buttons) > 1 else ''
            if self.sessions.get(uid, {}).get('input', (None,))[0] == 'image':
                hint = '\nМожно отправить другую картинку или отменить загрузку.'
                ids = self.sessions[uid]['input'][1]
                buttons.insert(0, ('← Картинка позиции', ('image', *ids)))
            if self.sessions.get(uid, {}).get('input', (None,))[0] in ('site-field', 'site-social'):
                hint = '\nИсправьте значение и отправьте снова.'
                buttons.insert(0, ('← О кофейне', ('site',)))
            self.send(uid, str(error) + hint, buttons)
        except Exception:
            LOG.error('Operation failed (%s); credentials and message contents omitted', type(update).__name__)
            self.send(uid, 'Операция не завершена. Откройте /start и проверьте актуальное меню.', [('← Меню', ('home',))])

    def run(self):
        self.api('getMe', {})
        self.api('deleteWebhook', {'drop_pending_updates': False})
        cursor = Path('.bot-state/offset')
        cursor.parent.mkdir(exist_ok=True)
        offset = int(cursor.read_text()) if cursor.exists() else 0
        end = time.monotonic() + int(os.environ.get('BOT_RUN_SECONDS', '14700'))
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: setattr(self, 'running', False))
        try:
            while self.running and time.monotonic() < end:
                try:
                    self.poll_generations()
                    updates = self.api('getUpdates', {'offset': offset, 'timeout': 2 if self.generations else 25,
                                                      'allowed_updates': ['message', 'callback_query']})
                    for update in updates:
                        self.handle(update)
                        offset = update['update_id'] + 1
                        cursor.write_text(str(offset))
                except Exception as error:
                    LOG.warning('Poll failed: %s; retrying', type(error).__name__)
                    time.sleep(5)
        finally:
            for uid in list(self.generations):
                self.cancel_generation(uid)
            self.generation_pool.shutdown(wait=True, cancel_futures=True)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    Bot().run()
