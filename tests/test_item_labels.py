import copy
import os
import unittest
from unittest.mock import Mock, patch
from bot.menu import render, validate
from bot.preview import document
from bot.telegram_bot import Bot, fingerprint
from scripts.carousel import render_carousel
from support import sample_menu


class ItemLabelsTests(unittest.TestCase):
    def test_invalid_labels_are_rejected(self):
        for labels in ('hit', ['other'], ['hit', 'hit'], [['new']], ['hit', 'new', 'other']):
            menu = sample_menu()
            menu['sections'][0]['groups'][0]['items'][0]['labels'] = labels
            with self.subTest(labels=labels), self.assertRaises(ValueError): validate(menu)

    def test_labels_follow_item_in_menu_carousel_and_private_preview(self):
        menu = sample_menu()
        item = menu['sections'][0]['groups'][0]['items'][0]
        item['labels'] = ['new', 'hit']
        expected = '<span class="item-label">Хит</span><span class="item-label">Новинка</span>'
        self.assertIn(expected, render(menu))
        self.assertIn(expected, document(menu).decode())
        with patch('scripts.carousel.selection.load', return_value=[{
            'section':'seasonal','group':'g1','item':'i1','image':'./assets/menu-icons/coffee.svg'}]):
            self.assertIn(expected, render_carousel(menu))
        item.pop('labels')
        self.assertNotIn('class="item-label"', render(menu))

    def test_bot_toggles_both_labels_in_draft_then_uses_confirmed_publication(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123'):
            bot = Bot()
        bot.generation_pool.shutdown()
        bot.send = Mock()
        menu = sample_menu()
        before = copy.deepcopy(menu)
        bot.sessions[123] = {'menu':menu, 'base':fingerprint(menu)}
        bot.action(123, ('labels','seasonal','g1','i1'), 1)
        with patch('bot.telegram_bot.publish', return_value=True) as publish, patch('bot.telegram_bot.dispatch'):
            bot.action(123, ('label-toggle','hit'), 2)
            bot.action(123, ('label-toggle','new'), 3)
            bot.action(123, ('labels-confirm',), 4)
            self.assertEqual(menu, before)
            publish.assert_not_called()
            bot.action(123, ('save',), 5)
            saved = publish.call_args.args[0]
            self.assertEqual(saved['sections'][0]['groups'][0]['items'][0]['labels'], ['hit','new'])
        bot.action(123, ('labels','seasonal','g1','i1'), 6)
        bot.action(123, ('label-toggle','hit'), 7)
        bot.action(123, ('label-toggle','new'), 8)
        bot.action(123, ('labels-confirm',), 9)
        self.assertNotIn('labels', bot.sessions[123]['draft'][0]['sections'][0]['groups'][0]['items'][0])
        self.assertEqual(bot.sessions[123]['menu'], saved)
        with self.assertRaises(ValueError): bot.action(123, ('label-toggle','invalid'), 10)
