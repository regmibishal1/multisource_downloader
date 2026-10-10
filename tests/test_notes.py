import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import batch, custody, runs, settings, verify
from multidownloader.runner import Job, Runner
from multidownloader.runs import Run

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON, hidden_root



class FakeDownloader:
    def download(self, source, url, options, out_dir=None):
        (Path(out_dir) / f'{url.rsplit("/", 1)[-1]}.jpg').write_bytes(url.encode())
        return True


A, B = 'https://x.com/a/status/1', 'https://x.com/a/status/2'
NOTES = {'topic': 'Rally', 'where': 'Kathmandu', 'collected_by': 'R.', 'notes': 'Second day\nof the march',
         'surprise': 'ignored'}


class NoteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.anchors = self.tmp / 'anchors.log'

    def run_links(self, notes=None, before=None, after=None):
        run = Run(self.tmp / 'out', notes=notes, anchor_path=self.anchors)
        for url, text in (before or {}).items():
            run.set_link_note(url, text)
        try:
            Runner(run, FakeDownloader(), delays={}, default_delay=(0, 0)).run([Job(A, 'Twitter'), Job(B, 'Twitter')])
        finally:
            run.finish()
        for url, text in (after or {}).items():
            run.set_link_note(url, text)
        return run

    def test_clean_and_describe(self):
        self.assertEqual({'topic': 'Rally', 'where': 'Kathmandu', 'collected_by': 'R.',
                          'notes': 'Second day\nof the march'}, runs.clean_notes(NOTES))
        self.assertEqual('Topic: Rally. Where: Kathmandu. Collected by: R. Notes: Second day of the march.',
                         runs.describe_notes(NOTES))
        self.assertEqual({}, runs.clean_notes({'topic': '  ', 'where': None}))

    def test_run_and_link_notes_in_the_manifest(self):
        run = self.run_links(NOTES, before={A: 'Filmed from the bridge'})
        rows = {r['input_url']: r for r in runs.read_manifest(run.dir / 'manifest.jsonl')}
        self.assertEqual('Filmed from the bridge', rows[A]['notes']['link'])
        self.assertEqual('Rally', rows[A]['notes']['run']['topic'])
        self.assertIsNone(rows[B]['notes']['link'])
        saved = json.loads((run.dir / 'notes.json').read_text(encoding='utf-8'))
        self.assertEqual({A: 'Filmed from the bridge'}, saved['links'])
        with open(run.dir / 'manifest.csv', encoding='utf-8-sig', newline='') as fp:
            csv_rows = {r['input_url']: r for r in csv.DictReader(fp)}
        self.assertEqual('Filmed from the bridge', csv_rows[A]['note'])

    def test_no_notes_no_file(self):
        run = self.run_links()
        self.assertFalse((run.dir / 'notes.json').exists())
        self.assertIsNone(runs.read_manifest(run.dir / 'manifest.jsonl')[0]['notes'])

    def test_notes_are_in_the_custody_log_and_it_stays_valid(self):
        run = self.run_links(NOTES, after={B: 'Added later'})
        entries = custody.read_chain(run.dir / 'custody.log').entries
        self.assertEqual('Rally', entries[0]['detail']['notes']['topic'])
        noted = [e for e in entries if e['event'] == 'noted']
        self.assertEqual([('https://x.com/a/status/2', 'Added later')], [(e['detail']['url'], e['note']) for e in noted])
        self.assertEqual('anchored', entries[-1]['event'])  # anchored again after the note
        report = verify.verify(run.dir, check_custody=True, anchor_path=self.anchors)
        self.assertTrue(report.ok, report.lines())
        self.assertEqual({B: 'Added later'}, json.loads((run.dir / 'notes.json').read_text(encoding='utf-8'))['links'])

    def test_removing_a_note(self):
        run = self.run_links(after={A: 'x'})
        run.set_link_note(A, '  ')
        self.assertEqual({}, json.loads((run.dir / 'notes.json').read_text(encoding='utf-8'))['links'])

    def test_command_line_flags(self):
        args = batch.build_arg_parser().parse_args(['links.txt', '--topic', 'Rally', '--where', 'Kathmandu',
                                                    '--collected-by', 'R.', '--note', 'Day 2'])
        self.assertEqual(('Rally', 'Kathmandu', 'R.', 'Day 2'), (args.topic, args.where, args.collected_by, args.note))
        with mock.patch.object(batch, 'Run', wraps=Run) as made:
            batch.execute_batch([batch.ManifestItem('', A)], self.tmp / 'cli', downloader=FakeDownloader(),
                                notes={'topic': 'Rally'})
        self.assertEqual({'topic': 'Rally'}, made.call_args.kwargs['notes'])


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class NotesWindowTests(unittest.TestCase):
    """Hidden root window only: nothing appears or takes the focus."""

    def setUp(self):
        self.root = hidden_root(self)
        import multisource_downloader_ui as ui
        self.ui = ui
        with mock.patch.object(ui.signins, 'summary', return_value=''):
            self.app = ui.DownloaderUI(self.root, offer_resume=False, prefs=settings.defaults())
        self.addCleanup(lambda: __import__('logging').getLogger().removeHandler(self.app._log_handler))

    def test_panel_and_values(self):
        app = self.app
        self.assertFalse(app.notes_panel.grid_info())
        app.toggle_notes()
        self.assertTrue(app.notes_panel.grid_info())
        self.assertEqual('Hide notes', app.notes_link.cget('text'))
        app.note_vars['topic'].set(' Rally ')
        app.note_vars['where'].set('')
        self.assertEqual({'topic': 'Rally'}, app.current_notes())

    def test_link_note_before_the_run(self):
        app = self.app
        app.table.reset([(A, 'Twitter')])
        row = app.table.rows[A]
        with mock.patch.object(self.ui.simpledialog, 'askstring', return_value='From the bridge'):
            app.add_note(row)
        self.assertEqual({A: 'From the bridge'}, app._pending_notes)


if __name__ == '__main__':
    unittest.main()
