import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from bot import site
from bot.menu import encode as menu_encode
from bot.telegram_bot import Bot, publish_site, site_fingerprint
from scripts.site import render_site
from support import sample_menu


def sample_profile():
    data = {key: '' for key in site.FIELDS}
    data.update(version=1, handle='@cafe', name='Кофейня', review_url='https://2gis.ru/test',
                address='Адрес кофейни', hours='09:00–22:00', about_text='Кофе и десерты',
                socials=[{'id': 'vk', 'name': 'ВКонтакте', 'url': 'https://vk.com/example'}])
    return data


class ProfileTests(unittest.TestCase):
    def test_legal_details_and_owner_message_render_only_when_filled(self):
        profile = sample_profile()
        html = render_site(profile)
        self.assertNotIn('<h3>Реквизиты</h3>', html)
        self.assertNotIn('От владельца', html)
        profile.update(legal_name='ИП Тест', inn='123456789012', owner_text='Рады видеть вас!')
        html = render_site(profile)
        self.assertIn('ИП Тест', html)
        self.assertIn('Рады видеть вас!', html)
        self.assertIn('От владельца', html)

    def test_all_three_footer_actions_and_content_are_safe(self):
        profile = sample_profile()
        profile['owner_text'] = '<script>alert("test")</script>'
        profile['socials'][0]['name'] = '<b>VK</b>'
        html = render_site(profile)
        self.assertIn('data-open-dialog="social-dialog"', html)
        self.assertIn('data-open-dialog="about-dialog"', html)
        self.assertIn('href="https://2gis.ru/test"', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;b&gt;VK&lt;/b&gt;', html)

    def test_invalid_links_identifiers_and_social_duplicates_rejected(self):
        for url in ['javascript:alert(1)', 'http://example.com', 'https://user:secret@example.com', 'https://example.com/a b']:
            profile = sample_profile()
            profile['socials'][0]['url'] = url
            with self.subTest(url=url), self.assertRaises(ValueError): site.validate(profile)
        for key, value in [('inn', 'bad'), ('ogrn', '123'), ('registered_on', '2026-03-26'), ('registered_on', '99.99.2026'), ('email', 'bad')]:
            profile = sample_profile()
            profile[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): site.validate(profile)
        profile = sample_profile()
        profile['socials'] *= 2
        with self.assertRaises(ValueError): site.validate(profile)

    def test_incomplete_fields_are_visible_only_in_admin_status(self):
        profile = sample_profile()
        self.assertIn('Регистрирующий орган', site.missing(profile))
        self.assertNotIn('Не заполнено', render_site(profile))


class ProfileBotTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, TELEGRAM_BOT_TOKEN='test', TELEGRAM_ADMIN_IDS='123'):
            self.bot = Bot()
        self.bot.generation_pool.shutdown()
        self.bot.send = Mock()
        self.bot.sessions[123] = {'menu': sample_menu(), 'site': sample_profile(), 'site_base': site_fingerprint(sample_profile())}

    def test_fields_are_drafts_until_confirmation_and_cancel_preserves_original(self):
        original = copy.deepcopy(self.bot.sessions[123])
        self.bot.action(123, ('site-ask', 'owner_text'), 1)
        self.bot.input(123, 'Пара слов от владельца')
        state = self.bot.sessions[123]
        self.assertEqual(state['menu'], original['menu'])
        self.assertEqual(state['site'], original['site'])
        self.assertEqual(state['site_draft'][0]['owner_text'], 'Пара слов от владельца')
        self.bot.action(123, ('site', 'texts'), 2)
        self.assertNotIn('site_draft', state)
        self.assertEqual(state['site']['owner_text'], '')

    def test_social_add_edit_and_delete_are_confirmed(self):
        self.bot.action(123, ('site-social-ask',), 1)
        self.bot.input(123, 'Telegram\nhttps://t.me/example')
        state = self.bot.sessions[123]
        self.assertEqual(len(state['site']['socials']), 1)
        self.assertEqual(len(state['site_draft'][0]['socials']), 2)
        self.bot.action(123, ('site-social-ask', 'vk'), 2)
        self.bot.input(123, 'Новый VK\nhttps://vk.com/new')
        self.assertEqual(state['site_draft'][0]['socials'][0]['name'], 'Новый VK')
        self.bot.action(123, ('site-social-delete', 'vk'), 3)
        self.assertEqual(len(state['site']['socials']), 1)
        self.assertEqual(state['site_draft'][0]['socials'], [])

    def test_invalid_field_can_be_retried_without_mutation(self):
        self.bot.action(123, ('site-ask', 'review_url'), 1)
        with self.assertRaises(ValueError): self.bot.input(123, 'javascript:alert(1)')
        self.assertIn('input', self.bot.sessions[123])
        self.bot.input(123, 'https://2gis.ru/new')
        self.assertEqual(self.bot.sessions[123]['site']['review_url'], 'https://2gis.ru/test')

    def test_save_and_history_restore_leave_menu_untouched(self):
        state = self.bot.sessions[123]
        original_menu = copy.deepcopy(state['menu'])
        self.bot.action(123, ('site-ask', 'phone'), 1)
        self.bot.input(123, '+7 999 111-22-33')
        with patch('bot.telegram_bot.publish_site', return_value=True) as publish, patch('bot.telegram_bot.dispatch') as dispatch:
            self.bot.action(123, ('site-save',), 2)
            publish.assert_called_once()
            dispatch.assert_called_once()
        self.assertEqual(state['menu'], original_menu)
        self.assertEqual(state['site']['phone'], '+7 999 111-22-33')
        old = sample_profile()
        with patch('bot.telegram_bot.git', return_value=site.encode(old)):
            self.bot.action(123, ('site-restore', 'a'*40), 3)
        self.assertEqual(state['site']['phone'], '+7 999 111-22-33')
        self.assertEqual(state['site_draft'][0]['phone'], '')


class ProfilePublishTests(unittest.TestCase):
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
        self.configure()
        Path('data').mkdir()
        self.profile = sample_profile()
        Path('data/site.json').write_text(site.encode(self.profile))
        Path('data/menu.json').write_text(menu_encode(sample_menu()))
        self.git('add', '.')
        self.git('commit', '-m', 'Initial')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')

    def git(self, *args):
        return subprocess.check_output(['git', *args], text=True, stderr=subprocess.PIPE).strip()

    def configure(self):
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')

    def remote_edit(self, filename, content):
        other = self.root / 'other'
        self.git('clone', str(self.remote), str(other))
        os.chdir(other)
        self.configure()
        Path(filename).write_text(content)
        self.git('add', filename)
        self.git('commit', '-m', 'Another editor')
        self.git('push', 'origin', 'main')
        os.chdir(self.repo)

    def test_concurrent_menu_changes_are_preserved_when_publishing_profile(self):
        menu = sample_menu()
        menu['sections'][0]['groups'][0]['items'][0]['prices'] = ['999 ₽']
        self.remote_edit('data/menu.json', menu_encode(menu))
        draft = copy.deepcopy(self.profile)
        draft['owner_text'] = 'Наш текст'
        self.assertTrue(publish_site(draft, site_fingerprint(self.profile), 1, 'Owner message'))
        self.assertEqual(json.loads(Path('data/menu.json').read_text()), menu)
        self.assertEqual(self.git('show', '--format=', '--name-only', 'HEAD'), 'data/site.json')
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.git('rev-parse', 'origin/main'))

    def test_concurrent_profile_edit_is_not_overwritten(self):
        changed = copy.deepcopy(self.profile)
        changed['hours'] = '10:00–22:00'
        self.remote_edit('data/site.json', site.encode(changed))
        draft = copy.deepcopy(self.profile)
        draft['hours'] = '11:00–20:00'
        before = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(ValueError, 'уже изменились'):
            publish_site(draft, site_fingerprint(self.profile), 1, 'Old draft')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_failed_push_rolls_back_profile_commit(self):
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n')
        hook.chmod(0o755)
        before = self.git('rev-parse', 'HEAD')
        draft = copy.deepcopy(self.profile)
        draft['owner_text'] = 'Unpublished'
        with self.assertRaises(ValueError): publish_site(draft, site_fingerprint(self.profile), 1, 'Rejected')
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(json.loads(Path('data/site.json').read_text()), self.profile)
        self.assertEqual(self.git('status', '--porcelain'), '')
