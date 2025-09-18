import json
import tempfile
import unittest
from pathlib import Path

from multidownloader.batch import (
    ManifestItem,
    detect_handler,
    execute_batch,
    load_manifest,
)


class FakeDownloader:
    def __init__(self, fail_tokens=None):
        self.calls = []
        self.fail_tokens = set(fail_tokens or [])

    def download(self, source_name, url, options):
        self.calls.append((source_name, url, options))
        if any(token in url for token in self.fail_tokens):
            raise RuntimeError('simulated failure')


class BatchTests(unittest.TestCase):
    def test_detect_handler_resolves_known_hosts(self):
        self.assertEqual('Twitter', detect_handler('twitter', 'https://twitter.com/example/status/1'))
        self.assertEqual('Twitter', detect_handler('', 'https://fxtwitter.com/example/2'))
        self.assertEqual('Threads', detect_handler('threads', 'https://www.threads.net/@user/post/3'))
        self.assertEqual('YouTube', detect_handler('', 'https://youtu.be/abc'))
        self.assertIsNone(detect_handler('unknown', 'https://example.com/video/1'))

    def test_load_manifest_json_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'grouped.json'
            data = {'twitter': ['https://twitter.com/example/status/1'], 'images': ['https://imgur.com/a/123']}
            path.write_text(json.dumps(data), encoding='utf-8')
            manifest = load_manifest(path, fmt='json')
            self.assertEqual(2, len(manifest))
            self.assertEqual('twitter', manifest[0].source_hint)
            self.assertEqual('https://twitter.com/example/status/1', manifest[0].url)

    def test_execute_batch_respects_limits_and_options(self):
        items = [
            ManifestItem('instagram', 'https://www.instagram.com/p/abc/'),
            ManifestItem('facebook', 'https://www.facebook.com/watch?v=123'),
            ManifestItem('reddit', 'https://www.reddit.com/r/test/comments/xyz'),
            ManifestItem('other', 'https://example.com/unsupported'),
            ManifestItem('twitter', 'https://twitter.com/example/status/1'),
            ManifestItem('twitter', 'https://twitter.com/example/status/2'),
        ]
        fake = FakeDownloader()
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_batch(
                items,
                Path(tmp),
                per_source_limit=1,
                downloader=fake,
            )
        self.assertEqual(4, len(fake.calls))
        insta_call = next((call for call in fake.calls if call[0] == 'Instagram'), None)
        self.assertIsNotNone(insta_call)
        self.assertEqual('auto', insta_call[2].get('auth'))
        fb_call = next((call for call in fake.calls if call[0] == 'Facebook'), None)
        self.assertIsNotNone(fb_call)
        self.assertTrue(fb_call[2].get('use_session'))
        skip_reasons = {reason for *_rest, reason in result['skipped']}
        self.assertIn('unsupported', skip_reasons)
        self.assertIn('per-source-limit', skip_reasons)

    def test_execute_batch_records_errors(self):
        items = [ManifestItem('twitter', 'https://twitter.com/fail/status/99')]
        fake = FakeDownloader(fail_tokens={'fail'})
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_batch(items, Path(tmp), downloader=fake)
        self.assertEqual(1, len(result['errors']))
        self.assertEqual(0, len(result['skipped']))


if __name__ == '__main__':
    unittest.main()
