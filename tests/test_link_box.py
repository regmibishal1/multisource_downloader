import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import settings
from multidownloader.batch import links_from_file

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON

if HAVE_DISPLAY:
    import tkinter as tk


class SettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'settings.json'

    def test_defaults_when_missing(self):
        values = settings.load(self.path)
        self.assertEqual(settings.defaults(), values)
        self.assertTrue(values['output_root'].endswith('Multisource'))

    def test_round_trip_and_schema(self):
        values = settings.defaults()
        values.update(output_root='D:\\archive', window_geometry='1000x700+10+20', parallel=4)
        settings.save(values, self.path)
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(settings.SCHEMA, stored['schema'])
        self.assertEqual(values, settings.load(self.path))

    def test_bad_values_and_unknown_keys(self):
        self.path.write_text(json.dumps({'output_root': '', 'parallel': 40, 'window_geometry': 'huge',
                                         'keep_awake': 'yes', 'surprise': 1, 'include_title': True}), encoding='utf-8')
        with self.assertLogs('multidownloader.settings', level='WARNING'):
            values = settings.load(self.path)
        self.assertEqual(settings.default_output_root(), values['output_root'])
        self.assertEqual(3, values['parallel'])
        self.assertIsNone(values['window_geometry'])
        self.assertTrue(values['keep_awake'])
        self.assertTrue(values['include_title'])
        self.assertNotIn('surprise', values)

    def test_damaged_file(self):
        self.path.write_text('{"output_root": ', encoding='utf-8')
        with self.assertLogs('multidownloader.settings', level='WARNING'):
            self.assertEqual(settings.defaults(), settings.load(self.path))


class ImportFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_scraper_json_and_csv(self):
        (self.dir / 'g.json').write_text(json.dumps({'twitter': ['https://x.com/a/status/1'],
                                                     'youtube': ['https://youtu.be/abc123']}), encoding='utf-8')
        self.assertEqual(['https://x.com/a/status/1', 'https://youtu.be/abc123'], links_from_file(self.dir / 'g.json'))
        (self.dir / 'g.csv').write_text('source,items\nx,"https://x.com/a/status/2, https://x.com/a/status/3"\n',
                                        encoding='utf-8')
        self.assertEqual(['https://x.com/a/status/2', 'https://x.com/a/status/3'], links_from_file(self.dir / 'g.csv'))

    def test_any_text_file(self):
        (self.dir / 'chat.txt').write_text('12:01 - Ram: hera https://www.instagram.com/p/AbC/ ani youtu.be/abc123।\n',
                                           encoding='utf-8')
        self.assertEqual(['https://www.instagram.com/p/AbC/', 'https://youtu.be/abc123'],
                         links_from_file(self.dir / 'chat.txt'))
        # a JSON file that is not the scraper's format is read as text
        (self.dir / 'other.json').write_text('{"note": "see https://x.com/a/status/9"}', encoding='utf-8')
        self.assertEqual(['https://x.com/a/status/9'], links_from_file(self.dir / 'other.json'))


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class LinkBoxTests(unittest.TestCase):
    """Uses a hidden root window only: nothing appears or takes the focus."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        import multisource_downloader_ui as ui
        self.ui = ui
        with mock.patch.object(ui.signins, 'summary', return_value=''):
            self.app = ui.DownloaderUI(self.root, offer_resume=False, prefs=settings.defaults())

    def test_no_source_dropdown_and_a_placeholder(self):
        self.assertFalse(hasattr(self.app, 'source_var'))
        self.assertEqual('', self.app.links_text())  # the placeholder is not text
        self.assertIn('disabled', self.app.download_btn.state())
        self.assertEqual('Download', self.app.download_btn.cget('text'))

    def test_summary_detection_and_button_label(self):
        self.app.set_links_text('https://www.instagram.com/p/AbC/\nhttps://x.com/a/status/1\n'
                                'https://twitter.com/a/status/1?s=20\nhttps://example.org/x')
        self.assertEqual('3 links found: Instagram 1, X (Twitter) 1. 1 duplicate removed. 1 not supported.',
                         self.app.summary_var.get())
        self.assertEqual('Download 2 links', self.app.download_btn.cget('text'))
        self.assertNotIn('disabled', self.app.download_btn.state())
        links, duplicates = self.app.current_links()
        self.assertEqual([('https://www.instagram.com/p/AbC/', 'Instagram'), ('https://x.com/a/status/1', 'Twitter'),
                          ('https://example.org/x', None)], links)
        self.assertEqual(1, duplicates)

    def test_clear(self):
        self.app.set_links_text('https://x.com/a/status/1')
        self.app.clear_links()
        self.assertEqual('No links found yet.', self.app.summary_var.get())
        self.assertIn('disabled', self.app.download_btn.state())

    def test_start_records_unsupported_links_and_uses_each_links_platform(self):
        self.app.set_links_text('https://x.com/a/status/1\nhttps://youtu.be/abc123\nhttps://example.org/x')
        with tempfile.TemporaryDirectory() as tmp:
            self.app.dir_var.set(tmp)
            started = []
            with mock.patch.object(self.ui, 'History'), \
                    mock.patch.object(self.ui.runs, 'default_last_run_pointer', return_value=Path(tmp) / 'p.json'), \
                    mock.patch.object(self.ui.custody, 'default_anchor_path', return_value=Path(tmp) / 'a.log'), \
                    mock.patch.object(self.ui.threading, 'Thread') as thread, \
                    mock.patch.object(self.app, 'save_prefs'):
                thread.return_value.start.side_effect = lambda: started.append(thread.call_args.kwargs['args'])
                self.app.start_download()
            _downloader, run, links, _source, prefs = started[0]
            self.assertEqual(self.app.prefs, prefs)
            self.assertIsNot(self.app.prefs, prefs)  # a copy: Options changes apply to the next run
            self.assertEqual([('https://x.com/a/status/1', 'Twitter'), ('https://youtu.be/abc123', 'YouTube')], links)
            self.assertEqual(['unsupported'], [r.skip_reason for r in run.results])
            self.assertEqual('disabled', str(self.app.links.cget('state')))  # read-only during the run
            run.finish()
            self.app._reset_controls()
            self.assertEqual('normal', str(self.app.links.cget('state')))

    def test_prefs_are_used_and_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = settings.defaults()
            prefs['output_root'] = tmp
            with mock.patch.object(self.ui.signins, 'summary', return_value=''):
                app = self.ui.DownloaderUI(self.root, offer_resume=False, prefs=prefs)
            self.assertEqual(tmp, app.dir_var.get())
            with mock.patch.object(self.ui.settings, 'save') as save:
                app.save_prefs()
            self.assertEqual(tmp, save.call_args.args[0]['output_root'])


if __name__ == '__main__':
    unittest.main()
