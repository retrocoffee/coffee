import copy
import os
import unittest
from unittest.mock import patch
from bot.menu import load, render, validate, prices
from bot.telegram_bot import Bot, fingerprint, publish


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.menu = load()

    def test_hide_preserves_items(self):
        before = copy.deepcopy(self.menu['sections'][0]['groups'])
        self.menu['sections'][0]['hidden'] = True
        self.assertNotIn('id="seasonal"', render(self.menu))
        self.assertEqual(before, self.menu['sections'][0]['groups'])
        self.menu['sections'][0]['hidden'] = False
        self.assertIn('id="seasonal"', render(self.menu))

    def test_html_is_escaped(self):
        self.menu['sections'][0]['name'] = '<script>alert(1)</script>'
        self.assertNotIn('<script>', render(self.menu))
        self.assertIn('&lt;script&gt;', render(self.menu))

    def test_price_validation(self):
        self.assertEqual(prices('220; 260 ₽', 2), ['220 ₽', '260 ₽'])
        for raw in ['-100', 'NaN', '100; 200', '<script>']:
            with self.assertRaises(ValueError): prices(raw, 1)

    def test_duplicate_ids_rejected(self):
        self.menu['sections'].append(copy.deepcopy(self.menu['sections'][0]))
        with self.assertRaises(ValueError): validate(self.menu)

    def test_mismatched_columns_rejected(self):
        self.menu['sections'][0]['groups'][0]['items'][0]['prices'].append('100 ₽')
        with self.assertRaises(ValueError): validate(self.menu)

    def test_remote_conflict_never_writes_or_pushes(self):
        remote = copy.deepcopy(self.menu)
        remote['sections'][0]['name'] = 'Another editor'
        from bot.menu import encode
        with patch('bot.telegram_bot.git', side_effect=['', encode(remote)]) as mock_git:
            with self.assertRaises(ValueError):
                publish(self.menu, fingerprint(self.menu), 1, 'test')
            self.assertEqual(mock_git.call_count, 2)


class BotTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123'):
            self.bot = Bot()
        self.bot.api = lambda *args: None
        menu = load()
        self.bot.sessions[123] = {'menu': menu, 'base': fingerprint(menu)}

    def test_private_admin_only(self):
        with patch.object(self.bot, 'home') as home:
            for uid, kind in [(999, 'private'), (123, 'group')]:
                self.bot.handle({'update_id':1,'message':{'from':{'id':uid},'chat':{'id':uid,'type':kind},'text':'/start'}})
            home.assert_not_called()
            self.bot.handle({'update_id':2,'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'/start'}})
            home.assert_called_once_with(123)

    def test_draft_does_not_mutate_original(self):
        self.bot.ask(123, 'prices', 'seasonal', 'g1', 'i1')
        original = copy.deepcopy(self.bot.sessions[123]['menu'])
        self.bot.input(123, '999')
        state = self.bot.sessions[123]
        self.assertEqual(original, state['menu'])
        self.assertEqual(state['draft'][0]['sections'][0]['groups'][0]['items'][0]['prices'], ['999 ₽'])

    def test_delete_requires_confirmation(self):
        self.bot.action(123, ('delete-section', 'coffee'), 1)
        self.assertEqual(len(self.bot.sessions[123]['menu']['sections']), 6)
        self.assertEqual(len(self.bot.sessions[123]['draft'][0]['sections']), 5)
        self.assertTrue(any(action[1][0] == 'save' for action in self.bot.callbacks.values()))

    def test_old_buttons_expire(self):
        self.bot.send(123, 'test', [('Save', ('save',))])
        old = next(iter(self.bot.callbacks))
        self.bot.send(123, 'next', [('Home', ('home',))])
        self.assertNotIn(old, self.bot.callbacks)

    def test_add_item(self):
        self.bot.ask(123, 'item-add', 'seasonal', 'g1')
        self.bot.input(123, 'Тест\n150\nОписание')
        item=self.bot.sessions[123]['draft'][0]['sections'][0]['groups'][0]['items'][-1]
        self.assertEqual(item['name'], 'Тест')
        self.assertEqual(item['prices'], ['150 ₽'])
        self.assertEqual(item['notes'], ['Описание'])

    def test_restore_is_a_new_confirmed_draft(self):
        from bot.menu import encode
        old = load()
        old['sections'][0]['name'] = 'Старая версия'
        with patch('bot.telegram_bot.git', return_value=encode(old)):
            self.bot.action(123, ('restore', 'abc123'), 1)
        self.assertNotEqual(self.bot.sessions[123]['menu']['sections'][0]['name'], 'Старая версия')
        self.assertEqual(self.bot.sessions[123]['draft'][0]['sections'][0]['name'], 'Старая версия')


if __name__ == '__main__':
    unittest.main()
