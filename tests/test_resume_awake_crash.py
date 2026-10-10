import json
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import awake, runs
from multidownloader.history import History
from multidownloader.runs import Run

from test_runs import FakeDownloader

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON, hidden_root



class Interrupting(FakeDownloader):
    """Saves the first link, then the process 'dies' (Ctrl+C, power cut)."""

    def download(self, source, url, options, out_dir=None):
        if self.calls:
            raise KeyboardInterrupt
        return super().download(source, url, options, out_dir)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.pointer = self.tmp / 'data' / 'last_run.json'
        self.root = self.tmp / 'out'
        self.links = [{'url': f'https://x.com/a/status/{i}', 'handler': 'Twitter'} for i in (1, 2, 3)]

    def test_a_stopped_run_is_offered_with_what_is_left(self):
        run = Run(self.root, last_run_pointer=self.pointer)
        run.plan(self.links)
        run.download(FakeDownloader({'1.jpg': b'1'}), 'Twitter', self.links[0]['url'])
        # no finish(): the app stopped here
        unfinished = runs.last_unfinished_run(self.pointer)
        self.assertEqual([link['url'] for link in self.links[1:]], [link['url'] for link in unfinished.remaining])
        self.assertEqual(self.root.resolve(), unfinished.output_root)
        self.assertEqual('Your last run stopped before it finished. 2 links were not done.', unfinished.message())

    def test_finished_resumed_or_dismissed_runs_are_not_offered(self):
        run = Run(self.root, last_run_pointer=self.pointer)
        run.plan(self.links)
        for link in self.links:
            run.download(FakeDownloader({'f.jpg': link['url'].encode()}), 'Twitter', link['url'])
        run.finish()
        self.assertIsNone(runs.last_unfinished_run(self.pointer))

        run = Run(self.root, last_run_pointer=self.pointer)
        run.plan(self.links)
        runs.last_unfinished_run(self.pointer).close('dismissed')
        self.assertIsNone(runs.last_unfinished_run(self.pointer))
        plan = json.loads((run.dir / 'run.json').read_text(encoding='utf-8'))
        self.assertEqual('dismissed', plan['closed'])

    def test_no_pointer_means_nothing_is_recorded(self):
        run = Run(self.root)
        run.plan(self.links)
        self.assertTrue((run.dir / 'run.json').exists())
        self.assertFalse(self.pointer.exists())
        self.assertIsNone(runs.last_unfinished_run(self.pointer))

    def test_interrupted_batch_then_resume(self):
        from multidownloader import batch
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        history = History(self.tmp / 'history.sqlite')
        self.addCleanup(history.close)
        items = [batch.ManifestItem('twitter', link['url']) for link in self.links]
        no_pauses = mock.patch.multiple('multidownloader.runner', DEFAULT_DELAY=(0, 0), DELAYS={})
        no_pauses.start()
        self.addCleanup(no_pauses.stop)
        # Ctrl+C during the second link: the run stops cleanly instead of crashing
        result = batch.execute_batch(items, self.root, downloader=Interrupting({'p.jpg': b'1'}), history=history,
                                     last_run_pointer=self.pointer)
        self.assertTrue(result['stopped'])
        unfinished = runs.last_unfinished_run(self.pointer)
        self.assertEqual([link['url'] for link in self.links[1:]], [link['url'] for link in unfinished.remaining])

        # batch --resume finishes it in the same output folder, with nothing real touched
        manifest_file = self.tmp / 'unused.json'
        original = runs.last_unfinished_run
        with mock.patch.object(batch.runs, 'default_last_run_pointer', return_value=self.pointer), \
                mock.patch.object(batch.custody, 'default_anchor_path', return_value=self.tmp / 'anchors.log'), \
                mock.patch.object(batch.runs, 'last_unfinished_run', side_effect=lambda: original(self.pointer)), \
                mock.patch.object(batch, 'History', return_value=history), \
                mock.patch.object(batch, 'Downloader', return_value=FakeDownloader({'p.jpg': b'2'})), \
                mock.patch('multidownloader.session_store.migrate_legacy_sessions'), \
                mock.patch('multidownloader.logs.setup'), \
                mock.patch.object(batch, 'keep_awake', wraps=awake.keep_awake) as awake_used:
            code = batch.main(['--resume', '--out-dir', str(manifest_file.parent / 'ignored')])
        self.assertEqual(0, code)
        self.assertTrue(awake_used.call_args.args[0])  # keep-awake on by default
        self.assertIsNone(runs.last_unfinished_run(self.pointer))
        self.assertFalse((self.tmp / 'ignored').exists())  # resumed into the original folder
        resumed_runs = sorted((self.root / '_runs').iterdir())
        self.assertEqual(2, len(resumed_runs))

    def test_resume_with_nothing_to_resume(self):
        from multidownloader import batch
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        with mock.patch.object(batch.runs, 'last_unfinished_run', return_value=None), \
                mock.patch('multidownloader.session_store.migrate_legacy_sessions'), \
                mock.patch('multidownloader.logs.setup'):
            self.assertEqual(2, batch.main(['--resume']))


class KeepAwakeTests(unittest.TestCase):
    def test_windows_holds_and_releases(self):
        calls = []
        with mock.patch.object(awake.sys, 'platform', 'win32'), \
                mock.patch.object(awake, '_windows_hold', side_effect=lambda on: calls.append(on) or True):
            with awake.keep_awake() as held:
                self.assertTrue(held)
                self.assertEqual([True], calls)
        self.assertEqual([True, False], calls)

    def test_flags(self):
        self.assertEqual(0x80000001, awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED)

    def test_macos_uses_caffeinate_for_this_process(self):
        proc = mock.Mock()
        with mock.patch.object(awake.sys, 'platform', 'darwin'), \
                mock.patch.object(awake.shutil, 'which', return_value='/usr/bin/caffeinate'), \
                mock.patch.object(awake.subprocess, 'Popen', return_value=proc) as popen:
            with awake.keep_awake() as held:
                self.assertTrue(held)
        args = popen.call_args.args[0]
        self.assertEqual(['caffeinate', '-i', '-w'], args[:3])
        proc.terminate.assert_called_once()

    def test_linux_without_systemd_runs_anyway(self):
        with mock.patch.object(awake.sys, 'platform', 'linux'), mock.patch.object(awake.shutil, 'which',
                                                                                  return_value=None):
            with awake.keep_awake() as held:
                self.assertFalse(held)

    def test_disabled_and_failures_never_stop_a_run(self):
        with mock.patch.object(awake, '_windows_hold') as hold, awake.keep_awake(enabled=False) as held:
            self.assertFalse(held)
        hold.assert_not_called()
        with mock.patch.object(awake.sys, 'platform', 'win32'), \
                mock.patch.object(awake, '_windows_hold', side_effect=OSError('no kernel32')):
            with awake.keep_awake() as held:
                self.assertFalse(held)

    @unittest.skipUnless(sys.platform == 'win32', 'Windows API')
    def test_real_windows_call(self):
        with awake.keep_awake() as held:
            self.assertTrue(held)


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class CrashTests(unittest.TestCase):
    def setUp(self):
        self.root = hidden_root(self)
        for target in ('multidownloader.logs.diagnostics',):
            patcher = mock.patch(target, return_value='diagnostics')
            patcher.start()
            self.addCleanup(patcher.stop)
        saved = threading.excepthook, sys.excepthook

        def restore():
            threading.excepthook, sys.excepthook = saved

        self.addCleanup(restore)

    def raise_and_capture(self, exc):
        try:
            raise exc
        except BaseException:
            return sys.exc_info()

    def test_dialog_with_redacted_details(self):
        from multidownloader.gui import crash
        recovered = []
        handler = crash.install(self.root, on_crash=lambda: recovered.append(True))
        with self.assertLogs('multidownloader.crash', level='ERROR'):
            handler.handle(*self.raise_and_capture(RuntimeError('auth_token=SECRET broke')))
        self.assertEqual([True], recovered)
        self.assertTrue(handler.dialog.winfo_exists())
        self.assertNotIn('SECRET', handler.dialog.details)
        self.assertIn('RuntimeError', handler.dialog.details)
        handler.dialog.copy()
        self.assertEqual(handler.dialog.details, self.root.clipboard_get())

    def test_report_link_has_no_details(self):
        from multidownloader.gui import crash
        url = crash.report_url('RuntimeError: broke')
        self.assertTrue(url.startswith(crash.ISSUES_URL + '?title='))
        self.assertNotIn('body', url)

    def test_exit_is_not_a_crash(self):
        from multidownloader.gui import crash
        handler = crash.install(self.root)
        handler.handle(*self.raise_and_capture(SystemExit(0)))
        handler.handle(*self.raise_and_capture(KeyboardInterrupt()))
        self.assertIsNone(handler.dialog)

    def test_thread_errors_reach_the_dialog(self):
        from multidownloader.gui import crash
        handler = crash.install(self.root)

        def boom():
            raise ValueError('worker failed')

        with self.assertLogs('multidownloader.crash', level='ERROR'):
            thread = threading.Thread(target=boom)
            thread.start()
            thread.join()
        import time
        deadline = time.time() + 5
        while handler.dialog is None and time.time() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.assertIsNotNone(handler.dialog)

    def test_tk_callback_errors_reach_the_dialog(self):
        from multidownloader.gui import crash
        handler = crash.install(self.root)
        self.assertEqual(handler.handle, self.root.report_callback_exception)

    def test_resume_bar(self):
        import multisource_downloader_ui as ui
        unfinished = mock.Mock()
        unfinished.message.return_value = 'Your last run stopped before it finished. 2 links were not done.'
        with mock.patch.object(ui.runs, 'last_unfinished_run', return_value=unfinished), \
                mock.patch.object(ui.signins, 'summary', return_value=''):
            app = ui.DownloaderUI(self.root)
        self.assertEqual('! Your last run stopped before it finished. 2 links were not done.', app.resume_var.get())
        self.assertTrue(app.resume_bar.grid_info())
        with mock.patch.object(ui.custody, 'default_anchor_path', return_value=Path('anchors.log')):
            app.dismiss_unfinished()
        unfinished.close.assert_called_once_with('dismissed', anchor_path=Path('anchors.log'))
        self.assertFalse(app.resume_bar.grid_info())


if __name__ == '__main__':
    unittest.main()
