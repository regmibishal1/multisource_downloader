import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import smoke  # noqa: E402


class LinksFileTests(unittest.TestCase):
    def test_the_shipped_list(self):
        links = smoke.load_links()
        platforms = {link['platform'] for link in links}
        for expected in ('youtube', 'x', 'instagram', 'tiktok', 'facebook', 'reddit', 'bluesky', 'gdrive', 'threads'):
            self.assertIn(expected, platforms)
        for link in links:
            with self.subTest(url=link['url']):
                self.assertIn(link['signin'], ('none', 'optional', 'required'))
                self.assertTrue(link['url'].startswith('https://'))
        # the list checks plain errors too, not only successes
        self.assertIn('failed:not_found', {link['expect'] for link in links})

    def test_bad_expectation_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'links.json'
            path.write_text(json.dumps({'links': [{'url': 'https://x.com/a', 'expect': 'works'}]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'unknown expectation'):
                smoke.load_links(path)


class ExpectationTests(unittest.TestCase):
    def test_meets(self):
        self.assertTrue(smoke.meets('saved', 'saved', None))
        self.assertTrue(smoke.meets('saved', 'already_saved', None))
        self.assertFalse(smoke.meets('saved', 'failed', 'outdated'))
        self.assertTrue(smoke.meets('failed:not_found', 'failed', 'not_found'))
        self.assertFalse(smoke.meets('failed:not_found', 'failed', 'network'))
        self.assertFalse(smoke.meets('failed:unsupported', 'saved', None))  # Threads working would be news
        self.assertTrue(smoke.meets('skipped', 'skipped', None))

    def test_selection(self):
        links = [{'platform': 'x', 'url': 'u1', 'expect': 'saved', 'signin': 'required'},
                 {'platform': 'tiktok', 'url': 'u2', 'expect': 'saved', 'ci': False, 'ci_note': 'blocked'},
                 {'platform': None, 'url': 'u3', 'expect': 'skipped'}]
        picked = {link['url']: link['skip_reason'] for link in smoke.selected(links, None, False, True)}
        self.assertIn('needs a sign-in', picked['u1'])
        self.assertEqual('not run on CI: blocked', picked['u2'])
        self.assertEqual('', picked['u3'])
        self.assertEqual('', {link['url']: link['skip_reason']
                              for link in smoke.selected(links, None, True, False)}['u1'])
        self.assertEqual(['u3'], [link['url'] for link in smoke.selected(links, ['other'], False, False)])


class ReportTests(unittest.TestCase):
    def report(self):
        report = smoke.Report(started_at='2026-10-10T16:40:00Z', mode='no sign-ins',
                              environment={'yt-dlp': '2026.8.19'}, verify='ok: All 3 files match.')
        report.outcomes = [
            smoke.Outcome('youtube', 'https://y', 'saved', 'saved', files=3, backend='yt-dlp', ok=True),
            smoke.Outcome('tiktok', 'https://t', 'saved', 'failed', 'outdated',
                          'TikTok changed something and the downloader needs an update.'),
            smoke.Outcome('x', 'https://x', 'saved', message='needs a sign-in (run with --with-signins)'),
        ]
        return report

    def test_failures_and_outputs(self):
        report = self.report()
        self.assertEqual(['tiktok'], [o.platform for o in report.failures])  # "not run" is not a failure
        self.assertFalse(report.ok)
        text = smoke.table(report)
        self.assertIn('BROKEN', text)
        self.assertIn('(expected saved)', text)
        self.assertIn('not run', text)
        md = smoke.markdown(report)
        self.assertIn('1 link(s) not as expected', md)
        self.assertIn('| BROKEN | tiktok | failed |', md)
        self.assertIn('yt-dlp 2026.8.19', md)

    def test_verify_failure_fails_the_report(self):
        report = self.report()
        report.outcomes = report.outcomes[:1]
        self.assertTrue(report.ok)
        report.verify = 'FAILED: Problems found'
        self.assertFalse(report.ok)


class IsolationTests(unittest.TestCase):
    def test_refuses_to_run_outside_the_temporary_folder(self):
        # if the sign-ins folder were the real one, nothing may run
        from multidownloader import session_store
        with mock.patch.object(smoke, 'isolate'), \
                mock.patch.object(session_store, 'SESSION_ROOT', Path(tempfile.gettempdir()) / 'elsewhere'), \
                mock.patch.object(smoke, 'run_all') as run_all, mock.patch('builtins.print'):
            self.assertEqual(2, smoke.main(['--only', 'youtube']))
        run_all.assert_not_called()

    def test_app_folders_but_not_firefoxs(self):
        self.assertIn('LOCALAPPDATA', smoke.ENV_KEYS)
        self.assertNotIn('APPDATA', smoke.ENV_KEYS)  # Firefox's profiles live there (TikTok's User-Agent)


if __name__ == '__main__':
    unittest.main()
