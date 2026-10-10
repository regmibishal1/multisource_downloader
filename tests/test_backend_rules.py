"""The backend interface and the rules from the brief (T2, T6)."""
import ast
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import auth, errors
from multidownloader.backends.base import Backend, require
from multidownloader.batch import next_step
from multidownloader.core import Downloader

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'multidownloader'
# modules that may import Tk inside a function: they open a window on request only
LAZY_TK_ALLOWED = {'sources/instagram.py', 'logs.py'}


def engine_modules():
    for path in sorted(PACKAGE.rglob('*.py')):
        rel = path.relative_to(PACKAGE).as_posix()
        if not rel.startswith('gui/'):
            yield rel, path


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from multidownloader import session_store
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', Path(tmp.name) / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.downloader = Downloader(tmp.name)

    def test_every_handler_is_a_backend(self):
        for source, handler in self.downloader.handlers.items():
            with self.subTest(source=source):
                self.assertIsInstance(handler, Backend)
                self.assertIn(handler.name, {'yt-dlp', 'instaloader', 'gdown', 'gallery-dl'})
                ok, reason = handler.available()
                self.assertIsInstance(ok, bool)
                self.assertIsInstance(reason, str)
                self.assertTrue(ok or reason)

    def test_backend_status(self):
        rows = self.downloader.backend_status()
        self.assertEqual(set(self.downloader.list_sources()), {row[0] for row in rows})

    def test_missing_library_says_what_to_install(self):
        from multidownloader.sources import instagram, yt_dlp_base
        for module, flag, source in ((yt_dlp_base, 'YTDLP_AVAILABLE', 'YouTube'),
                                     (instagram, 'INSTALOADER_AVAILABLE', 'Instagram')):
            with self.subTest(source=source), mock.patch.object(module, flag, False), \
                    self.assertRaises(RuntimeError) as ctx:
                self.downloader.download(source, 'https://example.org/x', {})
            info = errors.classify(ctx.exception)
            self.assertEqual((errors.MISSING_COMPONENT, errors.HOW_TO_FIX), (info.kind, info.action))
            self.assertIn('Install it with: python -m pip install', info.message)
            self.assertEqual('', next_step(info))  # the message already has the command

    def test_require(self):
        class Missing:
            name = 'x'

            def available(self):
                return False, 'x is not installed.'

            def download(self, url, out_dir, options):
                return True

        with self.assertRaises(RuntimeError) as ctx:
            require(Missing())
        self.assertEqual('x is not installed.', str(ctx.exception))


class RuleTests(unittest.TestCase):
    def test_engine_imports_without_any_ui_toolkit(self):
        names = ['multidownloader.' + rel[:-3].replace('/', '.').replace('.__init__', '')
                 for rel, _ in engine_modules()]
        code = ("import sys\n"
                "for blocked in ('tkinter', 'webview'):\n    sys.modules[blocked] = None\n"
                "import importlib\n"
                f"for name in {names!r}:\n    importlib.import_module(name)\n")
        proc = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr[-2000:])

    def test_no_ui_import_at_module_level(self):
        for rel, path in engine_modules():
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
                    if any(n.split('.')[0] in ('tkinter', 'webview') for n in names):
                        with self.subTest(module=rel, line=node.lineno):
                            self.assertIn(rel, LAZY_TK_ALLOWED, 'only lazy imports in allowed modules')
                            self.assertNotIn(node, tree.body, 'Tk must not be imported at module level')

    def test_nothing_changes_the_working_folder(self):
        for path in sorted(PACKAGE.rglob('*.py')) + [ROOT / 'multisource_downloader_ui.py']:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == 'chdir':
                    self.fail(f'{path.relative_to(ROOT)}:{node.lineno} calls chdir')


class AuthProviderTests(unittest.TestCase):
    def tty(self, value=True):
        stdin = mock.Mock()
        stdin.isatty.return_value = value
        return stdin

    def test_terminal_asks_only_in_a_terminal(self):
        prompt = mock.Mock(return_value='archiver')
        secret = mock.Mock(return_value='pw')
        out = io.StringIO()
        provider = auth.TerminalAuth(stdin=self.tty(False), prompt=prompt, secret=secret, out=out)
        self.assertIsNone(provider.request_credentials('Instagram', 'why'))
        self.assertIsNone(provider.request_2fa_code('Instagram'))
        prompt.assert_not_called()

        provider = auth.TerminalAuth(stdin=self.tty(True), prompt=prompt, secret=secret, out=out)
        self.assertEqual(auth.Credentials('archiver', 'pw'), provider.request_credentials('Instagram', 'why'))
        self.assertIn('why', out.getvalue())
        self.assertIn('not saved', secret.call_args.args[0])
        prompt.return_value = ' 123 456 '
        self.assertEqual('123456', provider.request_2fa_code('Instagram'))

    def test_cancel_and_empty_answers(self):
        provider = auth.TerminalAuth(stdin=self.tty(True), prompt=mock.Mock(side_effect=EOFError),
                                     secret=mock.Mock(), out=io.StringIO())
        self.assertIsNone(provider.request_credentials('Instagram', 'why'))
        provider = auth.TerminalAuth(stdin=self.tty(True), prompt=mock.Mock(return_value='u'),
                                     secret=mock.Mock(return_value=''), out=io.StringIO())
        self.assertIsNone(provider.request_credentials('Instagram', 'why'))

    def test_providers_satisfy_the_protocol(self):
        for provider in (auth.NoAuth(), auth.TerminalAuth()):
            self.assertIsInstance(provider, auth.AuthProvider)
        self.assertIsNone(auth.NoAuth().request_credentials('X', 'why'))

    def test_instagram_password_signin_asks_through_the_provider(self):
        from multidownloader.signins import SignInError
        from multidownloader.sources.instagram import InstagramHandler
        handler = InstagramHandler()
        provider = mock.Mock()
        provider.request_credentials.return_value = auth.Credentials('archiver', 'pw')
        provider.request_2fa_code.return_value = '123456'

        def fake_login(username, password, ask_code):
            self.assertEqual(('archiver', 'pw'), (username, password))
            self.assertEqual('123456', ask_code())
            return 'loader'

        with mock.patch.object(handler, 'password_login', side_effect=fake_login), \
                mock.patch.object(handler, 'save_password_signin', return_value='saved') as save:
            self.assertEqual('saved', handler.password_signin(provider, 'terms-v'))
        save.assert_called_once_with('loader', 'archiver', 'terms-v')

        with self.assertRaises(SignInError) as ctx:
            handler.password_signin(auth.NoAuth(), 'terms-v')
        self.assertIn('--browser firefox', str(ctx.exception))


class SigninCliPasswordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        from multidownloader import session_store
        for target, value in ((session_store, 'SESSION_ROOT'),):
            patcher = mock.patch.object(target, value, Path(tmp.name) / '.sessions')
            patcher.start()
            self.addCleanup(patcher.stop)
        for target in ('multidownloader.session_store.migrate_legacy_sessions', 'multidownloader.logs.setup'):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *argv, tty=False):
        from multidownloader import signin_cli
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch('sys.stdin.isatty', return_value=tty), \
                mock.patch.object(auth.TerminalAuth, '_interactive', return_value=tty):
            try:
                code = signin_cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, err.getvalue()

    def test_needs_a_terminal(self):
        code, err = self.run_cli('import', 'instagram', '--password', '--accept-terms')
        self.assertEqual(2, code)
        self.assertIn('needs one', err)

    def test_instagram_only(self):
        code, err = self.run_cli('import', 'x', '--password', '--accept-terms', tty=True)
        self.assertEqual(2, code)
        self.assertIn('only for Instagram', err)

    def test_asks_in_the_terminal(self):
        from multidownloader.sources.instagram import InstagramHandler
        with mock.patch.object(InstagramHandler, 'password_signin') as signin:
            code, _err = self.run_cli('import', 'instagram', '--password', '--accept-terms', tty=True)
        self.assertEqual(0, code)
        provider, terms = signin.call_args.args
        self.assertIsInstance(provider, auth.TerminalAuth)
        self.assertTrue(terms)


if __name__ == '__main__':
    unittest.main()
