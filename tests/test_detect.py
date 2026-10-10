import logging
import tempfile
import unittest
from pathlib import Path

from multidownloader import detect, platforms
from multidownloader.batch import ManifestItem, execute_batch


class ExtractTests(unittest.TestCase):
    CASES = [
        ('https://x.com/a/status/1', ['https://x.com/a/status/1']),
        ('see https://x.com/a/status/1.', ['https://x.com/a/status/1']),
        ('https://a.com/1, https://b.com/2; https://c.com/3!', ['https://a.com/1', 'https://b.com/2', 'https://c.com/3']),
        ('https://a.com/1 https://b.com/2', ['https://a.com/1', 'https://b.com/2']),
        ('line one\nhttps://a.com/1\n\nhttps://b.com/2\r\n', ['https://a.com/1', 'https://b.com/2']),
        ('(see https://a.com/x)', ['https://a.com/x']),
        ('[video](https://youtu.be/abc123)', ['https://youtu.be/abc123']),
        ('https://en.wikipedia.org/wiki/Foo_(bar)', ['https://en.wikipedia.org/wiki/Foo_(bar)']),
        ('<https://a.com/x>', ['https://a.com/x']),
        ('"https://a.com/x"', ['https://a.com/x']),
        ("'https://a.com/x'", ['https://a.com/x']),
        ('https://a.com/x?y=1&z=2#frag', ['https://a.com/x?y=1&z=2#frag']),
        ('HTTP://A.COM/x', ['HTTP://A.COM/x']),
        ('www.example.com/page', ['https://www.example.com/page']),
        ('youtu.be/abc123 and instagram.com/p/XyZ/', ['https://youtu.be/abc123', 'https://instagram.com/p/XyZ/']),
        ('m.facebook.com/watch?v=1', ['https://m.facebook.com/watch?v=1']),
        ('mail me at someone@x.com/foo', []),  # public-check: ok (fake address)
        ('dropbox.com/s/abc', []),
        ('notyoutube.com/watch', []),
        ('just x.com with no path', []),
        ('भिडियो हेर्नुहोस् https://x.com/a/status/1।', ['https://x.com/a/status/1']),
        ('काठमाडौं: https://youtu.be/abc123, प्रदर्शन', ['https://youtu.be/abc123']),
        ('12:01 - Ram: https://www.tiktok.com/@u/video/1?_r=1', ['https://www.tiktok.com/@u/video/1?_r=1']),
        ('**https://a.com/x**', ['https://a.com/x']),
        ('', []),
        ('no links here', []),
    ]

    def test_cases(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(expected, detect.extract_urls(text))


class NormalizeTests(unittest.TestCase):
    # Every URL in a group must normalize to the group's first entry.
    GROUPS = [
        [
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://youtu.be/dQw4w9WgXcQ',
            'https://youtu.be/dQw4w9WgXcQ?si=abcdef',
            'https://youtu.be/dQw4w9WgXcQ?t=42',
            'https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share',
            'https://www.youtube.com/shorts/dQw4w9WgXcQ',
            'https://www.youtube.com/embed/dQw4w9WgXcQ',
            'https://www.youtube.com/live/dQw4w9WgXcQ?si=x',
            'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ',
            'http://youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&index=2',
            'HTTPS://WWW.YOUTUBE.COM/watch?v=dQw4w9WgXcQ#comments',
        ],
        [
            'https://x.com/i/status/1890000000000000000',
            'https://x.com/someone/status/1890000000000000000',
            'https://twitter.com/SomeOne/status/1890000000000000000?s=20',
            'https://mobile.twitter.com/someone/status/1890000000000000000',
            'https://fxtwitter.com/someone/status/1890000000000000000',
            'https://vxtwitter.com/someone/status/1890000000000000000',
            'https://fixupx.com/someone/status/1890000000000000000/',
            'https://x.com/someone/status/1890000000000000000/photo/1',
            'https://x.com/i/web/status/1890000000000000000',
            'https://www.x.com/someone/statuses/1890000000000000000?t=abc&s=09',
        ],
        [
            'https://instagram.com/p/AbC123',
            'https://www.instagram.com/p/AbC123/',
            'https://www.instagram.com/p/AbC123/?igsh=MWQ1ZGUxMzBkMA==',
            'https://www.instagram.com/some.user/p/AbC123/',
            'https://instagr.am/p/AbC123/',
            'https://www.ddinstagram.com/p/AbC123/',
            'https://www.instagram.com/p/AbC123/?utm_source=ig_web_copy_link&igshid=xyz',
        ],
        [
            'https://instagram.com/reel/Reel42',
            'https://www.instagram.com/reels/Reel42/',
            'https://www.instagram.com/someone/reel/Reel42/?igsh=abc',
        ],
        [
            'https://facebook.com/watch?v=123',
            'https://m.facebook.com/watch?v=123&mibextid=abc',
            'https://www.facebook.com/watch/?v=123&fbclid=IwAR0',
        ],
        [
            'https://reddit.com/r/nepal/comments/abc/title',
            'https://old.reddit.com/r/nepal/comments/abc/title/',
            'https://www.reddit.com/r/nepal/comments/abc/title/?utm_source=share&utm_medium=web2x',
        ],
        [
            'https://tiktok.com/@user/video/7300000000000000000',
            'https://www.tiktok.com/@user/video/7300000000000000000?is_from_webapp=1&sender_device=pc',
            'https://www.tiktok.com/@user/video/7300000000000000000?_r=1&_t=8abc',
        ],
        [
            'https://drive.google.com/file/d/FILEID/view',
            'https://drive.google.com/file/d/FILEID/view?usp=sharing',
            'https://drive.google.com/file/d/FILEID/view?usp=drive_link',
        ],
        [
            'https://example.com/a?b=2&c=1',
            'https://example.com/a?c=1&b=2',
            'https://example.com/a/?c=1&b=2&gclid=zzz#top',
        ],
    ]

    DIFFERENT = [
        ('https://www.youtube.com/watch?v=aaaaaaaaaaa', 'https://www.youtube.com/watch?v=bbbbbbbbbbb'),
        ('https://x.com/a/status/1', 'https://x.com/a/status/2'),
        ('https://www.instagram.com/p/AbC123/', 'https://www.instagram.com/p/abc123/'),
        ('https://www.instagram.com/p/Same1/', 'https://www.instagram.com/reel/Same1/'),
        ('https://example.com/a?page=1', 'https://example.com/a?page=2'),
        ('https://example.com:8080/a', 'https://example.com/a'),
    ]

    def test_groups(self):
        for group in self.GROUPS:
            expected = detect.normalize_url(group[0])
            for url in group:
                with self.subTest(url=url):
                    self.assertEqual(expected, detect.normalize_url(url))

    def test_canonical_forms(self):
        self.assertEqual('https://youtube.com/watch?v=dQw4w9WgXcQ', detect.normalize_url('https://youtu.be/dQw4w9WgXcQ?si=1'))
        self.assertEqual('https://x.com/i/status/1', detect.normalize_url('https://twitter.com/a/status/1?s=20'))
        self.assertEqual('https://instagram.com/reel/R1', detect.normalize_url('https://www.instagram.com/u/reels/R1/'))

    def test_different_posts_stay_different(self):
        for a, b in self.DIFFERENT:
            with self.subTest(a=a, b=b):
                self.assertNotEqual(detect.normalize_url(a), detect.normalize_url(b))

    def test_garbage_is_returned_stripped(self):
        self.assertEqual('not a url', detect.normalize_url('  not a url '))
        self.assertEqual('', detect.normalize_url(''))


class DetectTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_every_domain_maps_to_its_platform(self):
        for platform in platforms.PLATFORMS:
            for domain in platform.domains:
                for host in (domain, 'www.' + domain, 'sub.' + domain):
                    with self.subTest(host=host):
                        self.assertEqual(platform, detect.platform_for_url(f'https://{host}/x'))

    def test_lookalike_hosts_are_not_matched(self):
        for platform in platforms.PLATFORMS:
            for domain in platform.domains:
                for host in ('not' + domain, domain + '.evil.example', domain.replace('.', '-') + '.com'):
                    with self.subTest(host=host):
                        self.assertIsNone(detect.platform_for_url(f'https://{host}/x'))

    def test_hints(self):
        self.assertEqual('x', detect.platform_for_hint('Twitter').key)
        self.assertEqual('gdrive', detect.platform_for_hint('Google Drive').key)
        self.assertEqual('x', detect.platform_for_hint('x.com').key)
        self.assertIsNone(detect.platform_for_hint('dropbox.com'))
        self.assertIsNone(detect.platform_for_hint('images'))
        self.assertIsNone(detect.platform_for_hint(''))

    def test_url_beats_hint(self):
        self.assertEqual('x', detect.detect_platform('https://x.com/a/status/1', 'YouTube').key)
        self.assertEqual('x', detect.detect_platform('https://t.co/abc', 'twitter').key)

    def test_handler_names_match_downloader(self):
        from multidownloader.core import Downloader
        handlers = set(Downloader('.').list_sources())
        self.assertEqual(handlers, {p.handler for p in platforms.PLATFORMS})


class UniqueAndSummaryTests(unittest.TestCase):
    def test_unique_keeps_first_original(self):
        urls = [
            'https://youtu.be/dQw4w9WgXcQ?si=1',
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://x.com/a/status/1',
            'https://twitter.com/a/status/1?s=20',
            'https://x.com/a/status/2',
        ]
        kept, duplicates = detect.unique_urls(urls)
        self.assertEqual(['https://youtu.be/dQw4w9WgXcQ?si=1', 'https://x.com/a/status/1', 'https://x.com/a/status/2'], kept)
        self.assertEqual(2, duplicates)

    def test_summary(self):
        urls = [
            'https://www.instagram.com/p/a/', 'https://www.instagram.com/p/b/',
            'https://x.com/a/status/1', 'https://example.com/x',
        ]
        self.assertEqual(
            '4 links found: Instagram 2, X (Twitter) 1. 2 duplicates removed. 1 not supported.',
            detect.summarize(urls, duplicates=2),
        )
        self.assertEqual('1 link found: YouTube 1.', detect.summarize(['https://youtu.be/abc123']))
        self.assertEqual('No links found yet.', detect.summarize([]))


class BatchDedupeTests(unittest.TestCase):
    def test_same_post_in_two_formats_downloads_once(self):
        class Fake:
            calls = []

            def download(self, source, url, options, out_dir=None):
                self.calls.append(url)

        items = [
            ManifestItem('twitter', 'https://twitter.com/a/status/1?s=20'),
            ManifestItem('twitter', 'https://x.com/a/status/1'),
            ManifestItem('youtube', 'https://youtu.be/dQw4w9WgXcQ'),
        ]
        fake = Fake()
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_batch(items, Path(tmp), downloader=fake)
        # downloads on different sites run in parallel, so their order varies
        self.assertEqual(sorted(['https://twitter.com/a/status/1?s=20', 'https://youtu.be/dQw4w9WgXcQ']),
                         sorted(fake.calls))
        self.assertEqual([('twitter', 'https://x.com/a/status/1', 'duplicate')], result['skipped'])


if __name__ == '__main__':
    unittest.main()
