"""Private Telegram menu admin. Run only in the dedicated Actions checkout."""
import copy
import hashlib
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
from bot.bulk import MAX_BYTES, apply_csv, export_csv
from bot.menu import MENU_PATH, encode, load, prices, validate

LOG = logging.getLogger('menu-bot')

REPLY_ACTIONS = {
    '🏠 Главное меню': 'home',
    '📥 Массовая загрузка': 'bulk-mode',
    '🕘 История меню': 'history',
    '✖ Отмена': 'home',
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


def dispatch():
    repo = os.environ['GITHUB_REPOSITORY']
    req = urllib.request.Request(
        f'https://api.github.com/repos/{repo}/actions/workflows/deploy.yml/dispatches',
        data=json.dumps({'ref': 'main'}).encode(),
        headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status


def publish(menu, baseline, update_id, description):
    """Optimistic concurrency: never overwrite a menu changed by another editor."""
    validate(menu)
    git('fetch', 'origin', 'main')
    remote = validate(json.loads(git('show', 'FETCH_HEAD:data/menu.json')))
    if fingerprint(remote) != baseline:
        raise ValueError('Меню уже изменилось. Откройте /start и повторите правку в актуальной версии.')
    git('merge', '--ff-only', 'FETCH_HEAD')
    if fingerprint(menu) == fingerprint(remote):
        return False
    MENU_PATH.write_text(encode(menu))
    try:
        git('add', 'data/menu.json')
        git('commit', '-m', description, '-m', f'Telegram-update: {update_id}')
    except Exception:
        git('restore', '--staged', '--worktree', 'data/menu.json')
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
        info = self.api('getFile', {'file_id': document['file_id']})
        path = info['file_path']
        if '..' in path or not all(c.isalnum() or c in '_./-' for c in path):
            raise ValueError('Не удалось получить файл Telegram.')
        try:
            with urllib.request.urlopen(f'https://api.telegram.org/file/bot{self.token}/{path}', timeout=40) as response:
                content = response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError):
            raise RuntimeError('Telegram file download failed') from None
        if len(content) > MAX_BYTES:
            raise ValueError('Максимальный размер CSV — 512 КБ.')
        try:
            return content.decode('utf-8-sig')
        except UnicodeError:
            raise ValueError('Сохраните файл в кодировке CSV UTF-8 и отправьте заново.') from None

    def refresh(self, uid):
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
        buttons += [('📥 Массовая загрузка', ('bulk-mode',)),
                    ('＋ Раздел', ('input', 'section-add')), ('🕘 История меню', ('history', 0)),
                    ('↻ Опубликовать повторно', ('deploy',))]
        self.send(uid, 'Управление меню\nВыберите раздел. 🙈 — скрыт с сайта.\n/start — обновить меню, /cancel — отменить ввод.', buttons)

    def section(self, uid, sid):
        s = self.find(uid, sid)[0]
        buttons = [(g['heading'] or g['caption'], ('group', sid, g['id'])) for g in s['groups']]
        buttons += [('✎ Название раздела', ('input', 'section-name', sid)),
                    ('Показать на сайте' if s['hidden'] else 'Скрыть с сайта', ('toggle', sid)),
                    ('＋ Группа позиций', ('input', 'group-add', sid)),
                    ('Удалить раздел', ('delete-section', sid)), ('← Все разделы', ('home',))]
        self.send(uid, s['name'] + ('\nСкрыт с сайта; все позиции сохранены.' if s['hidden'] else '\nВыберите группу позиций.'), buttons)

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
            ('Удалить позицию', ('delete-item', sid, gid, iid)),
            ('← Позиции', ('group', sid, gid))])

    def find(self, uid, sid, gid=None, iid=None, menu=None):
        m = menu if menu is not None else self.sessions[uid]['menu']
        s = next(s for s in m['sections'] if s['id'] == sid)
        g = next(g for g in s['groups'] if g['id'] == gid) if gid else None
        i = next(i for i in g['items'] if i['id'] == iid) if iid else None
        return s,g,i

    def preview(self, uid, menu, description, changes=None):
        validate(menu)
        self.sessions[uid]['draft'] = (menu, description)
        self.sessions[uid].pop('input', None)
        self.sessions[uid]['bulk_changes'] = changes or []
        self.show_preview(uid)

    def show_preview(self, uid):
        state = self.sessions[uid]
        _, description = state['draft']
        buttons = [('✓ Сохранить', ('save',))]
        if state.get('bulk_changes'):
            buttons.insert(0, ('📋 Все изменения', ('bulk-preview', 0)))
        buttons.append(('Отмена', ('home',)))
        self.send(uid, description + '\n\nСохранить и опубликовать? Предыдущая версия останется в истории.',
                  buttons)

    def bulk_start(self, uid, mode, scope=None):
        state = self.sessions[uid]
        state['bulk'] = (mode, scope)
        state['input'] = ('bulk', ())
        state.pop('draft', None)
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
        prompts = {
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
        self.send(uid, prompt, [('Отмена', ('home',))])

    def input(self, uid, raw):
        state = self.sessions[uid]
        kind, ids = state['input']
        if kind == 'bulk':
            return self.bulk_input(uid, raw)
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
        self.preview(uid, menu, f'{names[kind]}{": " + target if target else ""}\n{raw}')

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
        if op == 'section': return self.section(uid, *args)
        if op == 'group': return self.group(uid, *args)
        if op == 'item': return self.item(uid, *args)
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
            menu, description = self.sessions[uid].pop('draft')
            changed = publish(menu, self.sessions[uid]['base'], update_id, ' — '.join(description.splitlines()[:2])[:180])
            if changed:
                try:
                    dispatch()
                    message = 'Сохранено в истории GitHub. Публикация сайта запущена; обычно она занимает несколько минут.'
                except Exception:
                    message = 'Меню сохранено в GitHub, но запуск публикации не удался. Нажмите «Опубликовать повторно» в главном меню.'
            else: message = 'Изменений нет.'
            self.send(uid, message, [('← Меню', ('home',))])
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
                    if 'document' in msg:
                        if self.sessions[uid]['input'][0] != 'bulk':
                            raise ValueError('Файлы принимаются в разделе «Массовая загрузка».')
                        text = self.read_document(msg['document'])
                    self.input(uid, text)
                else:
                    self.send(uid, 'Откройте /start для управления меню.')
        except ValueError as error:
            buttons = [('← Главное меню', ('home',))]
            if self.sessions.get(uid, {}).get('input', (None,))[0] == 'bulk':
                buttons.insert(0, ('📄 Скачать шаблон', ('bulk-template',)))
            self.send(uid, str(error) + ('\nИсправьте файл и отправьте его снова.' if len(buttons) > 1 else ''), buttons)
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
        while self.running and time.monotonic() < end:
            try:
                updates = self.api('getUpdates', {'offset': offset, 'timeout': 25, 'allowed_updates': ['message', 'callback_query']})
                for update in updates:
                    self.handle(update)
                    offset = update['update_id'] + 1
                    cursor.write_text(str(offset))
            except Exception as error:
                LOG.warning('Poll failed: %s; retrying', type(error).__name__)
                time.sleep(5)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    Bot().run()
