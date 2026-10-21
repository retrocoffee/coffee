import os
import unittest
from unittest.mock import Mock, patch
from bot.telegram_bot import Bot, REPLY_ACTIONS
from support import sample_menu


class ReplyKeyboardTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123'):
            self.bot = Bot()
        self.bot.messages = {}
        self.bot.api = Mock(return_value={'message_id': 42})

    def test_persistent_keyboard_is_reused_and_replaced_on_start(self):
        self.bot.ensure_reply_keyboard(123)
        self.bot.ensure_reply_keyboard(123)
        self.assertEqual(self.bot.api.call_count, 1)
        method, payload = self.bot.api.call_args.args
        self.assertEqual(method, 'sendMessage')
        keyboard = payload['reply_markup']
        self.assertTrue(keyboard['is_persistent'])
        self.assertTrue(keyboard['resize_keyboard'])
        self.assertFalse(keyboard['one_time_keyboard'])
        self.assertEqual({b['text'] for row in keyboard['keyboard'] for b in row}, set(REPLY_ACTIONS))
        self.bot.api.return_value = {'message_id': 43}
        self.bot.ensure_reply_keyboard(123, force=True)
        self.assertEqual(self.bot.messages['keyboard:123'], 43)
        self.bot.api.assert_called_with('deleteMessage', {'chat_id': 123, 'message_id': 42})

    def test_reply_navigation_cancels_pending_input_before_routing(self):
        for button, action in REPLY_ACTIONS.items():
            self.bot.sessions[123] = {'input': ('prices', ()), 'draft': ('unsaved', 'test')}
            with patch('bot.telegram_bot.git', return_value=''), patch('bot.telegram_bot.load', side_effect=sample_menu), \
                 patch.object(self.bot, 'input') as input_handler, patch.object(self.bot, 'action') as action_handler, \
                 patch.object(self.bot, 'send'):
                self.bot.handle({'update_id': 1, 'message': {'message_id': 90, 'from': {'id': 123},
                    'chat': {'id': 123, 'type': 'private'}, 'text': button}})
                input_handler.assert_not_called()
                self.assertNotIn('input', self.bot.sessions[123])
                self.assertNotIn('draft', self.bot.sessions[123])
                if action != 'home':
                    action_handler.assert_called_once_with(123, (action, 0) if action == 'history' else (action,), 1)
                self.bot.api.assert_called_with('deleteMessage', {'chat_id': 123, 'message_id': 90})

    def test_keyboard_is_not_sent_to_unauthorized_users(self):
        self.bot.handle({'update_id': 1, 'message': {'from': {'id': 999},
            'chat': {'id': 999, 'type': 'private'}, 'text': '🏠 Главное меню'}})
        self.bot.api.assert_not_called()
