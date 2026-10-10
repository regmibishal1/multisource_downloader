import logging
import logging.handlers
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import logs, session_store


class MigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.old = Path(tmp.name) / 'pkg' / '.sessions'
        self.new = Path(tmp.name) / 'data' / 'sessions'
        for rel, data in {'twitter/cookies.txt': b'x cookies', 'instagram/user.session': b'\x00\x01session',
                          'googledrive/client_secrets.json': b'{}'}.items():
            path = self.old / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_copies_verifies_then_deletes(self):
        stats = session_store.migrate_legacy_sessions(self.old, self.new)
        self.assertEqual({'copied': 3, 'kept_existing': 0, 'deleted': 3, 'failed': 0}, stats)
        self.assertEqual(b'\x00\x01session', (self.new / 'instagram' / 'user.session').read_bytes())
        self.assertFalse(self.old.exists())  # emptied folders are removed too

    def test_nothing_to_move(self):
        self.assertEqual(0, session_store.migrate_legacy_sessions(self.new / 'nope', self.new)['copied'])

    def test_existing_file_in_the_new_folder_wins_and_old_copy_stays(self):
        (self.new / 'twitter').mkdir(parents=True)
        (self.new / 'twitter' / 'cookies.txt').write_bytes(b'newer sign-in')
        stats = session_store.migrate_legacy_sessions(self.old, self.new)
        self.assertEqual((2, 1), (stats['copied'], stats['kept_existing']))
        self.assertEqual(b'newer sign-in', (self.new / 'twitter' / 'cookies.txt').read_bytes())
        self.assertEqual(b'x cookies', (self.old / 'twitter' / 'cookies.txt').read_bytes())

    def test_any_failure_keeps_every_old_file(self):
        real_copy = session_store.shutil.copy2
        calls = []

        def flaky(src, dest):
            calls.append(src)
            if len(calls) == 2:
                raise OSError('disk full')
            return real_copy(src, dest)

        with mock.patch.object(session_store.shutil, 'copy2', side_effect=flaky), \
                self.assertLogs('multidownloader.session_store', level='ERROR'):
            stats = session_store.migrate_legacy_sessions(self.old, self.new)
        self.assertEqual(1, stats['failed'])
        self.assertEqual(0, stats['deleted'])
        self.assertEqual(3, len([p for p in self.old.rglob('*') if p.is_file()]))

    def test_a_copy_that_does_not_match_is_not_deleted(self):
        with mock.patch.object(session_store, '_same_bytes', return_value=False), \
                self.assertLogs('multidownloader.session_store', level='ERROR'):
            stats = session_store.migrate_legacy_sessions(self.old, self.new)
        self.assertEqual(3, stats['failed'])
        self.assertEqual(3, len([p for p in self.old.rglob('*') if p.is_file()]))

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions')
    def test_private_permissions(self):
        session_store.migrate_legacy_sessions(self.old, self.new)
        self.assertEqual(0o700, (self.new / 'twitter').stat().st_mode & 0o777)
        self.assertEqual(0o600, (self.new / 'twitter' / 'cookies.txt').stat().st_mode & 0o777)

    def test_default_location_is_the_user_data_folder(self):
        from multidownloader import appdirs
        self.assertEqual(appdirs.data_dir() / 'sessions', session_store.SESSION_ROOT)
        self.assertEqual('.sessions', session_store.LEGACY_SESSION_ROOT.name)


class RedactTests(unittest.TestCase):
    CASES = [
        ('.x.com\tTRUE\t/\tTRUE\t1893456000\tauth_token\tSECRET', '[cookie line redacted]'),
        ('#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tSECRET', '[cookie line redacted]'),
        ('Cookie: auth_token=SECRET; ct0=SECRET', 'Cookie: [redacted]'),
        ('headers={"Cookie": "sessionid=SECRET"}', None),
        ('Authorization: Bearer SECRET', 'Authorization: [redacted]'),
        ('Authorization: LOW accesskey:SECRET', 'Authorization: [redacted]'),
        ('login with password=SECRET for u', 'login with password=[redacted] for u'),
        ('{"password": "SECRET", "user": "u"}', '{"password": [redacted], "user": "u"}'),
        ('{"refresh_token": "SECRET"}', '{"refresh_token": [redacted]}'),
        ('sent sessionid=SECRET to instagram', 'sent sessionid=[redacted] to instagram'),
        ('__Secure-3PAPISID=SECRET; LOGIN_INFO=SECRET', '__Secure-3PAPISID=[redacted]; LOGIN_INFO=[redacted]'),
        ('Saved https://x.com/a/status/1: 2 file(s)', 'Saved https://x.com/a/status/1: 2 file(s)'),
        ('boxes=3 and cookies saved', 'boxes=3 and cookies saved'),
    ]

    def test_cases(self):
        for text, expected in self.CASES:
            with self.subTest(text=text):
                result = logs.redact(text)
                self.assertNotIn('SECRET', result)
                if expected is not None:
                    self.assertEqual(expected, result)

    def test_home_folder_becomes_tilde(self):
        home = str(Path.home())
        self.assertEqual('~' + os.sep + 'x.mp4', logs.redact(home + os.sep + 'x.mp4'))
        self.assertEqual('~/x.mp4', logs.redact(home.replace('\\', '/') + '/x.mp4'))


class SetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / 'Logs'
        patcher = mock.patch.object(logs, 'log_dir', return_value=self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        root = logging.getLogger()
        before = list(root.handlers), root.level

        def restore():
            for handler in list(root.handlers):
                if getattr(handler, '_msdl', False):
                    handler.close()
                    root.removeHandler(handler)
            root.handlers[:] = before[0]
            root.setLevel(before[1])

        self.addCleanup(restore)

    def test_file_is_rotating_redacted_and_utc(self):
        path = logs.setup(console=False)
        self.assertEqual(self.dir / 'app.log', path)
        handler = next(h for h in logging.getLogger().handlers if getattr(h, '_msdl', False))
        self.assertIsInstance(handler, logging.handlers.RotatingFileHandler)
        self.assertEqual((1024 * 1024, 5), (handler.maxBytes, handler.backupCount))
        logging.getLogger('multidownloader.test').info('cookie sent: %s', 'sessionid=SECRET')
        try:
            raise RuntimeError('auth_token=SECRET')
        except RuntimeError:
            logging.getLogger('multidownloader.test').exception('failed')
        handler.flush()
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('SECRET', text)
        self.assertRegex(text, r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ INFO multidownloader\.test ')

    def test_setup_twice_does_not_double_handlers(self):
        logs.setup(console=False)
        logs.setup(console=False)
        ours = [h for h in logging.getLogger().handlers if getattr(h, '_msdl', False)]
        self.assertEqual(1, len(ours))

    def test_diagnostics(self):
        logs.setup(console=False)
        logging.getLogger('multidownloader.test').warning('ct0=SECRET happened')
        for h in logging.getLogger().handlers:
            h.flush()
        report = logs.diagnostics()
        for needle in ('multisource-downloader', 'Python:', 'yt-dlp:', 'ffmpeg:', 'Deno:', 'Log file:'):
            self.assertIn(needle, report)
        self.assertIn('ct0=[redacted] happened', report)
        self.assertNotIn('SECRET', report)
        self.assertIsNone(re.search(re.escape(str(Path.home())), report, re.IGNORECASE))


if __name__ == '__main__':
    unittest.main()
