import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import settings
from multidownloader.runs import FileRecord, JobResult

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON, hidden_root


ROOT = Path(__file__).resolve().parents[1]


def result(status, **kw):
    return JobResult(run_id='r1', input_url='u', normalized_url='u', platform='youtube', backend='yt-dlp',
                     status=status, **kw)


class SimpleResultTests(unittest.TestCase):
    def setUp(self):
        import multisource_downloader_ui as ui
        self.simple_result = ui.simple_result

    def test_one_link(self):
        saved = result('saved', post={'title': 'Rally at Maitighar\nsecond line'},
                       files=[FileRecord('youtube/a/1.mp4', 1, 'h'), FileRecord('youtube/a/1.info.json', 1, 'h')])
        self.assertEqual('✓ Saved "Rally at Maitighar" (1 file).', self.simple_result([saved]))
        two = result('saved', files=[FileRecord('a_1.jpg', 1, 'h'), FileRecord('a_2.jpg', 1, 'h')])
        self.assertEqual('✓ Saved (2 files).', self.simple_result([two]))
        self.assertEqual('= Already saved "Live". Nothing new to download.',
                         self.simple_result([result('already_saved', post={'title': 'Live'})]))
        self.assertEqual('✕ Failed: This post was deleted or the link is wrong.',
                         self.simple_result([result('failed', error_message='This post was deleted or the link is wrong.')]))
        self.assertEqual('⊘ Skipped: this site is not supported yet.',
                         self.simple_result([result('skipped', skip_reason='unsupported')]))

    def test_several_links_and_stop(self):
        results = [result('saved'), result('saved'), result('failed'), result('already_saved')]
        self.assertEqual('✕ 2 saved, 1 already saved, 1 failed.', self.simple_result(results))
        self.assertEqual('✓ 2 saved.', self.simple_result(results[:2]))
        self.assertEqual('■ Stopped. 1 saved.', self.simple_result([result('saved')], stopped=True))


class StartViewTests(unittest.TestCase):
    def setUp(self):
        import multisource_downloader_ui as ui
        self.ui = ui
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings_file = Path(tmp.name) / 'settings.json'
        patcher = mock.patch.object(ui.settings, 'settings_path', return_value=self.settings_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def view(self, argv, prefs=None, frozen=False):
        with mock.patch.object(self.ui.sys, 'frozen', frozen, create=True):
            return self.ui.start_view(self.ui.parse_args(argv), prefs or settings.defaults())

    def test_flags_win(self):
        self.assertEqual('simple', self.view(['--simple']))
        self.assertEqual('full', self.view(['--full'], frozen=True))
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            self.ui.parse_args(['--simple', '--full'])

    def test_first_start(self):
        self.assertEqual('full', self.view([]))
        self.assertEqual('simple', self.view([], frozen=True))  # the Windows app starts simple

    def test_last_view_is_kept(self):
        self.settings_file.write_text('{}', encoding='utf-8')
        self.assertEqual('simple', self.view([], dict(settings.defaults(), view='simple')))
        self.assertEqual('full', self.view([], dict(settings.defaults(), view='full'), frozen=True))

    def test_view_setting_is_checked(self):
        self.assertTrue(settings.valid('view', 'simple'))
        self.assertFalse(settings.valid('view', 'tiny'))


class PortableRetiredTests(unittest.TestCase):
    def test_no_second_codebase(self):
        import subprocess
        tracked = subprocess.run(['git', 'ls-files', 'portable_manager'], cwd=ROOT, capture_output=True, text=True)
        if tracked.returncode == 0:
            self.assertEqual('', tracked.stdout.strip())
        self.assertTrue((ROOT / 'scripts' / 'build_exe.py').exists())


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class SimpleViewWindowTests(unittest.TestCase):
    """Hidden root window only: nothing appears or takes the focus."""

    def setUp(self):
        self.root = hidden_root(self)
        import multisource_downloader_ui as ui
        self.ui = ui
        with mock.patch.object(ui.signins, 'summary', return_value=''):
            self.app = ui.DownloaderUI(self.root, offer_resume=False, prefs=settings.defaults(), view='simple')
        self.addCleanup(lambda: __import__('logging').getLogger().removeHandler(self.app._log_handler))

    def test_simple_hides_the_queue_and_comes_back(self):
        app = self.app
        self.assertEqual('simple', app.view)
        self.assertEqual('Paste a link', app.links_label.cget('text'))
        self.assertEqual(3, int(app.links.cget('height')))
        for widget in (app.table, app.footer, app.queue_head, app.signins_btn, app.import_btn):
            self.assertFalse(widget.grid_info(), widget)
        self.assertTrue(app.simple_row.grid_info())
        self.assertTrue(app.help_btn.grid_info())  # help stays in the same place (A16)
        self.assertEqual([app.links, app.download_btn, app.help_btn], app.regions())
        app.toggle_view()
        self.assertEqual('full', app.view)
        self.assertEqual('full', app.prefs['view'])
        self.assertTrue(app.table.grid_info())
        self.assertFalse(app.simple_row.grid_info())
        self.assertEqual(6, int(app.links.cget('height')))

    def test_stop_shows_next_to_download_during_a_run(self):
        app = self.app
        self.assertFalse(app.simple_stop.winfo_manager())
        app._runner = self.ui._Starting()
        app._show_stop()
        self.assertEqual('pack', app.simple_stop.winfo_manager())
        app._reset_controls()
        self.assertFalse(app.simple_stop.winfo_manager())

    def test_simple_result_in_the_status_line(self):
        run = mock.Mock(counts=mock.Mock(return_value={'saved': 1}), finished=True,
                        results=[result('saved', post={'title': 'Live'}, files=[FileRecord('a.mp4', 1, 'h')])])
        self.app._run_finished(None, ['u'], [], run)
        self.assertEqual('✓ Saved "Live" (1 file).', self.app.status_var.get())


if __name__ == '__main__':
    unittest.main()
