import copy
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from bot import season
from bot.menu import render, validate, local_day
from bot.preview import document
from bot.telegram_bot import Bot, fingerprint
from scripts.carousel import render_carousel
from support import sample_menu


class MenuImprovementsTests(unittest.TestCase):
    def test_sizes_stay_paired_with_prices_and_descriptions_expand(self):
        menu = sample_menu()
        item = menu['sections'][1]['groups'][0]['items'][0]
        item['prices'] = ['220 ₽', '280 ₽']
        item['notes'] = ['Состав ' * 30, '<script>']
        html = render(menu)
        self.assertIn('200 мл</span><span>220 ₽', html)
        self.assertIn('300 мл</span><span>280 ₽', html)
        self.assertIn('class="item-description"', html)
        self.assertNotIn('<script>', html)

    def test_availability_is_dated_and_does_not_erase_prices(self):
        menu = sample_menu()
        item = menu['sections'][0]['groups'][0]['items'][0]
        item['unavailable_on'] = '2026-10-30'
        with patch('bot.menu.local_day', return_value=date(2026, 10, 30)):
            self.assertIn('class="availability-label">Сегодня нет', render(menu))
            with patch('scripts.carousel.selection.load', return_value=[{
                'section':'seasonal', 'group':'g1', 'item':'i1', 'image':'./assets/menu-icons/coffee.svg'}]):
                self.assertEqual(render_carousel(menu), '')
        with patch('bot.menu.local_day', return_value=date(2026, 10, 31)):
            self.assertIn('class="availability-label" hidden', render(menu))
        self.assertEqual(item['prices'], ['100 ₽'])
        item['unavailable_on'] = 'bad-date'
        with self.assertRaises(ValueError): validate(menu)

    def test_private_preview_is_self_contained_and_escapes_text(self):
        menu = sample_menu()
        menu['sections'][0]['groups'][0]['items'][0]['name'] = '<script>alert(1)</script>'
        html = document(menu).decode()
        self.assertIn('data:image/', html)
        self.assertNotIn('src="./', html)
        self.assertNotIn("url('./", html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_season_expires_after_last_local_day(self):
        with patch('bot.season.load', return_value={'halloween':True, 'until':'2026-10-31'}):
            with patch('bot.season.local_day', return_value=date(2026,10,31)):
                self.assertTrue(season.enabled())
            with patch('bot.season.local_day', return_value=date(2026,11,1)):
                self.assertFalse(season.enabled())
        for config in ({'halloween':'true'}, {'halloween':True,'until':'tomorrow'}):
            with self.assertRaises(ValueError): season.validate(config)

    def test_season_conflict_and_push_failure_preserve_remote(self):
        before = {'halloween': True}
        draft = {'halloween': False}
        git = Mock(side_effect=['', json.dumps(draft)])
        with self.assertRaises(ValueError): season.publish(draft, before, 1, git)
        self.assertEqual(git.call_count, 2)
        with tempfile.TemporaryDirectory() as directory, patch('bot.season.PATH', Path(directory)/'seasonal.json'):
            def run(*args):
                if args[0] == 'show': return json.dumps(before)
                if args[0] == 'push': raise RuntimeError('rejected')
                return ''
            git = Mock(side_effect=run)
            with self.assertRaises(ValueError): season.publish(draft, before, 1, git)
            git.assert_called_with('reset', '--hard', 'HEAD^')

    def test_bot_availability_and_undo_are_confirmed_drafts(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123'):
            bot = Bot()
        bot.generation_pool.shutdown()
        bot.send = Mock()
        menu = sample_menu()
        bot.sessions[123] = {'menu':menu, 'base':fingerprint(menu)}
        bot.action(123, ('availability','seasonal','g1','i1'), 1)
        self.assertNotIn('unavailable_on', menu['sections'][0]['groups'][0]['items'][0])
        self.assertEqual(bot.sessions[123]['draft'][0]['sections'][0]['groups'][0]['items'][0]['unavailable_on'], local_day().isoformat())
        previous = copy.deepcopy(menu)
        previous['sections'][0]['name'] = 'Прежняя версия'
        def run(*args):
            if args[0] == 'log': return 'a'*40 + '\n' + 'b'*40
            if args[0] == 'show': return json.dumps(previous if args[1].startswith('b'*40) else menu)
            return ''
        with patch('bot.telegram_bot.git', side_effect=run):
            bot.action(123, ('undo-menu',), 2)
        self.assertEqual(bot.sessions[123]['menu'], menu)
        self.assertEqual(bot.sessions[123]['draft'][0], previous)
        changed = copy.deepcopy(menu)
        changed['sections'][0]['name'] = 'Изменено другим администратором'
        with patch('bot.telegram_bot.git', side_effect=['', 'a'*40+'\n'+'b'*40, json.dumps(changed)]):
            with self.assertRaises(ValueError): bot.action(123, ('undo-menu',), 3)
