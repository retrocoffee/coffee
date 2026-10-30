"""Editable seasonal settings, with inclusive dates in the cafe's timezone."""
import json
from datetime import date
from pathlib import Path
from bot.menu import local_day

PATH = Path('config/seasonal.json')


def validate(config):
    if not isinstance(config, dict) or type(config.get('halloween')) is not bool:
        raise ValueError('halloween должен быть true или false.')
    until = config.get('until', '')
    if not isinstance(until, str):
        raise ValueError('Дата: ГГГГ-ММ-ДД или «-» для снятия срока.')
    if until:
        try: date.fromisoformat(until)
        except ValueError: raise ValueError('Дата: ГГГГ-ММ-ДД или «-» для снятия срока.')
    return config


def load():
    return validate(json.loads(PATH.read_text())) if PATH.exists() else {'halloween': False}


def enabled():
    config = load()
    return config['halloween'] and (not config.get('until') or local_day().isoformat() <= config['until'])


def publish(config, baseline, update_id, git):
    validate(config)
    git('fetch', 'origin', 'main')
    remote = validate(json.loads(git('show', 'FETCH_HEAD:config/seasonal.json')))
    if remote != baseline:
        raise ValueError('Оформление уже изменилось. Откройте настройки заново.')
    git('merge', '--ff-only', 'FETCH_HEAD')
    if remote == config: return False
    try:
        PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
        git('add', '--', str(PATH))
        git('commit', '-m', 'Настройки сезонного оформления', '-m', f'Telegram-update: {update_id}')
    except Exception:
        git('restore', '--staged', '--worktree', str(PATH))
        raise
    try: git('push', 'origin', 'HEAD:main')
    except Exception:
        git('reset', '--hard', 'HEAD^')
        raise ValueError('Не удалось сохранить оформление. Откройте настройки заново.')
    return True
