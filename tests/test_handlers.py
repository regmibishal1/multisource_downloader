import unittest
import os

from multidownloader.sources.instagram import InstagramHandler
from multidownloader.sources.gdrive import GoogleDriveHandler
from multidownloader.sources.tiktok import TikTokHandler

class HandlerImportTests(unittest.TestCase):
    def test_instagram_handler_import(self):
        h = InstagramHandler()
        self.assertIsNotNone(h)

    def test_gdrive_handler_import(self):
        h = GoogleDriveHandler()
        self.assertIsNotNone(h)

    def test_tiktok_handler_import(self):
        h = TikTokHandler()
        self.assertIsNotNone(h)

    def test_instagram_download_without_instaloader(self):
        h = InstagramHandler()
        # If instaloader isn't available, download should raise a RuntimeError
        if not getattr(h, 'logger'):
            h.logger = None
        if not __import__('importlib').util.find_spec('instaloader'):
            with self.assertRaises(RuntimeError):
                h.download('https://instagram.com/p/INVALID', os.getcwd(), {})

if __name__ == '__main__':
    unittest.main()
