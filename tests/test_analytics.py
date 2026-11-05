import unittest
from unittest.mock import patch
from pathlib import Path
from scripts import analytics


class AnalyticsTests(unittest.TestCase):
    def test_disabled_has_no_external_bootstrap_or_permissions(self):
        with patch('scripts.analytics.load', return_value=None):
            html = analytics.apply(Path('public/index.html').read_text())
        self.assertNotIn('src="./analytics.js', html)
        self.assertNotIn('meta name="yandex-metrika-id"', html)
        self.assertIn("connect-src 'none'", html)

    def test_enabled_is_repeatable_and_can_be_disabled(self):
        original = Path('public/index.html').read_text()
        with patch('scripts.analytics.load', return_value=12345678):
            html = analytics.apply(original)
            html = analytics.apply(html)
        self.assertEqual(html.count('meta name="yandex-metrika-id"'), 1)
        self.assertEqual(html.count('src="./analytics.js'), 1)
        self.assertEqual(html.count('<!-- analytics-info:start -->'), 1)
        self.assertIn('https://mc.yandex.ru', html)
        self.assertNotIn("script-src 'unsafe-inline'", html)
        with patch('scripts.analytics.load', return_value=None): html = analytics.apply(html)
        self.assertNotIn('<!-- analytics-info:start -->', html)
        self.assertNotIn('src="./analytics.js', html)
        self.assertIn("connect-src 'none'", html)

    def test_invalid_counter_cannot_inject_html(self):
        for value in ('\"><script>', True, 0, -1, 1.25):
            with patch('scripts.analytics.PATH') as path:
                path.exists.return_value = True
                import json
                path.read_text.return_value = json.dumps({'yandex_counter_id':value})
                with self.assertRaises(ValueError): analytics.load()
