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
from bot import carousel
from bot.images import prepare_image
from bot.menu import encode as menu_encode
from bot.telegram_bot import Bot, fingerprint, git
from scripts.carousel import render_carousel
from support import sample_menu


def entries(menu):
    section = menu['sections'][0]
    group = section['groups'][0]
    return [carousel.new_entry(section, group, item) for item in group['items']]


def picture():
    stream = io.BytesIO()
    Image.new('RGB', (24, 32), 'orange').save(stream, 'PNG')
    return stream.getvalue()


class CarouselBotTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123,456'):
            self.bot = Bot()
        self.bot.generation_pool.shutdown()
        self.bot.send = Mock()
        self.bot.api = Mock(return_value={'message_id': 42})
        self.bot.ensure_reply_keyboard = Mock()
        for uid in (123, 456):
            menu = sample_menu()
            self.bot.sessions[uid] = {'menu': menu, 'base': fingerprint(menu)}
        self.menu = copy.deepcopy(self.bot.sessions[123]['menu'])
        self.original = entries(self.menu)
        self.enterContext(patch('bot.carousel.load', side_effect=lambda: copy.deepcopy(self.original)))
        self.bot.action(123, ('carousel',), 1)

    def test_order_hide_description_and_removal_are_only_drafts(self):
        self.bot.action(123, ('carousel-move', 1, -1), 2)
        self.bot.action(123, ('carousel-hide', 0), 3)
        self.bot.action(123, ('carousel-description', 0), 4)
        self.bot.input(123, 'Новое описание')
        self.bot.action(123, ('carousel-remove-ask', 1), 5)
        self.assertEqual(len(self.bot.sessions[123]['carousel_draft']), 2)
        self.bot.action(123, ('carousel-remove', 1), 6)
        draft = self.bot.sessions[123]['carousel_draft']
        self.assertEqual(draft[0]['item'], 'i2')
        self.assertTrue(draft[0]['hidden'])
        self.assertEqual(draft[0]['description_override'], 'Новое описание')
        self.assertEqual(self.original, entries(self.menu))
        self.assertEqual(self.bot.sessions[123]['menu'], self.menu)
        with patch('bot.telegram_bot.git', return_value=''), patch('bot.telegram_bot.load', side_effect=sample_menu):
            self.bot.action(123, ('home',), 7)
        self.assertNotIn('carousel_draft', self.bot.sessions[123])

    def test_add_uses_menu_reference_and_rejects_duplicates(self):
        self.bot.action(123, ('carousel-add', 'coffee', 'g1'), 2)
        self.bot.action(123, ('carousel-pick', 'coffee', 'g1', 'i1'), 3)
        self.assertEqual(carousel.key(self.bot.sessions[123]['carousel_draft'][-1]), ('coffee', 'g1', 'i1'))
        with self.assertRaises(ValueError):
            self.bot.action(123, ('carousel-pick', 'coffee', 'g1', 'i1'), 4)

    def test_upload_changes_only_carousel_and_can_reset_to_menu(self):
        self.bot.action(123, ('carousel-image', 0), 2)
        self.bot.download_file = Mock(return_value=picture())
        self.bot.handle({'update_id': 3, 'message': {'from': {'id': 123},
            'chat': {'id': 123, 'type': 'private'}, 'photo': [{'file_id': 'photo'}]}})
        state = self.bot.sessions[123]
        self.assertTrue(state['carousel_draft'][0]['custom_image'])
        self.assertEqual(set(state['carousel_assets']), {state['carousel_draft'][0]['image']})
        self.assertEqual(state['menu'], self.menu)
        self.bot.action(123, ('carousel-image-reset', 0), 4)
        self.assertEqual(state['carousel_draft'][0]['image'], self.original[0]['image'])
        self.assertNotIn('custom_image', state['carousel_draft'][0])

    def test_save_confirmation_and_private_access(self):
        with patch('bot.carousel.publish', return_value=True) as publish, patch('bot.telegram_bot.dispatch') as dispatch:
            self.bot.action(123, ('carousel-save-ask',), 2)
            publish.assert_not_called()
            self.bot.action(123, ('carousel-save',), 3)
            publish.assert_called_once()
            dispatch.assert_called_once()
        self.bot.callbacks['save'] = (123, ('carousel-save',))
        with patch('bot.carousel.publish') as publish:
            for uid, kind in [(999, 'private'), (123, 'group'), (456, 'private')]:
                self.bot.handle({'update_id': 4, 'callback_query': {'id': 'cb', 'data': 'save',
                    'from': {'id': uid}, 'message': {'chat': {'id': uid, 'type': kind}}}})
            publish.assert_not_called()

    def test_invalid_paths_and_options_rejected(self):
        for change in ({'image': 'https://bad.invalid/a.png'}, {'image': './assets/products/../a.svg'},
                       {'hidden': 'false'}, {'description_override': 'a'*1001}, {'token': 'secret'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                carousel.validate([dict(self.original[0], **change)])
        with self.assertRaises(ValueError): carousel.validate(self.original*2)


class CarouselPersistenceTests(unittest.TestCase):
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
        git('init', '--initial-branch=main')
        self.configure()
        Path('data').mkdir()
        Path('public/assets/menu-icons').mkdir(parents=True)
        Path('public/assets/menu-icons/coffee.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.menu = sample_menu()
        self.original = entries(self.menu)
        Path('data/menu.json').write_text(menu_encode(self.menu))
        carousel.PATH.write_text(carousel.encode(self.original))
        git('add', '.')
        git('commit', '-m', 'Initial')
        git('remote', 'add', 'origin', str(self.remote))
        git('push', '-u', 'origin', 'main')

    def configure(self):
        git('config', 'user.name', 'Test')
        git('config', 'user.email', 'test@example.com')

    def remote_edit(self, path, data):
        other = self.root / 'other'
        git('clone', str(self.remote), str(other))
        os.chdir(other)
        self.configure()
        Path(path).write_text(data)
        git('add', path)
        git('commit', '-m', 'Concurrent edit')
        git('push', 'origin', 'main')
        os.chdir(self.repo)

    def test_save_preserves_concurrent_menu_prices(self):
        changed = copy.deepcopy(self.menu)
        changed['sections'][0]['groups'][0]['items'][0]['prices'] = ['999 ₽']
        self.remote_edit('data/menu.json', menu_encode(changed))
        draft = list(reversed(self.original))
        self.assertTrue(carousel.publish(draft, carousel.fingerprint(self.original), {}, 1, git))
        self.assertEqual(json.loads(Path('data/menu.json').read_text()), changed)
        self.assertEqual(git('show', '--format=', '--name-only', 'HEAD'), 'data/carousel.json')
        self.assertIn('999 ₽', render_carousel(changed))

    def test_conflict_does_not_overwrite_other_admin(self):
        self.remote_edit('data/carousel.json', carousel.encode(list(reversed(self.original))))
        with self.assertRaisesRegex(ValueError, 'уже изменилась'):
            carousel.publish([], carousel.fingerprint(self.original), {}, 1, git)
        self.assertEqual(git('status', '--porcelain'), '')

    def test_upload_commits_atomically_and_description_is_escaped(self):
        image, data = prepare_image(picture())
        draft = copy.deepcopy(self.original)
        draft[0].update(image=image, custom_image=True, description_override='<script>test</script>')
        draft[1]['hidden'] = True
        carousel.publish(draft, carousel.fingerprint(self.original), {image: data}, 1, git)
        html = render_carousel(self.menu)
        self.assertEqual(html.count('class="product-card"'), 1)
        self.assertIn(image, html)
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)
        self.assertEqual(git('status', '--porcelain'), '')

    def test_rejected_push_rolls_back_data_and_new_assets(self):
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        image, data = prepare_image(picture())
        draft = [dict(self.original[0], image=image, custom_image=True)]
        before = git('rev-parse', 'HEAD')
        with self.assertRaises(ValueError): carousel.publish(draft, carousel.fingerprint(self.original), {image:data}, 1, git)
        self.assertEqual(git('rev-parse', 'HEAD'), before)
        self.assertEqual(carousel.load(), self.original)
        self.assertFalse(Path('public', image[2:]).exists())
        self.assertEqual(git('status', '--porcelain'), '')

    def test_empty_selection_hides_carousel(self):
        carousel.PATH.write_text('[]')
        self.assertEqual(render_carousel(self.menu), '')
