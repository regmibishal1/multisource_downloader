import unittest
import os

from multidownloader.core import Downloader
from multidownloader.sources.instagram import InstagramHandler
from multidownloader.sources.gdrive import GoogleDriveHandler
from multidownloader.sources.tiktok import TikTokHandler
from multidownloader.sources.threads import ThreadsHandler
from multidownloader.sources.twitter import TwitterHandler
from multidownloader.sources.reddit import RedditHandler
from multidownloader.sources.facebook import FacebookHandler
from multidownloader.sources.youtube import YouTubeHandler
from multidownloader.sources.yt_dlp_base import YTDLP_AVAILABLE


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

    def test_threads_handler_import(self):
        h = ThreadsHandler()
        self.assertIsNotNone(h)

    def test_twitter_handler_import(self):
        h = TwitterHandler()
        self.assertIsNotNone(h)

    def test_reddit_handler_import(self):
        h = RedditHandler()
        self.assertIsNotNone(h)

    def test_facebook_handler_import(self):
        h = FacebookHandler()
        self.assertIsNotNone(h)

    def test_youtube_handler_import(self):
        h = YouTubeHandler()
        self.assertIsNotNone(h)

    def test_downloader_sources_list(self):
        d = Downloader(os.getcwd())
        expected = {'Google Drive', 'Instagram', 'TikTok', 'Threads', 'Twitter', 'Reddit', 'Facebook', 'YouTube'}
        self.assertTrue(expected.issubset(set(d.list_sources())))

    def test_instagram_download_without_instaloader(self):
        h = InstagramHandler()
        if not getattr(h, 'logger'):
            h.logger = None
        if not __import__('importlib').util.find_spec('instaloader'):
            with self.assertRaises(RuntimeError):
                h.download('https://instagram.com/p/INVALID', os.getcwd(), {})


@unittest.skipUnless(YTDLP_AVAILABLE, 'yt-dlp not installed')
class YtDlpAvailabilityTests(unittest.TestCase):
    def test_ytdlp_available_flag(self):
        self.assertTrue(YTDLP_AVAILABLE)


if __name__ == '__main__':
    unittest.main()
