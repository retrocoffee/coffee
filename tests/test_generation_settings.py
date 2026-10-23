import base64
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from bot import generation_settings as artwork
from bot.generation import generate_image
from bot.images import prepare_image
from bot.telegram_bot import Bot, fingerprint, generation_fingerprint, publish_generation_settings
from support import sample_menu


def colored_png():
    stream = io.BytesIO()
    Image.new('RGB', (24, 32), (220, 45, 80)).save(stream, format='PNG')
    return stream.getvalue()


def response(raw):
    reply = Mock()
    reply.status = 200
    reply.read.return_value = raw
    reply.__enter__ = Mock(return_value=reply)
    reply.__exit__ = Mock(return_value=False)
    return reply


class SettingsAPITests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, CLOUDFLARE_API_TOKEN='test-key', CLOUDFLARE_ACCOUNT_ID='a' * 32))

    def test_color_and_style_reach_model_without_monochrome_instruction(self):
        settings = dict(artwork.DEFAULTS, style='watercolor')
        raw = json.dumps({'result': {'image': base64.b64encode(colored_png()).decode()}}).encode()
        with patch('bot.generation.urllib.request.urlopen', return_value=response(raw)) as call:
            _, content = generate_image('Клубничный латте', 'Розовый напиток с клубникой', settings)
        prompt = json.loads(call.call_args.args[0].data)['prompt']
        self.assertIn('Full color', prompt)
        self.assertIn('watercolor', prompt)
        self.assertNotIn('monochrome', prompt)
        self.assertIn('pure white', prompt)
        self.assertIn('Розовый напиток с клубникой', prompt)
        with Image.open(io.BytesIO(content)) as image:
            red, green, blue = image.convert('RGB').getpixel((0, 0))
        self.assertGreater(red, green + 100)
        self.assertGreater(red, blue + 80)

    def test_monochrome_setting_replaces_color_instruction(self):
        settings = dict(artwork.DEFAULTS, color='monochrome', style='photo')
        raw = json.dumps({'image': base64.b64encode(colored_png()).decode()}).encode()
        with patch('bot.generation.urllib.request.urlopen', return_value=response(raw)) as call:
            generate_image('Кофе', 'В чашке', settings)
        prompt = json.loads(call.call_args.args[0].data)['prompt']
        self.assertIn('monochrome', prompt)
        self.assertIn('photography', prompt)
        self.assertNotIn('Full color', prompt)

    def test_lightning_uses_own_parameters_and_accepts_binary_image(self):
        settings = dict(artwork.DEFAULTS, model='sdxl-lightning', style='3d')
        raw = colored_png()
        with patch('bot.generation.urllib.request.urlopen', return_value=response(raw)) as call:
            self.assertEqual(generate_image('Кофе', 'В чашке', settings), prepare_image(raw))
        request = call.call_args.args[0]
        self.assertTrue(request.full_url.endswith('/ai/run/@cf/bytedance/stable-diffusion-xl-lightning'))
        payload = json.loads(request.data)
        self.assertEqual(set(payload), {'prompt', 'num_steps', 'width', 'height'})
        self.assertEqual(payload['num_steps'], 4)
        self.assertEqual((payload['width'], payload['height']), (1024, 1024))
        self.assertEqual(call.call_count, 1)

    def test_binary_model_keeps_json_error_diagnostics(self):
        raw = b'{"success":false,"errors":[{"code":3040}]}'
        with patch('bot.generation.urllib.request.urlopen', return_value=response(raw)) as call, \
             self.assertLogs('menu-bot.generation'):
            with self.assertRaisesRegex(ValueError, 'HTTP 200; Cloudflare 3040'):
                generate_image('Кофе', 'В чашке', dict(artwork.DEFAULTS, model='sdxl-lightning'))
        self.assertEqual(call.call_count, 1)

    def test_invalid_model_or_style_never_sends_request(self):
        with patch('bot.generation.urllib.request.urlopen') as call:
            for field, value in [('model', 'https://other.invalid'), ('model', '@cf/unsupported'),
                                 ('color', 'other'), ('style', []), ('version', True)]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    generate_image('Кофе', 'В чашке', dict(artwork.DEFAULTS, **{field: value}))
        call.assert_not_called()

    def test_every_preset_fits_maximum_product_and_description_lengths(self):
        raw = json.dumps({'image': base64.b64encode(colored_png()).decode()}).encode()
        with patch('bot.generation.urllib.request.urlopen', return_value=response(raw)) as call:
            for color in artwork.COLORS:
                for style in artwork.STYLES:
                    with self.subTest(color=color, style=style):
                        generate_image('Я' * 200, 'О' * 1500, dict(artwork.DEFAULTS, color=color, style=style))
        self.assertTrue(all(len(json.loads(args.args[0].data)['prompt']) <= 2048 for args in call.call_args_list))

    def test_preferences_survive_reload_and_corrupt_file_is_reported(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(artwork, 'SETTINGS_PATH', Path(temp) / 'generation.json'):
            selected = dict(artwork.DEFAULTS, style='3d', model='sdxl-lightning')
            artwork.SETTINGS_PATH.write_text(artwork.encode(selected))
            self.assertEqual(artwork.load(), selected)
            selected['color'] = 'monochrome'
            self.assertEqual(artwork.load()['color'], 'color')
            artwork.SETTINGS_PATH.write_text('{broken')
            with self.assertRaisesRegex(ValueError, 'настройки генерации'):
                artwork.load()


class SettingsBotTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123,456'):
            self.bot = Bot()
        self.bot.generation_pool.shutdown()
        self.bot.generation_pool = Mock()
        self.bot.send = Mock()
        self.bot.ensure_reply_keyboard = Mock()
        self.bot.api = Mock(return_value={'message_id': 42})
        for uid in (123, 456):
            menu = sample_menu()
            self.bot.sessions[uid] = {'menu': menu, 'base': fingerprint(menu)}
        self.settings = dict(artwork.DEFAULTS)
        self.enterContext(patch('bot.generation_settings.load', side_effect=lambda: dict(self.settings)))
        self.ids = ('seasonal', 'g1', 'i1')

    def test_inline_options_are_drafts_and_cancel_preserves_menu_and_settings(self):
        original = copy.deepcopy(self.bot.sessions[123]['menu'])
        self.bot.action(123, ('generation-settings', *self.ids), 1)
        self.bot.action(123, ('generation-settings-show', 'color'), 2)
        self.assertIn(('✓ Цветная', ('generation-option', 'color', 'color')), self.bot.send.call_args.args[2])
        self.bot.action(123, ('generation-option', 'color', 'monochrome'), 3)
        self.assertEqual(self.bot.sessions[123]['artwork_draft']['color'], 'monochrome')
        self.assertEqual(self.settings['color'], 'color')
        with patch('bot.telegram_bot.available', return_value=True):
            self.bot.action(123, ('generation-settings-back',), 4)
        self.assertNotIn('artwork_draft', self.bot.sessions[123])
        self.assertEqual(self.bot.sessions[123]['menu'], original)
        self.assertEqual(self.bot.sessions[123]['input'], ('generation', self.ids))
        self.bot.generation_pool.submit.assert_not_called()

    def test_save_is_explicit_and_returns_to_description_with_new_preferences(self):
        self.bot.generation_review(123, self.ids, 'Наше описание')
        original = copy.deepcopy(self.bot.sessions[123]['menu'])
        self.bot.action(123, ('generation-settings', *self.ids), 1)
        self.bot.action(123, ('generation-option', 'color', 'monochrome'), 2)
        self.bot.action(123, ('generation-option', 'style', 'watercolor'), 3)
        with patch('bot.telegram_bot.publish_generation_settings', return_value=True) as publish, \
             patch('bot.telegram_bot.dispatch') as dispatch:
            self.bot.action(123, ('generation-settings-save',), 4)
        draft = dict(self.bot.sessions[123]['artwork_draft'])
        publish.assert_called_once_with(draft, generation_fingerprint(artwork.DEFAULTS), 4)
        dispatch.assert_not_called()
        self.settings.update(draft)
        self.bot.action(123, ('generation-settings-back',), 5)
        request = self.bot.sessions[123]['generation_request']
        self.assertEqual(request['prompt'], 'Наше описание')
        self.assertEqual(request['settings'], draft)
        self.assertIn('Стиль: Акварель', self.bot.send.call_args.args[1])
        self.assertEqual(self.bot.sessions[123]['menu'], original)
        self.bot.generation_pool.submit.assert_not_called()

    def test_other_admin_changes_do_not_alter_reviewed_or_running_request(self):
        self.bot.generation_review(123, self.ids, 'Описание')
        reviewed = copy.deepcopy(self.bot.sessions[123]['generation_request'])
        self.settings.update(color='monochrome', model='sdxl-lightning')
        self.bot.generation_review(456, self.ids, 'Другой запрос')
        with patch('bot.telegram_bot.available', return_value=True):
            self.bot.generation_start(123)
        self.bot.generation_pool.submit.assert_called_once_with(generate_image, 'Первый напиток', 'Описание', reviewed['settings'])
        self.assertEqual(self.bot.generations[123]['request'], reviewed)
        self.assertEqual(self.bot.sessions[456]['generation_request']['settings'], self.settings)

    def test_another_variant_keeps_original_settings(self):
        original = dict(artwork.DEFAULTS, color='monochrome', style='line')
        self.bot.sessions[123]['generated'] = {'ids': self.ids, 'prompt': 'Описание', 'settings': original}
        self.settings['style'] = 'photo'
        self.bot.action(123, ('generation-again',), 1)
        self.assertEqual(self.bot.sessions[123]['generation_request']['settings'], original)
        self.bot.generation_pool.submit.assert_not_called()

    def test_inline_navigation_replaces_screen_and_routes_real_callback_keys(self):
        self.bot.send = Bot.send.__get__(self.bot)
        self.bot.messages = {}
        with tempfile.TemporaryDirectory() as temp:
            self.bot.message_file = Path(temp) / 'messages.json'
            self.bot.action(123, ('generation-settings', *self.ids), 1)

            def click(label, update_id):
                screens = [call.args[1] for call in self.bot.api.call_args_list
                           if call.args[0] in ('sendMessage', 'editMessageText')]
                button = next(button for row in screens[-1]['reply_markup']['inline_keyboard']
                              for button in row if button['text'].startswith(label))
                self.bot.handle({'update_id': update_id, 'callback_query': {
                    'id': str(update_id), 'data': button['callback_data'], 'from': {'id': 123},
                    'message': {'message_id': 42, 'chat': {'id': 123, 'type': 'private'}}}})

            click('Модель:', 2)
            click('SDXL Lightning', 3)
            with patch('bot.telegram_bot.publish_generation_settings', return_value=True) as publish:
                click('✓ Сохранить настройки', 4)
            self.assertEqual(publish.call_args.args[0]['model'], 'sdxl-lightning')
            self.assertEqual(sum(call.args[0] == 'sendMessage' for call in self.bot.api.call_args_list), 1)
            self.assertGreaterEqual(sum(call.args[0] == 'editMessageText' for call in self.bot.api.call_args_list), 3)
            self.bot.generation_pool.submit.assert_not_called()

    def test_settings_are_separate_drafts_for_admins_and_access_is_private(self):
        for uid in (123, 456):
            self.bot.action(uid, ('generation-settings',), 1)
        self.bot.action(123, ('generation-option', 'style', '3d'), 2)
        self.assertEqual(self.bot.sessions[456]['artwork_draft']['style'], 'illustration')
        self.bot.callbacks['settings'] = (123, ('generation-settings-save',))
        with patch('bot.telegram_bot.publish_generation_settings') as publish:
            for uid, kind in [(999, 'private'), (123, 'group'), (456, 'private')]:
                self.bot.handle({'update_id': 3, 'callback_query': {'id': 'cb', 'data': 'settings',
                    'from': {'id': uid}, 'message': {'chat': {'id': uid, 'type': kind}}}})
            publish.assert_not_called()


class SettingsPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(os.chdir, Path.cwd())
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(self.remote)], check=True, capture_output=True)
        self.repo = self.root / 'work'
        self.repo.mkdir()
        os.chdir(self.repo)
        self.git('init', '--initial-branch=main')
        self.configure()
        Path('config').mkdir()
        Path('data').mkdir()
        self.settings = dict(artwork.DEFAULTS)
        artwork.SETTINGS_PATH.write_text(artwork.encode(self.settings))
        Path('data/menu.json').write_text(json.dumps(sample_menu()))
        self.git('add', '.')
        self.git('commit', '-m', 'Initial')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.PIPE).strip()

    def configure(self):
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')

    def remote_edit(self, path, content):
        other = self.root / 'other'
        self.git('clone', str(self.remote), str(other))
        os.chdir(other)
        self.configure()
        Path(path).write_text(content)
        self.git('add', path)
        self.git('commit', '-m', 'Another editor')
        self.git('push', 'origin', 'main')
        os.chdir(self.repo)

    def test_save_preserves_concurrent_menu_changes_and_survives_new_checkout(self):
        menu = sample_menu()
        menu['sections'][0]['groups'][0]['items'][0]['prices'] = ['999 ₽']
        self.remote_edit('data/menu.json', json.dumps(menu))
        draft = dict(self.settings, color='monochrome', model='sdxl-lightning')
        self.assertTrue(publish_generation_settings(draft, generation_fingerprint(self.settings), 1))
        self.assertEqual(json.loads(Path('data/menu.json').read_text()), menu)
        self.assertEqual(self.git('show', '--format=', '--name-only', 'HEAD'), 'config/generation.json')
        self.git('clone', str(self.remote), str(self.root / 'restart'))
        os.chdir(self.root / 'restart')
        self.assertEqual(artwork.load(), draft)

    def test_concurrent_settings_edit_is_not_overwritten(self):
        self.remote_edit('config/generation.json', artwork.encode(dict(self.settings, style='photo')))
        before = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'уже изменились'):
            publish_generation_settings(dict(self.settings, style='3d'), generation_fingerprint(self.settings), 1)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_failed_push_restores_last_saved_settings(self):
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        before = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'Не удалось сохранить'):
            publish_generation_settings(dict(self.settings, style='3d'), generation_fingerprint(self.settings), 1)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(artwork.load(), self.settings)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_saving_same_settings_creates_no_commit(self):
        before = self.git('rev-parse', 'HEAD')
        self.assertFalse(publish_generation_settings(self.settings, generation_fingerprint(self.settings), 1))
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
