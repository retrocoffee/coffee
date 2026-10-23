import copy
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
from bot.bulk import apply_csv, export_csv
from bot.images import MAX_IMAGE_BYTES, prepare_image
from bot.menu import encode, render, validate
from bot.telegram_bot import Bot, fingerprint, publish
from scripts.carousel import render_carousel
from support import sample_menu


def picture(color='black', size=(100, 80), format='PNG', **kwargs):
    stream = io.BytesIO()
    Image.new('RGBA' if format == 'PNG' else 'RGB', size, color).save(stream, format, **kwargs)
    return stream.getvalue()


class ImageTests(unittest.TestCase):
    def test_normalization_removes_exif_and_corrects_orientation(self):
        exif = Image.Exif()
        exif[274] = 6
        exif[271] = 'Private camera metadata'
        path, data = prepare_image(picture(size=(1000, 500), format='JPEG', exif=exif))
        self.assertTrue(path.endswith('.webp'))
        with Image.open(io.BytesIO(data)) as result:
            self.assertEqual(result.size, (320, 640))
            self.assertFalse(result.getexif())
            self.assertNotIn('exif', result.info)
        self.assertEqual((path, data), prepare_image(picture(size=(1000, 500), format='JPEG', exif=exif)))

    def test_transparency_is_kept_without_upscaling(self):
        source = Image.new('RGBA', (100, 100))
        source.paste(Image.new('RGBA', (30, 40), (0, 0, 0, 128)), (20, 20))
        stream = io.BytesIO()
        source.save(stream, 'PNG')
        _, data = prepare_image(stream.getvalue())
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (30, 40))
            self.assertEqual(image.getpixel((0, 0))[3], 128)

    def test_unsafe_unsupported_and_excessive_uploads_rejected(self):
        for content in [b'<svg onload="alert(1)"/>', b'bad', b'x' * (MAX_IMAGE_BYTES + 1), picture(format='GIF'), picture(color=(0, 0, 0, 0))]:
            with self.subTest(content=content[:10]), self.assertRaises(ValueError):
                prepare_image(content)
        with patch('bot.images.MAX_IMAGE_PIXELS', 50), self.assertRaises(ValueError):
            prepare_image(picture())
        frames = [Image.new('RGB', (10, 10), color) for color in ('red', 'blue')]
        stream = io.BytesIO()
        frames[0].save(stream, 'WEBP', save_all=True, append_images=frames[1:], duration=100, loop=0)
        with self.assertRaises(ValueError):
            prepare_image(stream.getvalue())

    def test_menu_validates_local_paths_and_renders_every_image(self):
        menu = sample_menu()
        image, _ = prepare_image(picture())
        item = menu['sections'][0]['groups'][0]['items'][0]
        item['image'] = image
        self.assertIn('src="' + image + '"', render(menu))
        self.assertEqual(render(menu).count('class="menu-item-image"'), 12)
        for path in ['https://example.com/image.png', './assets/menu-images/../secret.webp', 'javascript:alert(1)', None]:
            item['image'] = path
            with self.assertRaises(ValueError): validate(menu)

    def test_csv_preserves_custom_images(self):
        menu = sample_menu()
        image, _ = prepare_image(picture())
        menu['sections'][0]['groups'][0]['items'][0]['image'] = image
        updated, _, _ = apply_csv(menu, export_csv(menu).replace('100 ₽', '150 ₽'))
        self.assertEqual(updated['sections'][0]['groups'][0]['items'][0]['image'], image)


class BotImageTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123'):
            self.bot = Bot()
        menu = sample_menu()
        self.bot.sessions[123] = {'menu': menu, 'base': fingerprint(menu)}
        self.bot.send = Mock()
        self.bot.ensure_reply_keyboard = Mock()
        self.bot.download_file = Mock(return_value=picture())
        self.ids = ('seasonal', 'g1', 'i1')

    def upload(self, **message):
        self.bot.handle({'update_id': 3, 'message': {'from': {'id': 123}, 'chat': {'type': 'private', 'id': 123}, **message}})

    def test_photo_and_document_create_atomic_draft_and_reset(self):
        for message in [{'photo': [{'file_id': 'small'}, {'file_id': 'large'}]}, {'document': {'file_id': 'document'}}]:
            self.bot.ask(123, 'image', *self.ids)
            self.upload(**message)
            state = self.bot.sessions[123]
            self.assertNotIn('image', state['menu']['sections'][0]['groups'][0]['items'][0])
            item = state['draft'][0]['sections'][0]['groups'][0]['items'][0]
            self.assertEqual(set(state['draft_assets']), {item['image']})
            with patch('bot.telegram_bot.publish', return_value=True) as save, patch('bot.telegram_bot.dispatch'):
                self.bot.action(123, ('save',), 5)
                self.assertEqual(set(save.call_args.args[4]), {item['image']})
            state['menu']['sections'][0]['groups'][0]['items'][0]['image'] = item['image']
            self.bot.action(123, ('image-reset', *self.ids), 6)
            self.assertNotIn('image', state['draft'][0]['sections'][0]['groups'][0]['items'][0])
            self.assertEqual(state['draft_assets'], {})
            state['menu']['sections'][0]['groups'][0]['items'][0].pop('image')

    def test_invalid_upload_can_be_retried_and_cancelled(self):
        self.bot.ask(123, 'image', *self.ids)
        self.bot.download_file.return_value = b'broken'
        self.upload(document={'file_id': 'broken'})
        self.assertIn('input', self.bot.sessions[123])
        self.assertNotIn('draft', self.bot.sessions[123])
        self.bot.download_file.return_value = picture()
        self.upload(photo=[{'file_id': 'valid'}])
        self.assertIn('draft_assets', self.bot.sessions[123])
        with patch('bot.telegram_bot.git', return_value=''), patch('bot.telegram_bot.load', side_effect=sample_menu):
            self.upload(text='✖ Отмена')
        self.assertNotIn('draft_assets', self.bot.sessions[123])

    def test_unauthorized_or_oversize_photos_are_not_downloaded(self):
        self.bot.ask(123, 'image', *self.ids)
        self.bot.handle({'update_id': 1, 'message': {'from': {'id': 999}, 'chat': {'id': 999, 'type': 'private'}, 'photo': [{'file_id': 'bad'}]}})
        self.upload(document={'file_id': 'large', 'file_size': MAX_IMAGE_BYTES + 1})
        self.bot.download_file.assert_not_called()


class PublishImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.previous = Path.cwd()
        self.addCleanup(os.chdir, self.previous)
        self.remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(self.remote)], check=True, capture_output=True)
        self.repo = self.root / 'work'
        self.repo.mkdir()
        os.chdir(self.repo)
        self.git('init', '--initial-branch=main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        Path('data').mkdir()
        self.menu = sample_menu()
        Path('data/menu.json').write_text(encode(self.menu))
        self.git('add', '.')
        self.git('commit', '-m', 'Initial')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.PIPE).strip()

    def draft(self, color):
        image, data = prepare_image(picture(color))
        menu = copy.deepcopy(self.menu)
        menu['sections'][0]['groups'][0]['items'][0]['image'] = image
        return menu, {image: data}

    def test_upload_replace_and_history_restore_keep_old_assets(self):
        first, assets = self.draft('black')
        self.assertTrue(publish(first, fingerprint(self.menu), 1, 'First image', assets))
        first_path = Path('public', next(iter(assets))[2:])
        self.assertEqual(first_path.read_bytes(), next(iter(assets.values())))
        self.assertIn(str(first_path), self.git('show', '--format=', '--name-only', 'HEAD'))
        second, next_assets = self.draft('red')
        publish(second, fingerprint(first), 2, 'Replace image', next_assets)
        publish(first, fingerprint(second), 3, 'Restore previous image')
        self.assertTrue(first_path.is_file())
        self.assertTrue(Path('public', next(iter(next_assets))[2:]).is_file())
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.git('rev-parse', 'origin/main'))
        Path('data/carousel.json').write_text('[{"section":"seasonal","group":"g1","item":"i1","image":"./assets/products/unused.svg"}]')
        self.assertIn(next(iter(assets)), render_carousel(first))

    def test_failed_push_leaves_no_changed_menu_or_image(self):
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        before = self.git('rev-parse', 'HEAD')
        menu, assets = self.draft('blue')
        with self.assertRaises(ValueError):
            publish(menu, fingerprint(self.menu), 4, 'Rejected', assets)
        self.assertEqual(before, self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertFalse(Path('public', next(iter(assets))[2:]).exists())

    def test_commit_failure_cleans_up_unpublished_image(self):
        from bot.telegram_bot import git
        def fail_commit(*args):
            if args[0] == 'commit': raise RuntimeError('Commit failed')
            return git(*args)
        menu, assets = self.draft('blue')
        with patch('bot.telegram_bot.git', side_effect=fail_commit), self.assertRaises(RuntimeError):
            publish(menu, fingerprint(self.menu), 4, 'Rejected', assets)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertFalse(Path('public', next(iter(assets))[2:]).exists())

    def test_missing_or_mismatched_assets_never_change_repo(self):
        menu, assets = self.draft('blue')
        for files in [None, {next(iter(assets)): b'wrong content'}]:
            with self.assertRaises(ValueError): publish(menu, fingerprint(self.menu), 5, 'Missing', files)
            self.assertEqual(self.git('status', '--porcelain'), '')
