import base64
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from bot.generation import MAX_RESPONSE_BYTES, generate_image
from bot.images import prepare_image
from bot.telegram_bot import Bot, fingerprint
from support import sample_menu


def image_bytes():
    stream = io.BytesIO()
    Image.new('RGBA', (30, 40), (0, 0, 0, 128)).save(stream, 'PNG')
    return stream.getvalue()


class GenerationAPITests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, CLOUDFLARE_API_TOKEN='test-only-key', CLOUDFLARE_ACCOUNT_ID='a' * 32)
        self.env.start()
        self.addCleanup(self.env.stop)

    def response(self, content):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = content
        return response

    def test_single_cloudflare_call_uses_documented_api_and_normalizes_image(self):
        data = json.dumps({'success': True, 'result': {'image': base64.b64encode(image_bytes()).decode()}}).encode()
        response = self.response(data)
        with patch('bot.generation.urllib.request.urlopen', return_value=response) as call:
            path, content = generate_image('Латте', 'В стеклянной кружке, с пеной')
        self.assertEqual(call.call_count, 1)
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.cloudflare.com/client/v4/accounts/' + 'a' * 32 + '/ai/run/@cf/black-forest-labs/flux-1-schnell')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-only-key')
        payload = json.loads(request.data)
        self.assertEqual(payload['steps'], 4)
        self.assertIsInstance(payload['seed'], int)
        self.assertGreaterEqual(payload['seed'], 0)
        self.assertLess(payload['seed'], 2**32)
        self.assertIn('pure white', payload['prompt'])
        self.assertEqual(set(payload), {'prompt', 'steps', 'seed'})
        self.assertIn('Латте', payload['prompt'])
        self.assertIn('В стеклянной кружке', payload['prompt'])
        response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
        self.assertEqual((path, content), prepare_image(image_bytes()))

    def test_missing_key_or_invalid_description_never_calls_provider(self):
        with patch('bot.generation.urllib.request.urlopen') as call:
            for raw in ['', ' ' * 10, 'x' * 1501]:
                with self.assertRaises(ValueError): generate_image('Латте', raw)
            with patch.dict(os.environ, CLOUDFLARE_API_TOKEN=''), self.assertRaisesRegex(ValueError, 'CLOUDFLARE_API_TOKEN'):
                generate_image('Латте', 'Описание')
            call.assert_not_called()

    def test_missing_or_malformed_account_and_old_key_never_trigger_fallback(self):
        with patch('bot.generation.urllib.request.urlopen') as call:
            for account in ['', '../another-account', 'not-an-account-id', 'a' * 31, 'https://other.invalid']:
                with patch.dict(os.environ, CLOUDFLARE_ACCOUNT_ID=account, OPENAI_API_KEY='old-unused-key'):
                    with self.assertRaisesRegex(ValueError, 'CLOUDFLARE_ACCOUNT_ID'):
                        generate_image('Латте', 'Описание')
            call.assert_not_called()

    def test_documented_quota_error_is_recognized_without_retry(self):
        raw = json.dumps({'success': False, 'errors': [{'code': 3036, 'message': 'private-account-details'}]}).encode()
        for status in [200, 429]:
            with self.subTest(status=status):
                effect = urllib.error.HTTPError('https://api.cloudflare.com', status, 'error', {}, io.BytesIO(raw))
                with patch('bot.generation.urllib.request.urlopen', **({'return_value': self.response(raw)} if status == 200 else {'side_effect': effect})) as call:
                    with self.assertRaisesRegex(ValueError, '00:00 UTC') as caught:
                        generate_image('Латте', 'Описание')
                    self.assertNotIn('private-account-details', str(caught.exception))
                    self.assertEqual(call.call_count, 1)

    def test_direct_model_response_and_new_seed_for_each_variant(self):
        raw = json.dumps({'image': base64.b64encode(image_bytes()).decode()}).encode()
        with patch('bot.generation.secrets.randbelow', side_effect=[10, 20]), patch('bot.generation.urllib.request.urlopen', return_value=self.response(raw)) as call:
            for _ in range(2):
                generate_image('К' * 200, 'я' * 1500)
        payloads = [json.loads(args.args[0].data) for args in call.call_args_list]
        self.assertEqual([p['seed'] for p in payloads], [10, 20])
        self.assertTrue(all(len(p['prompt']) <= 2048 for p in payloads))
        self.assertTrue(all('я' * 1500 in p['prompt'] for p in payloads))

    def test_errors_do_not_leak_provider_body_or_retry(self):
        for status in [400, 401, 403, 429, 500]:
            body = io.BytesIO(json.dumps({"success": False, "errors": [{"code": 9999, "message": "sensitive-provider-body-test-only-key"}]}).encode())
            error = urllib.error.HTTPError('https://api.cloudflare.com/client/v4/accounts/' + 'a' * 32 + '/ai/run/@cf/black-forest-labs/flux-1-schnell', status, 'error', {}, body)
            with self.subTest(status=status), patch('bot.generation.urllib.request.urlopen', side_effect=error) as call:
                with self.assertRaises(ValueError) as caught: generate_image('Латте', 'Описание')
                self.assertNotIn('sensitive-provider-body', str(caught.exception))
                self.assertNotIn('test-only-key', str(caught.exception))
                self.assertEqual(call.call_count, 1)
        with patch('bot.generation.urllib.request.urlopen', side_effect=TimeoutError()) as call:
            with self.assertRaisesRegex(ValueError, 'Автоматического повтора не будет'):
                generate_image('Латте', 'Описание')
            self.assertEqual(call.call_count, 1)

    def test_bad_base64_urls_and_oversize_responses_are_rejected(self):
        replies = [b'not-json', b'{"result":[]}', b'{"result":{"url":"https://untrusted.invalid/photo"}}',
                   b'{"result":{"image":"!bad!"}}',
                   json.dumps({'result': {'image': base64.b64encode(b'not-an-image').decode()}}).encode()]
        for raw in replies:
            with self.subTest(raw=raw[:50]), patch('bot.generation.urllib.request.urlopen', return_value=self.response(raw)) as call:
                with self.assertRaises(ValueError): generate_image('Латте', 'Описание')
                self.assertEqual(call.call_count, 1)
        with patch('bot.generation.MAX_RESPONSE_BYTES', 10), patch('bot.generation.urllib.request.urlopen', return_value=self.response(b'x' * 11)):
            with self.assertRaises(ValueError): generate_image('Латте', 'Описание')


class GenerationBotTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123,456', CLOUDFLARE_API_TOKEN='test-only-key', CLOUDFLARE_ACCOUNT_ID='a' * 32)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.bot = Bot()
        self.bot.generation_pool.shutdown()
        self.bot.generation_pool = Mock()
        self.future = Future()
        self.future.set_running_or_notify_cancel()
        self.bot.generation_pool.submit.return_value = self.future
        self.bot.send = Mock()
        self.bot.send_generated_preview = Mock()
        self.bot.ensure_reply_keyboard = Mock()
        self.bot.api = Mock(return_value={'message_id': 42})
        for uid in (123, 456):
            menu = sample_menu()
            self.bot.sessions[uid] = {'menu': menu, 'base': fingerprint(menu)}
        self.ids = ('seasonal', 'g1', 'i1')

    def request(self, uid=123):
        self.bot.generation_ask(uid, *self.ids)
        self.bot.input(uid, 'Кружка горячего какао с зефирками')

    def test_description_is_reviewed_before_any_generation_request(self):
        self.request()
        self.bot.generation_pool.submit.assert_not_called()
        self.assertEqual(self.bot.sessions[123]['generation_request']['ids'], self.ids)
        self.bot.action(123, ('generation-current', *self.ids), 1)
        self.assertEqual(self.bot.sessions[123]['generation_request']['prompt'], 'Описание')
        self.bot.action(123, ('generation-start',), 2)
        self.bot.generation_pool.submit.assert_called_once()
        self.assertNotIn('draft', self.bot.sessions[123])

    def test_result_preserves_prices_and_waits_for_save(self):
        original = copy.deepcopy(self.bot.sessions[123]['menu'])
        self.request()
        self.bot.generation_start(123)
        image, data = prepare_image(image_bytes())
        self.future.set_result((image, data))
        self.bot.poll_generations()
        state = self.bot.sessions[123]
        self.assertEqual(state['menu'], original)
        item = state['draft'][0]['sections'][0]['groups'][0]['items'][0]
        self.assertEqual(item['prices'], ['100 ₽'])
        self.assertEqual(item['notes'], ['Описание'])
        self.assertEqual(state['draft_assets'], {image: data})
        self.bot.send_generated_preview.assert_called_once_with(123, refresh=False)
        self.bot.poll_generations()
        self.bot.generation_pool.submit.assert_called_once()
        self.bot.action(123, ('generation-again',), 3)
        self.bot.generation_pool.submit.assert_called_once()  # another variant also needs a click
        with patch('bot.telegram_bot.publish', return_value=True) as publish, patch('bot.telegram_bot.dispatch'):
            self.bot.action(123, ('save',), 4)
            self.assertEqual(publish.call_args.args[4], {image: data})
        self.assertEqual(self.bot.find(123, *self.ids)[2]['image'], image)

    def test_no_duplicate_or_parallel_provider_calls(self):
        self.request()
        self.bot.generation_start(123)
        self.bot.generation_start(123)
        self.request(456)
        self.bot.generation_start(456)
        self.assertEqual(self.bot.generation_pool.submit.call_count, 1)
        self.assertEqual(len(self.bot.generations), 1)

    def test_cancel_discards_late_result_even_after_new_input(self):
        self.request()
        self.bot.generation_start(123)
        self.bot.image(123, *self.ids)
        self.request()
        self.future.set_result(prepare_image(image_bytes()))
        self.bot.poll_generations()
        self.assertNotIn('draft', self.bot.sessions[123])
        self.bot.send_generated_preview.assert_not_called()
        self.assertFalse(self.bot.generations)

    def test_refresh_discards_old_result_and_errors_offer_explicit_retry(self):
        self.request()
        self.bot.generation_start(123)
        self.future.set_exception(ValueError('Нет баланса API'))
        self.bot.poll_generations()
        self.assertNotIn('draft', self.bot.sessions[123])
        self.assertIn('Нет баланса API', self.bot.send.call_args.args[1])
        self.assertEqual(self.bot.generation_pool.submit.call_count, 1)
        self.future = Future()
        self.future.set_running_or_notify_cancel()
        self.bot.generation_pool.submit.return_value = self.future
        self.bot.generation_start(123)
        with patch('bot.telegram_bot.git', return_value=''), patch('bot.telegram_bot.load', side_effect=sample_menu):
            self.bot.home(123)
        self.future.set_result(prepare_image(image_bytes()))
        self.bot.poll_generations()
        self.assertNotIn('draft', self.bot.sessions[123])

    def test_unauthorized_text_cannot_trigger_generation(self):
        self.request()
        self.bot.handle({'update_id': 1, 'message': {'from': {'id': 999}, 'chat': {'id': 999, 'type': 'private'}, 'text': 'Описание'}})
        self.bot.generation_pool.submit.assert_not_called()

    def test_missing_key_keeps_manual_upload_available(self):
        with patch.dict(os.environ, CLOUDFLARE_API_TOKEN=''):
            self.bot.image(123, *self.ids)
            self.assertTrue(any(action[0] == 'input' for _, action in self.bot.send.call_args.args[2]))
            self.bot.generation_ask(123, *self.ids)
            self.assertIn('CLOUDFLARE_API_TOKEN', self.bot.send.call_args.args[1])
            self.bot.generation_pool.submit.assert_not_called()

    def test_new_position_save_has_direct_image_button(self):
        self.bot.ask(123, 'item-add', 'seasonal', 'g1')
        self.bot.input(123, 'Новый напиток\n180\nС ванилью')
        created = self.bot.sessions[123]['created_item']
        with patch('bot.telegram_bot.publish', return_value=True), patch('bot.telegram_bot.dispatch'):
            self.bot.action(123, ('save',), 3)
        self.assertIn(('🖼 Картинка новой позиции', ('image', *created)), self.bot.send.call_args.args[2])
        self.assertEqual(self.bot.find(123, *created)[2]['name'], 'Новый напиток')

    def test_photo_preview_is_delivered_and_deleted_on_navigation(self):
        state = self.bot.sessions[123]
        image, data = prepare_image(image_bytes())
        self.bot.preview(123, state['menu'], 'Preview', assets={image: data}, generated={'ids': self.ids, 'prompt': 'Описание'})
        with tempfile.TemporaryDirectory() as temp:
            self.bot.message_file = Path(temp) / 'messages.json'
            self.bot.messages = {}
            self.bot.send = Bot.send.__get__(self.bot)
            self.bot.send_generated_preview = Bot.send_generated_preview.__get__(self.bot)
            self.bot.telegram_request = Mock(return_value={'message_id': 77})
            self.bot.send_generated_preview(123)
            method, body, content_type = self.bot.telegram_request.call_args.args
            self.assertEqual(method, 'sendPhoto')
            self.assertIn(b'Content-Type: image/jpeg', body)
            self.assertIn('multipart/form-data;', content_type)
            self.assertEqual(json.loads(self.bot.message_file.read_text())['preview:123'], 77)
            self.bot.send(123, 'Next screen')
            self.bot.api.assert_any_call('deleteMessage', {'chat_id': 123, 'message_id': 77})
            self.assertNotIn('preview:123', json.loads(self.bot.message_file.read_text()))
