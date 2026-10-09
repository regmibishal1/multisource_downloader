import json
import logging
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from multidownloader.batch import detect_handler, main

ROOT = Path(__file__).resolve().parents[1]


class HostDetectionTests(unittest.TestCase):
    CASES = [
        # (hint, url, expected)
        ('', 'https://x.com/user/status/1', 'Twitter'),
        ('', 'https://mobile.twitter.com/user/status/1', 'Twitter'),
        ('', 'https://fixupx.com/user/status/1', 'Twitter'),
        ('', 'https://www.instagram.com/p/AbC123/', 'Instagram'),
        ('', 'https://instagr.am/p/AbC123/', 'Instagram'),
        ('', 'https://www.threads.com/@user/post/1', 'Threads'),
        ('', 'https://vm.tiktok.com/ZMabc/', 'TikTok'),
        ('', 'https://old.reddit.com/r/a/comments/b', 'Reddit'),
        ('', 'https://fb.watch/abc/', 'Facebook'),
        ('', 'https://m.facebook.com/watch?v=1', 'Facebook'),
        ('', 'https://www.youtube-nocookie.com/embed/abc', 'YouTube'),
        ('', 'https://drive.google.com/file/d/abc/view', 'Google Drive'),
        ('', 'https://WWW.YOUTUBE.COM/watch?v=abc', 'YouTube'),
        # B2: hosts that merely contain a known domain as a substring
        ('', 'https://www.dropbox.com/s/abc/file.mp4', None),
        ('', 'https://www.netflix.com/title/1', None),
        ('', 'https://fedex.com/track', None),
        ('', 'https://notyoutube.com/watch?v=abc', None),
        ('', 'https://x.com.evil.example/a', None),
        # B3: the URL wins over a wrong hint
        ('YouTube', 'https://x.com/a/status/1', 'Twitter'),
        ('instagram', 'https://www.tiktok.com/@u/video/1', 'TikTok'),
        # the hint is used when the host is unknown (shortened links)
        ('twitter', 'https://t.co/abc', 'Twitter'),
        ('Google Drive', 'https://bit.ly/abc', 'Google Drive'),
        ('x.com', 'https://t.co/abc', 'Twitter'),
        ('dropbox.com', 'https://t.co/abc', None),
        ('unknown', 'https://example.com/video/1', None),
        ('', 'not a url', None),
    ]

    def test_cases(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        for hint, url, expected in self.CASES:
            with self.subTest(hint=hint, url=url):
                self.assertEqual(expected, detect_handler(hint, url))


class ExitCodeTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def _run(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'grouped.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            return main([str(path), '--out-dir', tmp, '--dry-run'])

    def test_all_unsupported_exits_2(self):
        self.assertEqual(2, self._run({'other': ['https://example.org/a', 'https://www.dropbox.com/s/x']}))

    def test_supported_dry_run_exits_0(self):
        self.assertEqual(0, self._run({'twitter': ['https://x.com/a/status/1'], 'other': ['https://example.org/a']}))


class NoTkinterTests(unittest.TestCase):
    def test_batch_cli_runs_without_tkinter(self):
        # B1: the CLI must not need Tk. Run in a fresh interpreter with Tk blocked.
        code = (
            "import sys; sys.modules['tkinter'] = None; "
            "from multidownloader.batch import main; "
            "raise SystemExit(main(['--help']))"
        )
        proc = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn('usage', proc.stdout.lower())


if __name__ == '__main__':
    unittest.main()
