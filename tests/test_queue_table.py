import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import report, settings
from multidownloader.gui.queue_table import middle, result_text
from multidownloader.runs import FileRecord, JobResult

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON, hidden_root



def result(url, status, **kw):
    return JobResult(run_id='r1', input_url=url, normalized_url=url, platform=kw.pop('platform', 'x'),
                     backend='yt-dlp', status=status, **kw)


class ResultTextTests(unittest.TestCase):
    def test_texts(self):
        one = result('u', 'saved', files=[FileRecord('x/a/2025-10-09_1.mp4', 10, 'h'),
                                          FileRecord('x/a/2025-10-09_1.info.json', 2, 'h')])
        self.assertEqual('2025-10-09_1.mp4', result_text(one))
        two = result('u', 'saved', files=[FileRecord('a_1.jpg', 1, 'h'), FileRecord('a_2.jpg', 1, 'h')])
        self.assertEqual('2 files', result_text(two))
        self.assertEqual('Saved on 2026-10-01',
                         result_text(result('u', 'already_saved', previous={'saved_at': '2026-10-01T05:00:00Z'})))
        self.assertEqual('This site is not supported yet.', result_text(result('u', 'skipped', skip_reason='unsupported')))
        self.assertEqual('This post was deleted or the link is wrong.',
                         result_text(result('u', 'failed', error_message='This post was deleted or the link is wrong.')))

    def test_middle(self):
        url = 'https://www.instagram.com/someone/p/' + 'A' * 60 + '/'
        short = middle(url, 40)
        self.assertEqual(39, len(short))
        self.assertTrue(short.startswith('https://www.instag') and short.endswith('AAAA/'))
        self.assertEqual('https://x.com/a', middle('https://x.com/a'))


class ReportTests(unittest.TestCase):
    def results(self):
        return [result('https://x.com/a/status/1', 'saved', files=[FileRecord('x/a/1.jpg', 3, 'abc123')],
                       post={'uploader': 'witness_account', 'posted_at': '2025-10-09T08:00:00Z'}),
                result('https://x.com/a/status/2', 'failed', error_message='This post was deleted or the link is wrong.'),
                result('https://example.org/<script>', 'skipped', skip_reason='unsupported', platform=None)]

    def test_accounts_left_out_by_default(self):
        page = report.html_report(self.results(), run_id='r1')
        self.assertNotIn('witness_account', page)
        self.assertIn('Account names are left out', page)
        self.assertIn('abc123', page)  # the SHA-256 is in the report
        self.assertIn('This post was deleted or the link is wrong.', page)
        self.assertIn('1 saved', page)

    def test_accounts_when_asked(self):
        self.assertIn('witness_account', report.html_report(self.results(), run_id='r1', include_accounts=True))

    def test_self_contained_and_escaped(self):
        page = report.html_report(self.results(), run_id='r1')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIsNone(re.search(r'<(link|script|img)\b', page))  # no external files

    def test_export_writes_html_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / 'run'
            run_dir.mkdir()
            (run_dir / 'manifest.csv').write_text('status\nsaved\n', encoding='utf-8')
            path = report.export(run_dir, self.results(), Path(tmp) / 'report.html')
            self.assertTrue(path.read_text(encoding='utf-8').startswith('<!doctype html>'))
            self.assertEqual('status\nsaved\n', (Path(tmp) / 'report.csv').read_text(encoding='utf-8'))


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class QueueWindowTests(unittest.TestCase):
    """Hidden root window only: nothing appears or takes the focus."""

    def setUp(self):
        self.root = hidden_root(self)
        import multisource_downloader_ui as ui
        self.ui = ui
        with mock.patch.object(ui.signins, 'summary', return_value=''):
            self.app = ui.DownloaderUI(self.root, offer_resume=False, prefs=settings.defaults())
        self.addCleanup(lambda: __import__('logging').getLogger().removeHandler(self.app._log_handler))
        self.table = self.app.table

    def values(self, url):
        return self.table.tree.item(url, 'values')

    def test_rows_follow_the_run(self):
        from multidownloader.runner import JobFinished, JobProgress, JobStarted
        self.assertIn('Nothing in the queue yet', self.table.empty.cget('text'))
        a, b = 'https://x.com/a/status/1', 'https://www.youtube.com/watch?v=aaaaaaaaaaa'
        self.table.reset([(a, 'Twitter'), (b, 'YouTube')])
        self.assertEqual(('… Waiting', 'X (Twitter)'), self.values(a)[:2])
        self.app._counts = {'total': 2}
        self.app._handle_event(JobStarted(1, a))
        self.app._handle_event(JobProgress(1, a, 0.45, 450, 1000, 10.0))
        self.assertEqual('↓ 45 %', self.values(a)[0])
        self.assertEqual('450 B of 1000 B', self.values(a)[3])
        self.app._handle_event(JobFinished(1, a, result(a, 'saved', files=[FileRecord('x/a/1.jpg', 1, 'h')])))
        self.app._handle_event(JobFinished(2, b, result(b, 'failed', error_message='Could not connect.')))
        self.assertEqual(('✓ Saved', '1.jpg'), (self.values(a)[0], self.values(a)[3]))
        self.assertEqual(('✕ Failed', 'Could not connect.'), (self.values(b)[0], self.values(b)[3]))
        self.assertEqual(('success',), self.table.tree.item(a, 'tags'))
        self.table.set_filter('Failed')
        self.assertEqual((b,), self.table.tree.get_children())
        self.table.set_filter('Saved')
        self.assertEqual((a,), self.table.tree.get_children())
        self.table.set_filter('Waiting')
        self.assertEqual('No waiting links.', self.table.empty.cget('text'))

    def test_after_a_run_no_dialog_and_retry_failed(self):
        a = 'https://x.com/a/status/1'
        self.table.reset([(a, 'Twitter')])
        failed = result(a, 'failed', error_message='Could not connect.', error_kind='network', error_action='retry')
        self.table.finished(a, failed)
        run = mock.Mock(counts=mock.Mock(return_value={'failed': 1}), finished=True)
        with mock.patch.object(self.ui.messagebox, 'askyesno') as ask, \
                mock.patch.object(self.ui.messagebox, 'showerror') as show:
            self.app._run_finished(None, [a], [(a, failed)], run)
        ask.assert_not_called()
        show.assert_not_called()
        self.assertEqual('Retry failed (1)', self.app.retry_btn.cget('text'))
        self.assertTrue(self.app.retry_btn.grid_info())
        self.assertIn('Select Retry failed to try them again.', self.app.status_var.get())
        self.assertEqual((a,), self.table.tree.selection())  # focus goes to the first failed row
        with mock.patch.object(self.app, 'start_download') as start:
            self.app.retry_failed()
        start.assert_called_once_with([a], handlers=['Twitter'], keep_queue=True)

    def test_signin_offer_only_for_signin_errors(self):
        a = 'https://www.instagram.com/p/AbC/'
        self.table.reset([(a, 'Instagram')])
        failed = result(a, 'failed', platform='instagram', error_kind='auth_required', error_action='fix_signin',
                        error_message='This post needs you to be signed in to Instagram.')
        self.table.finished(a, failed)
        run = mock.Mock(counts=mock.Mock(return_value={'failed': 1}), finished=True)
        with mock.patch.object(self.ui.messagebox, 'askyesno', return_value=False) as ask:
            self.app._run_finished(None, [a], [(a, failed)], run)
        self.assertEqual('1 link needs an Instagram sign-in. Sign in now?', ask.call_args.args[1])

    def test_menu_key_opens_the_row_menu(self):
        # the keyboard's menu key has a different name in Tk on each platform
        bound = set(self.table.tree.bind())
        self.assertIn('<Shift-Key-F10>', bound)
        self.assertTrue({'<Key-App>', '<Key-Menu>'} & bound, bound)

    def test_download_again_and_remove(self):
        a = 'https://x.com/a/status/1'
        self.table.reset([(a, 'Twitter')])
        self.table.finished(a, result(a, 'saved', files=[FileRecord('x/1.jpg', 1, 'h')]))
        self.table.tree.selection_set(a)
        self.app.remove_rows()
        self.assertEqual({}, self.table.rows)
        self.assertIn('Nothing in the queue yet', self.table.empty.cget('text'))

    def test_window_never_squeezes_the_queue(self):
        # at any display scaling the minimum size leaves room for every control and the queue's rows
        self.root.update_idletasks()
        min_w, min_h = self.root.minsize()
        self.assertGreaterEqual(min_h, self.app.frm.winfo_reqheight())
        self.assertGreaterEqual(min_w, self.app.frm.winfo_reqwidth())
        self.assertEqual(6, int(self.table.tree.cget('height')))
        before = min_h
        self.app.toggle_details()
        self.assertGreater(self.root.minsize()[1], before)  # the log gets room as well

    def test_details_log(self):
        import logging
        self.assertFalse(self.app.details.grid_info())
        self.app.toggle_details()
        self.assertEqual('Hide details', self.app.details_link.cget('text'))
        self.assertTrue(self.app.prefs['details_open'])
        logging.getLogger('multidownloader.test').warning('cookie sent: sessionid=SECRET')
        self.app._drain_log()
        text = self.app.details.get('1.0', 'end')
        self.assertIn('cookie sent: sessionid=[redacted]', text)
        self.assertNotIn('SECRET', text)
        self.app.toggle_details()
        self.assertEqual('Show details', self.app.details_link.cget('text'))


if __name__ == '__main__':
    unittest.main()
