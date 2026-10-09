import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import session_store, signin_cli, signins
from multidownloader.platforms import BY_KEY

COOKIES = '# Netscape HTTP Cookie File\n.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tA\n.example.com\tTRUE\t/\tTRUE\t0\tz\t1\n'


class SigninCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cookies = self.tmp / 'cookies.txt'
        self.cookies.write_text(COOKIES, encoding='utf-8')

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch('sys.stdin.isatty', return_value=False):
            try:
                code = signin_cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_import_needs_terms_when_not_interactive(self):
        code, _out, err = self.run_cli('import', 'x', '--cookies', str(self.cookies))
        self.assertEqual(2, code)
        self.assertIn("X's terms do not allow scraping", err)
        self.assertIn('--accept-terms', err)
        self.assertFalse(signins.cookie_path(BY_KEY['x']).exists())

    def test_import_with_accept_terms(self):
        code, out, err = self.run_cli('import', 'twitter', '--cookies', str(self.cookies), '--accept-terms')
        self.assertEqual(0, code, err)
        self.assertIn('Kept 1 X (Twitter) cookies. Removed 1 cookies for other sites', err)
        self.assertIn('Signed in via cookies file', out)

    def test_bad_file(self):
        bad = self.tmp / 'bad.txt'
        bad.write_text('hello', encoding='utf-8')
        code, _out, err = self.run_cli('import', 'x', '--cookies', str(bad), '--accept-terms')
        self.assertEqual(1, code)
        self.assertIn('not in cookies.txt format', err)

    def test_unknown_platform_suggests(self):
        code, _out, err = self.run_cli('import', 'facebok', '--cookies', str(self.cookies))
        self.assertEqual(2, code)
        self.assertIn('Did you mean facebook?', err)

    def test_status_lists_every_platform(self):
        code, out, _err = self.run_cli('status')
        self.assertEqual(0, code)
        for name in ('Instagram', 'X (Twitter)', 'YouTube', 'Google Drive'):
            self.assertIn(name, out)

    def test_remove_all_needs_force_when_not_interactive(self):
        self.run_cli('import', 'x', '--cookies', str(self.cookies), '--accept-terms')
        self.assertEqual(2, self.run_cli('remove', '--all')[0])
        self.assertTrue(signins.cookie_path(BY_KEY['x']).exists())
        self.assertEqual(0, self.run_cli('remove', '--all', '--force')[0])
        self.assertFalse(signins.cookie_path(BY_KEY['x']).exists())


if __name__ == '__main__':
    unittest.main()
