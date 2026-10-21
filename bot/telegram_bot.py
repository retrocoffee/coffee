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
from bot.menu import MENU_PATH, encode, load, prices, validate

LOG = logging.getLogger('menu-bot')


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
        self.running = True

    def api(self, method, payload):
        request = urllib.request.Request(
            f'https://api.telegram.org/bot{self.token}/{method}',
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                data = json.load(response)
            if not data.get('ok'):
                raise RuntimeError('Telegram rejected request')
            return data['result']
        except urllib.error.HTTPError as error:
            # Do not print exception URLs: Telegram embeds its token in the URL.
            raise RuntimeError(f'Telegram HTTP {error.code}') from None
        except urllib.error.URLError:
            raise RuntimeError('Telegram network error') from None

    def send(self, uid, text, buttons=()):
        # Expire previous keyboards, preventing accidental replay of a Save button.
        self.callbacks = {k: v for k, v in self.callbacks.items() if v[0] != uid}
        rows = []
        for label, action in buttons:
            key = secrets.token_hex(10)
            self.callbacks[key] = (uid, action)
            rows.append([{'text': label[:64], 'callback_data': key}])
        self.api('sendMessage', {'chat_id': uid, 'text': text[:4000],
                                'reply_markup': {'inline_keyboard': rows}})

    def home(self, uid, page=0):
        git('fetch', 'origin', 'main')
        git('merge', '--ff-only', 'FETCH_HEAD')
        menu = load()
        self.sessions[uid] = {'menu': menu, 'base': fingerprint(menu)}
        sections = menu['sections']
        buttons = [(('🙈 ' if s['hidden'] else '') + s['name'], ('section', s['id'])) for s in sections[page*15:(page+1)*15]]
        if page: buttons.append(('← Предыдущие разделы', ('home', page-1)))
        if (page+1)*15 < len(sections): buttons.append(('Следующие разделы →', ('home', page+1)))
        buttons += [('＋ Раздел', ('input', 'section-add')), ('🕘 История меню', ('history', 0)),
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

    def preview(self, uid, menu, description):
        validate(menu)
        self.sessions[uid]['draft'] = (menu, description)
        self.sessions[uid].pop('input', None)
        self.send(uid, description + '\n\nСохранить и опубликовать? Предыдущая версия останется в истории.',
                  [('✓ Сохранить', ('save',)), ('Отмена', ('home',))])

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
            if callback:
                entry = self.callbacks.get(callback.get('data'))
                if not entry or entry[0] != uid:
                    return self.send(uid, 'Кнопка устарела. Откройте /start.')
                self.action(uid, entry[1], update['update_id'])
            else:
                text = msg.get('text', '')
                if text.split('@')[0] in ('/start', '/cancel'):
                    self.home(uid)
                elif uid in self.sessions and 'input' in self.sessions[uid]:
                    self.input(uid, text)
                else:
                    self.send(uid, 'Откройте /start для управления меню.')
        except ValueError as error:
            self.send(uid, str(error), [('← Главное меню', ('home',))])
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
