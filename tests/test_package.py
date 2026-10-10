import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from multidownloader import cli, custody, package, settings, verify
from multidownloader.runner import Job, Runner
from multidownloader.runs import Run

from gui_gate import HAVE_DISPLAY, REASON as GUI_REASON, hidden_root


try:
    import bagit  # bagit-python (Library of Congress), only to check other tools accept the package
except ImportError:
    bagit = None

NOW = datetime(2026, 10, 9, 6, 0, tzinfo=timezone.utc)


class FakeDownloader:
    def download(self, source, url, options, out_dir=None):
        name = url.rsplit('/', 1)[-1]
        folder = Path(out_dir) / 'x' / 'someone'
        folder.mkdir(parents=True)
        (folder / f'2026-10-09_{name}.jpg').write_bytes(b'photo ' + name.encode())
        (folder / f'2026-10-09_{name}.info.json').write_text(json.dumps({'id': name, 'uploader': 'someone'}),
                                                             encoding='utf-8')
        return True


class PackageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.anchors = self.tmp / 'userdata' / 'anchors.log'
        run = Run(self.tmp / 'downloads', anchor_path=self.anchors,
                  notes={'topic': 'Rally', 'where': 'Kathmandu', 'collected_by': 'R.'})
        try:
            Runner(run, FakeDownloader(), delays={}, default_delay=(0, 0)).run(
                [Job('https://x.com/someone/status/1', 'Twitter'), Job('https://x.com/someone/status/2', 'Twitter')])
        finally:
            run.finish()
        self.run_dir = run.dir
        self.out = self.tmp / 'packages'
        self.out.mkdir()

    def export(self, **kw):
        kw.setdefault('anchor_path', self.anchors)
        return package.export(self.run_dir, self.out, now=NOW, **kw)

    def test_layout_and_bag_info(self):
        result = self.export(source_organization='Example Archive')
        bag = result.path
        self.assertEqual(f'package-{self.run_dir.name}', bag.name)
        self.assertEqual('BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n',
                         (bag / 'bagit.txt').read_text(encoding='utf-8'))
        for name in ('manifest-sha512.txt', 'manifest-sha256.txt', 'tagmanifest-sha512.txt', 'tagmanifest-sha256.txt'):
            self.assertTrue((bag / name).is_file(), name)
        data = sorted(p.relative_to(bag / 'data').as_posix() for p in (bag / 'data').rglob('*') if p.is_file())
        run_rel = f'_runs/{self.run_dir.name}'
        for expected in ('x/someone/2026-10-09_1.jpg', 'x/someone/2026-10-09_2.info.json', f'{run_rel}/manifest.jsonl',
                         f'{run_rel}/SHA256SUMS.txt', f'{run_rel}/custody.log', f'{run_rel}/notes.json'):
            self.assertIn(expected, data)
        info = verify.read_bag_info(bag)
        self.assertEqual(['Example Archive'], info['Source-Organization'])
        self.assertEqual(['2026-10-09'], info['Bagging-Date'])
        self.assertEqual([self.run_dir.name], info['External-Identifier'])
        self.assertEqual(['Topic: Rally. Where: Kathmandu. Collected by: R.'], info['External-Description'])
        self.assertTrue(info['Bag-Software-Agent'][0].startswith('multisource-downloader '))
        size = sum(p.stat().st_size for p in (bag / 'data').rglob('*') if p.is_file())
        self.assertEqual([f'{size}.{len(data)}'], info['Payload-Oxum'])
        # A2: the package records the head of its own copy of the custody log, after the "exported" line
        copy = custody.read_chain(bag / 'data' / run_rel / 'custody.log')
        self.assertEqual('exported', copy.entries[-1]['event'])
        self.assertEqual([copy.heads[copy.entries[-1]['seq']]], info['Custody-Log-Head'])
        self.assertEqual([str(copy.entries[-1]['seq'])], info['Custody-Log-Seq'])
        self.assertEqual('Package created and verified: 9 files in ' + str(bag) + '.', result.message())

    def test_package_verifies_and_survives_later_local_anchors(self):
        bag = self.export().path
        # the run's own log moved on after the export (anchored again); the package's copy did not
        self.assertEqual('anchored', custody.read_chain(self.run_dir / 'custody.log').entries[-1]['event'])
        report = verify.verify(bag, check_custody=True, anchor_path=self.anchors)
        self.assertTrue(report.ok, report.lines())
        self.assertEqual('Unchanged since the package was exported on 9 Oct 2026 06:00 UTC.', report.custody.message)
        self.assertTrue(verify.verify(self.run_dir, check_custody=True, anchor_path=self.anchors).ok)

    def test_done_when_one_byte_change_in_any_file_of_a_copied_package(self):
        import shutil
        copied = self.tmp / 'usb' / 'package'
        shutil.copytree(self.export().path, copied)
        self.assertTrue(verify.verify(copied).ok)
        files = [p for p in copied.rglob('*') if p.is_file()]
        self.assertGreater(len(files), 10)
        for path in files:
            with self.subTest(file=path.relative_to(copied).as_posix()):
                original = path.read_bytes()
                changed = bytearray(original)
                changed[len(changed) // 2] ^= 1
                path.write_bytes(bytes(changed))
                self.assertFalse(verify.verify(copied).ok)
                path.write_bytes(original)
        self.assertTrue(verify.verify(copied).ok)

    def test_done_when_a_rewritten_log_is_reported_after_its_anchor(self):
        bag = self.export().path
        log = next((bag / 'data').rglob('custody.log'))
        entries = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
        log.unlink()
        fresh = custody.CustodyLog(log, entries[0]['run_id'], clock=lambda: '2026-10-09T05:40:02Z')
        for entry in entries:
            if entry['event'] == 'hashed':
                entry['detail']['sha256'] = '0' * 64
            fresh._write(entry['event'], entry['detail'], entry['note'])
        report = verify.verify(bag, check_custody=True)
        self.assertFalse(report.ok)
        self.assertTrue(report.custody.message.startswith('Changed after 9 Oct 2026 06:00 UTC: line '),
                        report.custody.message)

    def test_refusals_leave_nothing_behind(self):
        self.export()
        with self.assertRaisesRegex(package.PackageError, 'already exists'):
            self.export()
        with self.assertRaisesRegex(package.PackageError, 'outside the download folder'):
            package.export(self.run_dir, self.run_dir.parent.parent / 'pkg', now=NOW)
        other = self.tmp / 'other'
        other.mkdir()
        next((self.run_dir.parent.parent / 'x' / 'someone').glob('*.jpg')).write_bytes(b'edited')
        with self.assertRaisesRegex(package.PackageError, 'has changed since it was saved'):
            package.export(self.run_dir, other, now=NOW)
        self.assertEqual([], list(other.iterdir()))

    def test_unfinished_run(self):
        run = Run(self.tmp / 'downloads2')
        try:
            with self.assertRaisesRegex(package.PackageError, 'not finished'):
                package.export(run.dir, self.out)
        finally:
            run.finish()

    def test_zip(self):
        result = self.export(make_zip=True)
        with zipfile.ZipFile(result.zip_path) as archive:
            self.assertIn(f'{result.path.name}/bagit.txt', archive.namelist())

    @unittest.skipIf(bagit is None, 'bagit-python is not installed')
    def test_other_bagit_tools_accept_it(self):
        bag = bagit.Bag(str(self.export().path))
        bag.validate()
        self.assertTrue(bag.is_valid())

    def test_cli(self):
        with mock.patch.object(custody, 'default_anchor_path', return_value=self.anchors), \
                mock.patch('builtins.print') as out:
            self.assertEqual(0, cli.main(['export', str(self.run_dir), '--bag', str(self.out), '--zip']))
            self.assertTrue(out.call_args.args[0].startswith('Package created and verified: 9 files in '))
            self.assertEqual(1, cli.main(['export', str(self.run_dir), '--bag', str(self.out)]))  # exists now
            bag = self.out / f'package-{self.run_dir.name}'
            self.assertEqual(0, cli.main(['verify', str(bag), '--custody']))

    def test_organization_setting(self):
        self.assertEqual('', settings.defaults()['source_organization'])
        self.assertTrue(settings.valid('source_organization', 'Example Archive'))
        self.assertFalse(settings.valid('source_organization', 'two\nlines'))


@unittest.skipUnless(HAVE_DISPLAY, GUI_REASON)
class ArchiveButtonsTests(unittest.TestCase):
    """Hidden root window only: nothing appears or takes the focus."""

    def setUp(self):
        self.root = hidden_root(self)
        import multisource_downloader_ui as ui
        self.ui = ui
        with mock.patch.object(ui.signins, 'summary', return_value=''):
            self.app = ui.DownloaderUI(self.root, offer_resume=False, prefs=settings.defaults())
        self.addCleanup(lambda: __import__('logging').getLogger().removeHandler(self.app._log_handler))

    def test_buttons_follow_the_run_and_results_are_shown(self):
        app = self.app
        self.assertIn('disabled', app.package_btn.state())
        report = verify.VerifyReport('run', checked=3)
        with mock.patch.object(self.ui.messagebox, 'showinfo') as info:
            app._archive_done('verified', report, None)
        self.assertEqual('All 3 files match.', app.status_var.get())
        info.assert_called_once()
        with mock.patch.object(self.ui.messagebox, 'showerror') as error:
            app._archive_done('packaged', None, package.PackageError('Choose another folder.'))
        self.assertEqual('Export package: Choose another folder.', app.status_var.get())
        error.assert_called_once()
        self.assertFalse(app._archive_busy)


if __name__ == '__main__':
    unittest.main()
