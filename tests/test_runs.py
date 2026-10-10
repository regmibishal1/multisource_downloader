import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import runs, session_store
from multidownloader.runs import Run


class FakeDownloader:
    """Writes the given files into out_dir, or raises, like a real handler."""

    def __init__(self, files=None, error=None):
        self.files = files or {}
        self.error = error
        self.calls = []

    def download(self, source, url, options, out_dir=None):
        self.calls.append((source, url, out_dir))
        for rel, data in self.files.items():
            target = Path(out_dir) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if self.error:
            raise self.error
        return True


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        # error messages depend on whether a sign-in exists: never read the user's real ones
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.root / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_saved_files_are_hashed_moved_and_recorded(self):
        files = {'youtube/chan/Rally [abc].mp4': b'video bytes', 'youtube/chan/Rally [abc].webp': b'thumb'}
        with Run(self.root) as run:
            result = run.download(FakeDownloader(files), 'YouTube', 'https://youtu.be/abc123?si=x')
        self.assertEqual('saved', result.status)
        self.assertEqual(['youtube/chan/Rally [abc].mp4', 'youtube/chan/Rally [abc].webp'],
                         [f.path for f in result.files])
        for rel, data in files.items():
            self.assertEqual(data, (self.root / rel).read_bytes())
        self.assertEqual(sha(b'video bytes'), result.files[0].sha256)
        self.assertEqual(len(b'video bytes'), result.files[0].bytes)
        self.assertEqual('youtube', result.platform)
        self.assertEqual('yt-dlp', result.backend)
        self.assertEqual('https://youtube.com/watch?v=abc123', result.normalized_url)
        self.assertTrue(result.started_at.endswith('Z') and result.finished_at.endswith('Z'))
        self.assertFalse(any((self.root / '_staging').iterdir()))  # staging removed

    def test_manifest_csv_and_sums(self):
        with Run(self.root) as run:
            run.download(FakeDownloader({'a/one.jpg': b'1'}), 'Twitter', 'https://x.com/a/status/1')
            run.download(FakeDownloader(error=RuntimeError('ERROR: login required')), 'Instagram',
                         'https://www.instagram.com/p/X/')
            run.skip('https://example.org/x', 'unsupported')
        lines = runs.read_manifest(run.dir / 'manifest.jsonl')
        self.assertEqual(['saved', 'failed', 'skipped'], [line['status'] for line in lines])
        self.assertEqual('auth_required', lines[1]['error_kind'])
        self.assertEqual('ERROR: login required', lines[1]['error_detail'])
        self.assertEqual('unsupported', lines[2]['skip_reason'])
        self.assertTrue(all(line['run_id'] == run.run_id for line in lines))
        self.assertTrue(lines[0]['app_version'].startswith('multisource-downloader '))

        with open(run.dir / 'manifest.csv', encoding='utf-8-sig', newline='') as fp:
            rows = list(csv.DictReader(fp))
        self.assertEqual(3, len(rows))
        self.assertEqual('a/one.jpg', rows[0]['files'])
        self.assertEqual(sha(b'1'), rows[0]['sha256'])
        self.assertEqual('unsupported', rows[2]['error_message'])

        sums = (run.dir / 'SHA256SUMS.txt').read_text(encoding='utf-8')
        self.assertEqual(f'{sha(b"1")}  a/one.jpg\n', sums)
        self.assertEqual([], list(runs.verify_sums(self.root, run.dir / 'SHA256SUMS.txt')))
        (self.root / 'a/one.jpg').write_bytes(b'changed')
        self.assertEqual(['changed: a/one.jpg'], list(runs.verify_sums(self.root, run.dir / 'SHA256SUMS.txt')))

    @unittest.skipUnless(shutil.which('sha256sum'), 'sha256sum not installed')
    def test_sums_file_works_with_sha256sum(self):
        with Run(self.root) as run:
            run.download(FakeDownloader({'d/नेपाल.jpg': b'x', 'd/b c.jpg': b'y'}), 'Twitter',
                         'https://x.com/a/status/1')
        rel = (run.dir / 'SHA256SUMS.txt').relative_to(self.root).as_posix()
        proc = subprocess.run(['sha256sum', '-c', rel], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_manifest_line_is_on_disk_before_the_run_ends(self):
        run = Run(self.root)
        run.download(FakeDownloader({'f.jpg': b'1'}), 'Twitter', 'https://x.com/a/status/1')
        # read while the run is still open, as after a crash
        self.assertEqual(1, len(runs.read_manifest(run.dir / 'manifest.jsonl')))
        run.finish()

    def test_cut_short_last_line_is_ignored(self):
        path = self.root / 'manifest.jsonl'
        path.write_text(json.dumps({'status': 'saved'}) + '\n{"status": "sa', encoding='utf-8')
        self.assertEqual([{'status': 'saved'}], runs.read_manifest(path))

    def test_failed_download_leaves_nothing_behind(self):
        with Run(self.root) as run:
            result = run.download(FakeDownloader({'x/partial.mp4.part': b'half'}, error=RuntimeError('timed out')),
                                  'YouTube', 'https://youtu.be/abc123')
        self.assertEqual(('failed', 'network'), (result.status, result.error_kind))
        self.assertFalse((self.root / 'x').exists())
        self.assertEqual([], list((self.root / '_staging').iterdir()))

    def test_no_file_is_a_failure(self):
        with Run(self.root) as run:
            result = run.download(FakeDownloader({'v.mp4.part': b'half'}), 'YouTube', 'https://youtu.be/abc123')
        self.assertEqual(('failed', 'no_media'), (result.status, result.error_kind))

    def test_never_overwrites_a_different_file(self):
        (self.root / 'x').mkdir()
        (self.root / 'x' / 'photo.jpg').write_bytes(b'older, different photo')
        with Run(self.root) as run:
            result = run.download(FakeDownloader({'x/photo.jpg': b'new photo'}), 'Twitter', 'https://x.com/a/status/1')
        self.assertEqual(b'older, different photo', (self.root / 'x' / 'photo.jpg').read_bytes())
        self.assertEqual('x/photo (2).jpg', result.files[0].path)
        self.assertEqual(b'new photo', (self.root / 'x' / 'photo (2).jpg').read_bytes())

    def test_identical_file_is_not_duplicated(self):
        (self.root / 'x').mkdir()
        (self.root / 'x' / 'photo.jpg').write_bytes(b'same')
        with Run(self.root) as run:
            result = run.download(FakeDownloader({'x/photo.jpg': b'same'}), 'Twitter', 'https://x.com/a/status/1')
        self.assertEqual('x/photo.jpg', result.files[0].duplicate_of)
        self.assertEqual(['photo.jpg'], sorted(p.name for p in (self.root / 'x').iterdir()))
        self.assertEqual(1, (run.dir / 'SHA256SUMS.txt').read_text(encoding='utf-8').count('x/photo.jpg'))

    def test_old_staging_is_cleaned_new_is_kept(self):
        staging = self.root / '_staging'
        old, new = staging / 'old_0001', staging / 'new_0001'
        old.mkdir(parents=True)
        new.mkdir()
        past = time.time() - 2 * runs.STAGING_MAX_AGE_S
        os.utime(old, (past, past))
        self.assertEqual(1, runs.clean_old_staging(self.root))
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_each_job_gets_its_own_staging_folder(self):
        fake = FakeDownloader({'f.jpg': b'1'})
        with Run(self.root) as run:
            run.download(fake, 'Twitter', 'https://x.com/a/status/1')
            run.download(fake, 'Twitter', 'https://x.com/a/status/2')
        self.assertNotEqual(fake.calls[0][2], fake.calls[1][2])
        self.assertTrue(all(Path(c[2]).parent == self.root / '_staging' for c in fake.calls))

    def test_classify(self):
        cases = {
            "Sign in to confirm you're not a bot": 'bot_check',
            'HTTP Error 429: Too Many Requests': 'rate_limited',
            'This post needs you to be signed in to Instagram.': 'auth_required',
            'HTTP Error 404: Not Found': 'not_found',
            'Connection reset by peer': 'network',
            'something odd': 'unknown',
        }
        for message, kind in cases.items():
            with self.subTest(message=message):
                self.assertEqual(kind, runs.classify(message))

    def test_summary_line(self):
        with Run(self.root) as run:
            run.download(FakeDownloader({'f.jpg': b'1'}), 'Twitter', 'https://x.com/a/status/1')
            run.download(FakeDownloader(error=RuntimeError('x')), 'Twitter', 'https://x.com/a/status/2')
            run.skip('https://example.org', 'unsupported')
        self.assertEqual('Done. 1 saved, 1 failed, 1 skipped.', run.summary_line())


class BatchManifestTests(unittest.TestCase):
    def test_batch_records_every_link(self):
        import logging

        from multidownloader.batch import ManifestItem, execute_batch
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        items = [
            ManifestItem('twitter', 'https://x.com/a/status/1'),
            ManifestItem('twitter', 'https://twitter.com/a/status/1'),  # same post
            ManifestItem('other', 'https://example.org/x'),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_batch(items, Path(tmp), downloader=FakeDownloader({'p.jpg': b'1'}))
            lines = runs.read_manifest(Path(result['run_dir']) / 'manifest.jsonl')
        by_url = {line['input_url']: line for line in lines}
        self.assertEqual('saved', by_url['https://x.com/a/status/1']['status'])
        self.assertEqual('duplicate', by_url['https://twitter.com/a/status/1']['skip_reason'])
        self.assertEqual('unsupported', by_url['https://example.org/x']['skip_reason'])

    def test_dry_run_writes_nothing(self):
        from multidownloader.batch import ManifestItem, execute_batch
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_batch([ManifestItem('x', 'https://x.com/a/status/1')], Path(tmp), dry_run=True,
                                   downloader=FakeDownloader())
            self.assertIsNone(result['run_dir'])
            self.assertFalse((Path(tmp) / '_runs').exists())


if __name__ == '__main__':
    unittest.main()
