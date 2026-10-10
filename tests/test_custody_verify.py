import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import cli, custody, verify
from multidownloader.runner import Job, Runner
from multidownloader.runs import Run


def clock():
    return '2026-10-09T05:40:02Z'


class FakeDownloader:
    def download(self, source, url, options, out_dir=None):
        name = url.rsplit('/', 1)[-1]
        (Path(out_dir) / 'x').mkdir(exist_ok=True)
        (Path(out_dir) / 'x' / f'2026-10-09_{name}.jpg').write_bytes(b'photo ' + name.encode())
        return True


def rewrite(log: Path, change) -> None:
    """What someone who edits the file would do: change a line and recompute the whole chain."""
    entries = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]
    log.unlink()
    fresh = custody.CustodyLog(log, entries[0]['run_id'], clock=clock)
    for entry in entries:
        change(entry)
        fresh._write(entry['event'], entry['detail'], entry['note'])


class ChainTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.log_path = self.tmp / 'custody.log'
        self.anchors = self.tmp / 'data' / 'anchors.log'

    def log(self, **kw):
        return custody.CustodyLog(self.log_path, 'r1', clock=clock, **kw)

    def test_chain_links_every_line(self):
        log = self.log()
        log.append('run_started')
        log.append('hashed', {'path': 'x/a.jpg', 'sha256': 'ab' * 32})
        log.append('run_finished')
        chain = custody.read_chain(self.log_path)
        self.assertIsNone(chain.broken_at)
        self.assertEqual([1, 2, 3], [e['seq'] for e in chain.entries])
        self.assertEqual(custody.ZERO, chain.entries[0]['prev'])
        lines = self.log_path.read_text(encoding='utf-8').splitlines()
        self.assertEqual('sha256:' + hashlib.sha256(lines[0].encode()).hexdigest(), chain.entries[1]['prev'])
        self.assertEqual(log.head, chain.heads[3])
        with self.assertRaises(ValueError):
            log.append('deleted')  # only the brief's events

    def test_one_edited_line_breaks_the_chain(self):
        log = self.log()
        for i in range(4):
            log.append('hashed', {'path': f'x/{i}.jpg', 'sha256': f'{i:064x}'})
        text = self.log_path.read_text(encoding='utf-8').replace('x/2.jpg', 'x/9.jpg')
        self.log_path.write_text(text, encoding='utf-8', newline='\n')
        self.assertEqual(3, custody.read_chain(self.log_path).broken_at)
        report = custody.check(self.log_path)
        self.assertFalse(report.ok)
        self.assertEqual('The custody log was changed: line 3 does not match the line before it.', report.message)

    def test_without_an_anchor_a_rewrite_cannot_show(self):
        log = self.log()
        log.append('run_started')
        report = custody.check(self.log_path)
        self.assertTrue(report.ok)
        self.assertIn('has no anchor', report.message)

    def test_local_anchor_and_a_rewritten_log(self):
        log = self.log(anchor_path=self.anchors)
        log.append('run_started')
        log.append('hashed', {'path': 'x/a.jpg', 'sha256': 'ab' * 32})
        record = log.anchor_local()
        self.assertEqual({'run_id': 'r1', 'seq': 2, 'head': record['head'], 'anchored_at': clock(),
                          'methods': ['local'], 'ots_file': None, 'tsr_file': None, 'git_commit': None},
                         json.loads(self.anchors.read_text(encoding='utf-8')))
        self.assertEqual('anchored', custody.read_chain(self.log_path).entries[-1]['event'])
        report = custody.check(self.log_path, anchor_path=self.anchors)
        self.assertTrue(report.ok)
        self.assertIn('only anchored on this computer', report.message)

        def swap(entry):
            if entry['event'] == 'hashed':
                entry['detail']['sha256'] = 'cd' * 32
        rewrite(self.log_path, swap)
        self.assertIsNone(custody.read_chain(self.log_path).broken_at)  # the chain alone looks fine
        report = custody.check(self.log_path, anchor_path=self.anchors)
        self.assertFalse(report.ok)
        self.assertEqual('Changed after 9 Oct 2026 05:40 UTC: line 2 does not match the anchored record.',
                         report.message)

    def test_package_anchor(self):
        log = self.log()
        log.append('run_started')
        log.append('exported')
        anchor = {'seq': 2, 'head': log.head, 'at': '2026-10-09T06:00:00Z'}
        self.assertEqual('Unchanged since the package was exported on 9 Oct 2026 06:00 UTC.',
                         custody.check(self.log_path, package_anchor=anchor).message)
        anchor['head'] = 'sha256:' + '1' * 64
        self.assertFalse(custody.check(self.log_path, package_anchor=anchor).ok)

    def test_long_runs_anchor_every_100_events(self):
        with mock.patch.object(custody, 'ANCHOR_EVERY_EVENTS', 5):
            log = self.log(anchor_path=self.anchors)
            for i in range(11):
                log.append('hashed', {'path': f'{i}', 'sha256': '0' * 64})
        self.assertEqual(2, len(self.anchors.read_text(encoding='utf-8').splitlines()))
        self.assertTrue(custody.check(self.log_path, anchor_path=self.anchors).ok)

    def test_continues_an_existing_log(self):
        self.log().append('run_started')
        log = self.log()
        log.append('run_finished')
        self.assertEqual(2, log.seq)
        self.assertIsNone(custody.read_chain(self.log_path).broken_at)


class RunVerifyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.anchors = self.tmp / 'userdata' / 'anchors.log'
        run = Run(self.tmp / 'out', anchor_path=self.anchors)
        try:
            Runner(run, FakeDownloader(), delays={}, default_delay=(0, 0)).run(
                [Job('https://x.com/a/status/1', 'Twitter'), Job('https://x.com/a/status/2', 'Twitter')])
        finally:
            run.finish()
        self.run_dir = run.dir
        self.root = run.root

    def test_run_writes_the_custody_events_and_anchors_outside(self):
        events = [e['event'] for e in custody.read_chain(self.run_dir / 'custody.log').entries]
        self.assertEqual(['run_started', 'captured', 'hashed', 'captured', 'hashed', 'run_finished', 'anchored'],
                         events)
        self.assertTrue(self.anchors.exists())
        self.assertFalse(str(self.anchors).startswith(str(self.root)))

    def test_valid_run(self):
        report = verify.verify(self.run_dir, check_custody=True, anchor_path=self.anchors)
        self.assertTrue(report.ok, report.lines())
        self.assertEqual('All 2 files match.', report.summary())
        self.assertIn('only anchored on this computer', report.custody.message)

    def test_one_byte_change_and_a_missing_file(self):
        files = sorted((self.root / 'x').iterdir())
        data = bytearray(files[0].read_bytes())
        data[0] ^= 1
        files[0].write_bytes(bytes(data))
        files[1].unlink()
        report = verify.verify(self.run_dir)
        self.assertFalse(report.ok)
        self.assertEqual([f'x/{files[0].name}'], report.changed)
        self.assertEqual([f'x/{files[1].name}'], report.missing)
        self.assertEqual('Problems found in 2 files: 1 changed, 1 missing.', report.summary())

    def test_file_and_its_checksum_edited_together(self):
        target = sorted((self.root / 'x').iterdir())[0]
        target.write_bytes(b'something else')
        new = hashlib.sha256(b'something else').hexdigest()
        rel = f'x/{target.name}'
        old = verify.parse_sums((self.run_dir / 'SHA256SUMS.txt').read_text(encoding='utf-8'))[rel]
        for name in ('SHA256SUMS.txt', 'manifest.jsonl'):
            path = self.run_dir / name
            path.write_text(path.read_text(encoding='utf-8').replace(old, new), encoding='utf-8', newline='\n')
        self.assertTrue(verify.verify(self.run_dir).files_ok)  # the checksums alone now agree
        report = verify.verify(self.run_dir, check_custody=True, anchor_path=self.anchors)
        self.assertFalse(report.ok)
        self.assertIn(f'{rel} is listed with a different SHA-256 than the custody log recorded.', report.problems)

    def test_record_adds_a_verified_line(self):
        verify.verify(self.run_dir, record=True)
        chain = custody.read_chain(self.run_dir / 'custody.log')
        self.assertIsNone(chain.broken_at)
        self.assertEqual(('verified', 'valid'), (chain.entries[-1]['event'], chain.entries[-1]['detail']['result']))

    def test_a_damaged_log_is_reported_not_added_to(self):
        log = self.run_dir / 'custody.log'
        log.write_text(log.read_text(encoding='utf-8').replace('run_finished', 'run_finishee'), encoding='utf-8',
                       newline='\n')
        before = log.read_bytes()
        report = verify.verify(self.run_dir, record=True)
        self.assertEqual(before, log.read_bytes())
        self.assertFalse(report.ok)
        self.assertIn('The custody log was changed', report.custody.message)

    def test_cli(self):
        with mock.patch.object(custody, 'default_anchor_path', return_value=self.anchors), \
                mock.patch('builtins.print') as out:
            self.assertEqual(0, cli.main(['verify', str(self.run_dir), '--custody', '--no-record']))
            self.assertEqual('All 2 files match.', out.call_args_list[0].args[0])
            next(iter((self.root / 'x').iterdir())).write_bytes(b'changed')
            self.assertEqual(1, cli.main(['verify', str(self.run_dir), '--no-record']))
            self.assertEqual(2, cli.main(['verify', str(self.tmp / 'nothing')]))
            self.assertEqual(2, cli.main(['verify', str(self.tmp)]))


def make_bag(bag: Path, files, info_extra=''):
    """A minimal BagIt bag (RFC 8493), written by hand so the test does not depend on the exporter."""
    data = bag / 'data'
    for rel, content in files.items():
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_bytes(content)
    (bag / 'bagit.txt').write_text('BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n', encoding='utf-8')
    for alg in ('sha512', 'sha256'):
        lines = [f'{hashlib.new(alg, c).hexdigest()}  data/{r}\n' for r, c in sorted(files.items())]
        (bag / f'manifest-{alg}.txt').write_text(''.join(lines), encoding='utf-8')
    size = sum(len(c) for c in files.values())
    (bag / 'bag-info.txt').write_text(f'Bagging-Date: 2026-10-09\nPayload-Oxum: {size}.{len(files)}\n' + info_extra,
                                      encoding='utf-8')
    tags = ['bagit.txt', 'bag-info.txt', 'manifest-sha512.txt', 'manifest-sha256.txt']
    (bag / 'tagmanifest-sha512.txt').write_text(''.join(
        f'{hashlib.sha512((bag / t).read_bytes()).hexdigest()}  {t}\n' for t in tags), encoding='utf-8')


class PackageVerifyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bag = Path(tmp.name) / 'bag'
        make_bag(self.bag, {'x/a/1.jpg': b'one', 'x/a/2.jpg': b'two', '_runs/r1/manifest.jsonl': b'{}\n'})

    def test_valid_and_never_written(self):
        before = {p: p.read_bytes() for p in self.bag.rglob('*') if p.is_file()}
        report = verify.verify(self.bag, record=True)
        self.assertTrue(report.ok, report.lines())
        self.assertEqual('package', report.kind)
        self.assertEqual(3, report.checked)
        self.assertEqual(before, {p: p.read_bytes() for p in self.bag.rglob('*') if p.is_file()})

    def test_one_byte_change_in_any_file(self):
        for path in [p for p in self.bag.rglob('*') if p.is_file()]:
            with self.subTest(file=path.name):
                original = path.read_bytes()
                changed = bytearray(original)
                changed[-1 if changed else 0] ^= 1
                path.write_bytes(bytes(changed))
                self.assertFalse(verify.verify(self.bag).ok)
                path.write_bytes(original)
        self.assertTrue(verify.verify(self.bag).ok)

    def test_extra_and_missing(self):
        (self.bag / 'data' / 'x' / 'new.jpg').write_bytes(b'new')
        (self.bag / 'data' / 'x' / 'a' / '1.jpg').unlink()
        report = verify.verify(self.bag)
        self.assertEqual(['data/x/new.jpg'], report.extra)
        self.assertEqual(['data/x/a/1.jpg'], report.missing)

    def test_not_a_run_or_package(self):
        with self.assertRaises(ValueError):
            verify.verify(self.bag / 'data')


if __name__ == '__main__':
    unittest.main()
