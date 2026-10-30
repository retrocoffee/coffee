import os
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from bot.responsive import build, attributes


class ResponsiveTests(unittest.TestCase):
    def test_derivatives_preserve_originals_and_have_valid_unique_widths(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                root = Path('public/assets/menu-images')
                root.mkdir(parents=True)
                for name, size in [('small', (24,40)), ('large',(640,480))]:
                    path = root / (name+'.webp')
                    Image.new('RGBA', size, (120,40,20,150)).save(path)
                    before = path.read_bytes()
                    build()
                    self.assertEqual(before, path.read_bytes())
                    attrs = attributes('./assets/menu-images/'+name+'.webp', '32px')
                    import re
                    widths = re.findall(r' (\d+)w', attrs)
                    self.assertEqual(len(widths),len(set(widths)))
                    self.assertTrue(widths)
                    for filename,width in re.findall(r'(\./[^ ,"]+) (\d+)w', attrs):
                        with Image.open(Path('public', filename[2:])) as image:
                            self.assertEqual(image.width,int(width))
                            self.assertLessEqual(image.width,320)
                            self.assertEqual(image.mode,'RGBA')
                self.assertEqual(attributes('./assets/menu-images/missing.webp','32px'), '')
            finally:
                os.chdir(previous)
