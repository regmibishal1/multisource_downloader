import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import settings, updates

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON

if HAVE_DISPLAY:
    import tkinter as tk

DAY = 24 * 3600
NOW = dt.datetime(2026, 10, 9, 12, 0).timestamp()


class VersionTests(unittest.TestCase):
    def test_release_date(self):
        self.assertEqual(dt.date(2026, 8, 19), updates.release_date('2026.8.19'))
        self.assertEqual(dt.date(2026, 8, 19), updates.release_date('2026.08.19.233000'))
        self.assertIsNone(updates.release_date('1.32.16'))
        self.assertIsNone(updates.release_date('2026.13.40'))

    def test_version_tuple(self):
        self.assertGreater(updates.version_tuple('1.32.16'), updates.version_tuple('1.32.9'))
        self.assertGreater(updates.version_tuple('2026.10.1'), updates.version_tuple('2026.8.19'))

    def test_how_to_update(self):
        self.assertIn('-m pip install -U "yt-dlp[default]"', updates.how_to_update('yt-dlp'))
        with mock.patch.object(updates.sys, 'frozen', True, create=True):
            self.assertIn('Releases page', updates.how_to_update('yt-dlp'))


class NoticeTests(unittest.TestCase):
    today = dt.date(2026, 10, 9)

    def test_old_ytdlp(self):
        found = updates.notices({'yt-dlp': '2026.8.19'}, {'yt-dlp': '2026.10.1'}, today=self.today)
        self.assertEqual(['yt-dlp'], [n.package for n in found])
        self.assertEqual('Your yt-dlp is 51 days old. Sites change often, so downloads may fail.', found[0].message)

    def test_fresh_or_newest_ytdlp_is_quiet(self):
        self.assertEqual([], updates.notices({'yt-dlp': '2026.9.30'}, {}, today=self.today))
        # old by date, but PyPI has nothing newer: no point asking for an update
        self.assertEqual([], updates.notices({'yt-dlp': '2026.8.19'}, {'yt-dlp': '2026.8.19'}, today=self.today))

    def test_offline_still_warns_by_age(self):
        self.assertEqual(1, len(updates.notices({'yt-dlp': '2026.6.1'}, {}, today=self.today)))

    def test_gallery_dl(self):
        found = updates.notices({'gallery-dl': '1.32.16'}, {'gallery-dl': '1.33.0'}, today=self.today)
        self.assertEqual('gallery-dl 1.33.0 is available (you have 1.32.16). Image downloads may need it.',
                         found[0].message)
        self.assertEqual([], updates.notices({'gallery-dl': '1.33.0'}, {'gallery-dl': '1.33.0'}, today=self.today))
        self.assertEqual([], updates.notices({}, {'gallery-dl': '1.33.0'}, today=self.today))


class CheckTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'update_check.json'
        self.calls = []
        self.versions = {'yt-dlp': '2026.8.19', 'gallery-dl': None}

    def fetch(self, name):
        self.calls.append(name)
        return '2026.10.1'

    def test_once_a_day(self):
        found = updates.check(path=self.path, now=NOW, fetch=self.fetch, versions=self.versions)
        self.assertEqual(['yt-dlp'], self.calls)  # gallery-dl is not installed: not asked about
        self.assertEqual('2026.10.1', found[0].latest)
        updates.check(path=self.path, now=NOW + DAY / 2, fetch=self.fetch, versions=self.versions)
        self.assertEqual(['yt-dlp'], self.calls)  # cached
        updates.check(path=self.path, now=NOW + DAY, fetch=self.fetch, versions=self.versions)
        self.assertEqual(['yt-dlp', 'yt-dlp'], self.calls)

    def test_offline(self):
        def down(_name):
            raise OSError('no network')
        found = updates.check(path=self.path, now=NOW, fetch=down, versions=self.versions)
        self.assertEqual(1, len(found))
        self.assertIsNone(found[0].latest)
        updates.check(path=self.path, now=NOW + DAY, fetch=self.fetch, versions=self.versions, online=False)
        self.assertEqual([], self.calls)  # online=False never asks PyPI

    def test_dismiss_until_a_newer_release(self):
        found = updates.check(path=self.path, now=NOW, fetch=self.fetch, versions=self.versions)
        updates.dismiss(found[0], path=self.path)
        self.assertEqual([], updates.check(path=self.path, now=NOW, fetch=self.fetch, versions=self.versions))
        state = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(['yt-dlp==2026.10.1'], state['dismissed'])
        found = updates.check(path=self.path, now=NOW + DAY, fetch=lambda _n: '2026.10.8', versions=self.versions)
        self.assertEqual('2026.10.8', found[0].latest)  # a newer one shows again

    def test_damaged_state_file(self):
        self.path.write_text('not json', encoding='utf-8')
        self.assertEqual(1, len(updates.check(path=self.path, now=NOW, fetch=self.fetch, versions=self.versions)))

    def test_setting(self):
        self.assertTrue(settings.defaults()['check_updates'])
        self.assertFalse(settings.valid('check_updates', 'yes'))


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class UpdateBarTests(unittest.TestCase):
    """Hidden root window only: nothing appears or takes the focus."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        import multisource_downloader_ui as ui
        self.ui = ui
        with mock.patch.object(ui.signins, 'summary', return_value=''):
            self.app = ui.DownloaderUI(self.root, offer_resume=False, prefs=settings.defaults())
        self.addCleanup(lambda: __import__('logging').getLogger().removeHandler(self.app._log_handler))

    def test_bar_shows_and_dismisses(self):
        notice = updates.Notice('yt-dlp', '2026.8.19', '2026.10.1', 51, 'Your yt-dlp is 51 days old.')
        self.app._handle_event(('updates', [notice]))
        self.assertTrue(self.app.update_bar.grid_info())
        self.assertEqual('! Your yt-dlp is 51 days old.', self.app.update_var.get())
        with mock.patch.object(self.ui.updates, 'dismiss') as dismiss:
            self.app.dismiss_updates()
        dismiss.assert_called_once_with(notice)
        self.assertFalse(self.app.update_bar.grid_info())

    def test_off_in_options_means_no_check(self):
        self.app.prefs['check_updates'] = False
        with mock.patch.object(self.ui.threading, 'Thread') as thread:
            self.app.start_update_check()
        thread.assert_not_called()


if __name__ == '__main__':
    unittest.main()
