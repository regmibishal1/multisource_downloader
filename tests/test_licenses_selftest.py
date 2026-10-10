import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import session_store
from multidownloader.gui import selftest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

try:
    import third_party_licenses
except ImportError:  # packaging is not installed: the script needs it
    third_party_licenses = None


class SelfTestTests(unittest.TestCase):
    def test_runs_in_a_temporary_folder_and_puts_everything_back(self):
        env_before = {name: os.environ.get(name) for name in selftest._ENV}
        root_before = session_store.SESSION_ROOT
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'report.txt'
            with mock.patch('builtins.print'):
                code = selftest.run(report=str(report))
            text = report.read_text(encoding='utf-8')
        if 'yt-dlp-ejs is missing' in text or 'No module named' in text.split('download')[0]:
            self.skipTest('yt-dlp is not fully installed here')
        self.assertEqual(0, code, text)
        self.assertIn('ok    download: 1 file, manifest, SHA256SUMS and custody log written and verified', text)
        self.assertIn('ok    sign-in: saved, read back and removed', text)
        self.assertTrue(text.endswith('self-test: ok\n'))
        self.assertEqual(env_before, {name: os.environ.get(name) for name in selftest._ENV})
        self.assertEqual(root_before, session_store.SESSION_ROOT)

    def test_refuses_a_sign_in_folder_outside_the_temporary_one(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            with mock.patch.object(session_store, 'SESSION_ROOT', Path(other)):
                with self.assertRaisesRegex(RuntimeError, 'nothing was changed'):
                    selftest._fake_signin(Path(tmp))
            self.assertEqual([], list(Path(other).iterdir()))

    def test_bundled_components_must_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'MultisourceDownloader.exe'
            (Path(tmp) / 'components.txt').write_text('instaloader\nnot_a_real_module\n', encoding='utf-8')
            with mock.patch.object(selftest.sys, 'frozen', True, create=True), \
                    mock.patch.object(selftest.sys, 'executable', str(exe)), \
                    mock.patch.object(selftest, 'OPTIONAL', (('not_a_real_module', 'Example'),)):
                self.assertEqual(['instaloader', 'not_a_real_module'], selftest.expected_components())
                with self.assertRaisesRegex(RuntimeError, 'bundled but cannot load: Example'):
                    selftest._components()

    def test_ui_has_the_flags(self):
        import multisource_downloader_ui as ui
        args = ui.parse_args(['--self-test', '--report', 'r.txt'])
        self.assertTrue(args.self_test)
        self.assertEqual('r.txt', args.report)
        with mock.patch.object(ui, 'logs') as logs, mock.patch('multidownloader.gui.selftest.run', return_value=0) as run:
            with self.assertRaises(SystemExit) as done:
                ui.main(['--self-test'])
        self.assertEqual(0, done.exception.code)
        run.assert_called_once()
        logs.setup.assert_not_called()  # the user's log file is never opened by a self-test


@unittest.skipIf(third_party_licenses is None, 'packaging is not installed')
class LicenseFileTests(unittest.TestCase):
    def test_lists_bundled_packages_and_leaves_out_gallery_dl(self):
        dists = third_party_licenses.closure(third_party_licenses.BUNDLED)
        names = {third_party_licenses._key(d.metadata['Name']) for d in dists}
        if 'yt-dlp' not in names:
            self.skipTest('yt-dlp is not installed here')
        self.assertNotIn('gallery-dl', names)
        self.assertFalse(names & third_party_licenses.BUILD_ONLY)
        text = third_party_licenses.render(dists)
        self.assertIn('- yt-dlp ', text)
        self.assertIn('gallery-dl (GPL-2.0-only) is not part of this app', text)
        self.assertIn('[LICENSE', text)

    def test_build_script_excludes_gallery_dl_and_ships_the_licenses(self):
        source = (ROOT / 'scripts' / 'build_exe.py').read_text(encoding='utf-8')
        self.assertIn("'--exclude-module=gallery_dl'", source)
        self.assertIn('THIRD_PARTY_LICENSES.txt', source)

    def test_no_browser_cookie3(self):
        # brief T19: yt-dlp's own cookie reader replaces it (LGPL)
        for name in ('pyproject.toml', 'requirements.txt', 'requirements-lock.txt'):
            self.assertNotIn('browser-cookie3', (ROOT / name).read_text(encoding='utf-8').lower(), name)


if __name__ == '__main__':
    unittest.main()
