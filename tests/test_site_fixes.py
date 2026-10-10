import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import errors, session_store, signins
from multidownloader.sources import yt_dlp_base

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON, hidden_root

FIREFOX_157 = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:157.0) Gecko/20100101 Firefox/157.0'


class OwnFirefoxTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.profile = Path(tmp.name) / 'abc.default-release'
        self.profile.mkdir()

    def agent(self, platform='win32'):
        with mock.patch.object(signins, 'firefox_profiles',
                               return_value=[signins.BrowserProfile('default-release', str(self.profile))]), \
                mock.patch.object(signins.sys, 'platform', platform):
            return signins.own_firefox_user_agent()

    def test_from_the_installed_version(self):
        (self.profile / 'compatibility.ini').write_text(
            '[Compatibility]\nLastVersion=157.0.1_20261005135250/20261005135250\n', encoding='utf-8')
        self.assertEqual(FIREFOX_157, self.agent())
        self.assertIn('(Macintosh; Intel Mac OS X 10.15; rv:157.0)', self.agent('darwin'))
        self.assertIn('(X11; Linux x86_64; rv:157.0)', self.agent('linux'))

    def test_without_firefox(self):
        self.assertIsNone(self.agent())  # no compatibility.ini
        with mock.patch.object(signins, 'firefox_profiles', return_value=[]):
            self.assertIsNone(signins.own_firefox_user_agent())


class TikTokIdentityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / 'sessions')
        patcher.start()
        self.addCleanup(patcher.stop)

    def opts(self, handler, firefox=FIREFOX_157):
        with mock.patch.object(signins, 'own_firefox_user_agent', return_value=firefox):
            return yt_dlp_base.YtDlpHandler(handler)._build_opts(str(self.tmp / 'out'), {}, None)

    def test_tiktok_gets_the_users_own_firefox(self):
        self.assertEqual({'User-Agent': FIREFOX_157}, self.opts('TikTok')['http_headers'])

    def test_other_platforms_and_no_firefox_keep_the_default(self):
        self.assertNotIn('http_headers', self.opts('YouTube'))
        self.assertNotIn('http_headers', self.opts('TikTok', firefox=None))

    def test_the_sign_in_windows_agent_wins(self):
        cookie = self.tmp / 'cookies.txt'
        cookie.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        handler = yt_dlp_base.YtDlpHandler('TikTok')
        with mock.patch.object(signins, 'user_agent_for', return_value='Sign-in window agent'), \
                mock.patch.object(signins, 'own_firefox_user_agent', return_value=FIREFOX_157):
            opts = handler._build_opts(str(self.tmp / 'out'), {}, cookie)
        self.assertEqual({'User-Agent': 'Sign-in window agent'}, opts['http_headers'])


class ErrorWordingTests(unittest.TestCase):
    def test_extractor_out_of_date(self):
        for text in ('ERROR: [TikTok] 1: Unexpected response from webpage request; please report this issue on  '
                     'https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. '
                     'Confirm you are on the latest version using  yt-dlp -U',
                     'ERROR: [facebook] 1: Cannot parse data; please report this issue on  https://github.com/'):
            with self.subTest(text=text[:30]):
                info = errors.classify(RuntimeError(text), platform='TikTok')
                self.assertEqual((errors.OUTDATED, errors.HOW_TO_UPDATE), (info.kind, info.action))

    def test_blocked_for_this_ip(self):
        info = errors.classify(RuntimeError('ERROR: [TikTok] 7: Your IP address is blocked from accessing this post'))
        self.assertEqual(errors.GEO_BLOCKED, info.kind)

    def test_specific_rules_still_win(self):
        # a sign-in problem that also says "please report" stays a sign-in problem
        text = 'ERROR: [instagram] x: login required; please report this issue on https://github.com/'
        self.assertEqual(errors.AUTH_REQUIRED, errors.classify(RuntimeError(text)).kind)


class UnfinishedRunAnchorTests(unittest.TestCase):
    def test_closing_an_unfinished_run_anchors_its_custody_log(self):
        from multidownloader import custody, runs, verify
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pointer, anchors = tmp / 'last_run.json', tmp / 'userdata' / 'anchors.log'
            run = runs.Run(tmp / 'out', last_run_pointer=pointer, anchor_path=anchors)
            run.plan([{'url': 'https://x.com/a/status/1', 'handler': 'Twitter'}])
            run._jsonl.close()  # the app stopped here: no run_finished, no anchor
            report = custody.check(run.dir / 'custody.log', anchor_path=anchors)
            self.assertIn('has no anchor', report.message)
            runs.last_unfinished_run(pointer).close('dismissed', anchor_path=anchors)
            self.assertIn(run.run_id, anchors.read_text(encoding='utf-8'))
            self.assertIn('only anchored on this computer',
                          custody.check(run.dir / 'custody.log', anchor_path=anchors).message)
            self.assertTrue(verify.verify(run.dir, check_custody=True, anchor_path=anchors).custody.ok)


class RetryFailedTests(unittest.TestCase):
    def test_only_failures_a_retry_can_fix(self):
        from multidownloader.gui import queue_table
        rows = {}
        for url, kind in (('a', 'network'), ('b', 'not_found'), ('c', 'unsupported'), ('d', 'rate_limited'),
                          ('e', 'geo_blocked'), ('f', None)):
            rows[url] = queue_table.Row(url, 'Twitter', 'X', state='failed', result=mock.Mock(error_kind=kind))
        table = mock.Mock(rows=rows)
        self.assertEqual(['a', 'd', 'f'], [u for u, _h in queue_table.QueueTable.failed_links(table)])
        self.assertEqual(6, len(queue_table.QueueTable.failed_links(table, retryable_only=False)))


class NoConsoleTests(unittest.TestCase):
    def test_streams_get_somewhere_to_write(self):
        import sys
        import multisource_downloader_ui as ui
        with mock.patch.object(sys, 'stdout', None), mock.patch.object(sys, 'stderr', None):
            ui.ensure_std_streams()
            sys.stderr.write('progress bar')  # what gdown does; must not fail
            sys.stdout.write('x')
            self.assertIsNotNone(sys.stdout)
            opened = (sys.stdout, sys.stderr)
        for stream in opened:
            stream.close()

    def test_existing_streams_are_kept(self):
        import sys
        import multisource_downloader_ui as ui
        before = (sys.stdout, sys.stderr)
        ui.ensure_std_streams()
        self.assertEqual(before, (sys.stdout, sys.stderr))

    def test_main_does_it_first(self):
        import multisource_downloader_ui as ui
        with mock.patch.object(ui, 'ensure_std_streams', side_effect=SystemExit(0)) as ensure:
            with self.assertRaises(SystemExit):
                ui.main(['--self-test'])
        ensure.assert_called_once()


class ThreadsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / 'sessions')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_says_not_supported_instead_of_trying(self):
        from multidownloader.sources import threads
        handler = threads.ThreadsHandler()
        with mock.patch.object(threads, 'ytdlp_reads', return_value=False), \
                mock.patch.object(yt_dlp_base.YtDlpHandler, 'download') as ytdlp:
            with self.assertRaises(RuntimeError) as ctx:
                handler.download('https://www.threads.com/@a/post/X1', str(self.tmp))
        ytdlp.assert_not_called()
        info = errors.classify(ctx.exception, platform='Threads')
        self.assertEqual((errors.UNSUPPORTED, errors.COPY_LINK), (info.kind, info.action))
        self.assertTrue(info.message.startswith('Threads posts cannot be downloaded yet'))

    def test_uses_ytdlp_once_it_reads_threads(self):
        from multidownloader.sources import threads
        handler = threads.ThreadsHandler()
        with mock.patch.object(threads, 'ytdlp_reads', return_value=True), \
                mock.patch.object(yt_dlp_base.YtDlpHandler, 'download', return_value=True) as ytdlp:
            self.assertTrue(handler.download('https://www.threads.com/@a/post/X1', str(self.tmp)))
        ytdlp.assert_called_once()

    def test_reader_check(self):
        from multidownloader.sources import threads
        if not yt_dlp_base.YTDLP_AVAILABLE:
            self.skipTest('yt-dlp is not installed')
        self.assertTrue(threads.ytdlp_reads('https://www.youtube.com/watch?v=jNQXAC9IVRw'))
        self.assertFalse(threads.ytdlp_reads('https://example.org/not-a-video-site'))


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class BlueskyRowTests(unittest.TestCase):
    def setUp(self):
        self.root = hidden_root(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', Path(tmp.name) / 'sessions')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_public_only_row_says_so(self):
        from multidownloader.gui.signin_sheet import SignInsWindow
        with mock.patch.object(signins, 'find_firefox_signins', return_value=[]):
            window = SignInsWindow(self.root)
        window.withdraw()
        texts = [w.cget('text') for w in window.rows.winfo_children()
                 for w in ([w] + list(w.winfo_children())) if 'text' in w.keys()]
        self.assertIn('Nothing to sign in to', texts)
        window.destroy()


if __name__ == '__main__':
    unittest.main()
