import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import history, runs
from multidownloader.history import History
from multidownloader.runs import Run
from multidownloader.sources import yt_dlp_base

from test_runs import FakeDownloader


class PostIdTests(unittest.TestCase):
    def test_ids(self):
        cases = {
            'https://youtu.be/dQw4w9WgXcQ?si=1': 'dQw4w9WgXcQ',
            'https://www.youtube.com/shorts/dQw4w9WgXcQ': 'dQw4w9WgXcQ',
            'https://twitter.com/a/status/1890000000000000000?s=20': '1890000000000000000',
            'https://www.instagram.com/someone/reel/AbC123/': 'AbC123',
            'https://www.tiktok.com/@u/video/7300000000000000000?_r=1': '7300000000000000000',
            'https://old.reddit.com/r/nepal/comments/abc12/title/': 'abc12',
            'https://www.threads.net/@u/post/C1xYz': 'C1xYz',
            'https://drive.google.com/file/d/FILEID/view?usp=sharing': 'FILEID',
            'https://www.facebook.com/watch/?v=123&fbclid=x': '123',
            'https://www.facebook.com/someone/videos/456/': '456',
            'https://example.org/x': None,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(expected, history.post_id(url))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.history = History(self.tmp / 'history.sqlite')
        self.addCleanup(self.history.close)

    def test_find_by_url_or_post_id(self):
        self.history.add('https://twitter.com/a/status/1?s=20', saved_at='2026-10-01T05:00:00Z',
                         output_root=self.tmp, files=['x/1.jpg'])
        for url in ('https://x.com/a/status/1', 'https://x.com/renamed_user/status/1/photo/1'):
            with self.subTest(url=url):
                entry = self.history.find(url)
                self.assertIsNotNone(entry)
                self.assertEqual(['x/1.jpg'], entry.files)
                self.assertTrue(entry.describe().startswith('Saved on 2026-10-01 in'))
        self.assertIsNone(self.history.find('https://x.com/a/status/2'))

    def test_first_save_is_kept(self):
        self.history.add('https://x.com/a/status/1', saved_at='2026-10-01T00:00:00Z', output_root='A')
        self.history.add('https://x.com/a/status/1', saved_at='2026-10-05T00:00:00Z', output_root='B')
        self.assertEqual(('2026-10-01T00:00:00Z', 'A'),
                         (self.history.find('https://x.com/a/status/1').first_saved_at,
                          self.history.find('https://x.com/a/status/1').output_root))
        self.assertEqual(1, self.history.count())

    def test_user_data_dir(self):
        from multidownloader import appdirs
        with mock.patch.object(appdirs.sys, 'platform', 'win32'), \
                mock.patch.dict(appdirs.os.environ, {'LOCALAPPDATA': r'C:\Users\u\AppData\Local'}):
            self.assertEqual(Path(r'C:\Users\u\AppData\Local') / 'multisource-downloader', history.user_data_dir())
            self.assertEqual(Path(r'C:\Users\u\AppData\Local') / 'multisource-downloader' / 'Logs', appdirs.log_dir())
        with mock.patch.object(appdirs.sys, 'platform', 'linux'), \
                mock.patch.dict(appdirs.os.environ, {'XDG_DATA_HOME': '/home/u/.data', 'XDG_STATE_HOME': '/home/u/.st'}):
            self.assertEqual(Path('/home/u/.data/multisource-downloader'), history.user_data_dir())
            self.assertEqual(Path('/home/u/.st/multisource-downloader/log'), appdirs.log_dir())


class SkipDoneTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'out'
        self.history = History(Path(tmp.name) / 'history.sqlite')
        self.addCleanup(self.history.close)

    def test_second_run_downloads_nothing(self):
        fake = FakeDownloader({'x/1.jpg': b'1'})
        with Run(self.root, history=self.history) as first:
            first.download(fake, 'Twitter', 'https://twitter.com/a/status/1')
        with Run(self.root, history=self.history) as second:
            result = second.download(fake, 'Twitter', 'https://x.com/a/status/1?s=20')
        self.assertEqual(1, len(fake.calls))
        self.assertEqual('already_saved', result.status)
        self.assertEqual(['x/1.jpg'], result.previous['files'])
        self.assertTrue(result.previous['manifest_path'].endswith('manifest.jsonl'))
        self.assertEqual('Done. 0 saved, 1 already saved.', second.summary_line())
        line = runs.read_manifest(second.dir / 'manifest.jsonl')[0]
        self.assertEqual('already_saved', line['status'])

    def test_force_downloads_again(self):
        fake = FakeDownloader({'x/1.jpg': b'1'})
        with Run(self.root, history=self.history) as first:
            first.download(fake, 'Twitter', 'https://x.com/a/status/1')
        with Run(self.root, history=self.history, force=True) as again:
            result = again.download(fake, 'Twitter', 'https://x.com/a/status/1')
        self.assertEqual(2, len(fake.calls))
        self.assertEqual('saved', result.status)
        self.assertEqual('x/1.jpg', result.files[0].duplicate_of)  # same bytes: not duplicated on disk

    def test_failures_are_not_remembered(self):
        with Run(self.root, history=self.history) as run:
            run.download(FakeDownloader(error=RuntimeError('timed out')), 'Twitter', 'https://x.com/a/status/1')
        self.assertIsNone(self.history.find('https://x.com/a/status/1'))

    def test_batch_force_flag(self):
        from multidownloader.batch import build_arg_parser
        self.assertTrue(build_arg_parser().parse_args(['in.json', '--force']).force)


class PostDetailsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_ytdlp_info_json_fills_post(self):
        info = json.dumps({'id': 'abc', 'title': 'Rally at Maitighar', 'uploader': 'reporter', 'timestamp': 1760000000,
                           'extractor': 'youtube', 'webpage_url': 'https://youtube.com/watch?v=abc'})
        files = {'yt/Rally [abc].mp4': b'v', 'yt/Rally [abc].info.json': info.encode()}
        with Run(self.root) as run:
            result = run.download(FakeDownloader(files), 'YouTube', 'https://youtu.be/abc')
        self.assertEqual('yt/Rally [abc].info.json', result.metadata_path)
        self.assertEqual({'id': 'abc', 'uploader': 'reporter', 'posted_at': '2025-10-09T08:53:20Z',
                          'title': 'Rally at Maitighar', 'date_source': 'posted'}, result.post)

    def test_instaloader_json_fills_post(self):
        node = {'node': {'shortcode': 'AbC', 'owner': {'username': 'witness'}, 'taken_at_timestamp': 1760000000,
                         'edge_media_to_caption': {'edges': [{'node': {'text': 'काठमाडौं'}}]}}}
        files = {'AbC/2025-10-09_08-53-20_UTC.jpg': b'i', 'AbC/2025-10-09_08-53-20_UTC.json': json.dumps(node).encode()}
        with Run(self.root) as run:
            result = run.download(FakeDownloader(files), 'Instagram', 'https://www.instagram.com/p/AbC/')
        self.assertEqual(('AbC', 'witness', 'काठमाडौं'),
                         (result.post['id'], result.post['uploader'], result.post['title']))

    def test_drive_json_fills_post(self):
        details = {'id': 'F1', 'name': 'report.pdf', 'owners': ['Org'], 'modified': '2026-01-01T00:00:00Z'}
        files = {'report.pdf': b'%PDF', 'report.pdf.drive.json': json.dumps(details).encode()}
        with Run(self.root) as run:
            result = run.download(FakeDownloader(files), 'Google Drive', 'https://drive.google.com/file/d/F1/view')
        self.assertEqual({'id': 'F1', 'uploader': 'Org', 'posted_at': '2026-01-01T00:00:00Z', 'title': 'report.pdf',
                          'date_source': 'posted'},
                         result.post)


class MetadataOptionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        from multidownloader import session_store
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_info_json_and_thumbnail_on_by_default(self):
        opts = yt_dlp_base.YtDlpHandler('YouTube')._build_opts(str(self.tmp), {}, None)
        self.assertTrue(opts['writeinfojson'])
        self.assertTrue(opts['writethumbnail'])
        self.assertNotIn('embedthumbnail', opts)  # never alter the media itself
        off = yt_dlp_base.YtDlpHandler('YouTube')._build_opts(str(self.tmp), {'write_metadata': False}, None)
        self.assertNotIn('writeinfojson', off)

    def test_cookies_are_scrubbed_from_info_json(self):
        info = {'id': 'x', 'cookies': 'auth_token=SECRET', 'formats': [
            {'format_id': '1', 'cookies': 'auth_token=SECRET; ct0=SECRET',
             'http_headers': {'User-Agent': 'UA', 'Cookie': 'auth_token=SECRET'}}]}
        path = self.tmp / 'a' / 'v.info.json'
        path.parent.mkdir()
        path.write_text(json.dumps(info), encoding='utf-8')
        (self.tmp / 'clean.info.json').write_text(json.dumps({'id': 'y'}), encoding='utf-8')
        self.assertEqual(1, yt_dlp_base.scrub_info_json(self.tmp))
        text = path.read_text(encoding='utf-8')
        self.assertNotIn('SECRET', text)
        self.assertEqual('UA', json.loads(text)['formats'][0]['http_headers']['User-Agent'])



@unittest.skipUnless(__import__('importlib').util.find_spec('yt_dlp'), 'yt-dlp not installed')
class LayoutTemplateTests(unittest.TestCase):
    """The yt-dlp template, evaluated by yt-dlp itself (brief T10)."""

    def evaluate(self, template, **info):
        import yt_dlp
        with yt_dlp.YoutubeDL({'quiet': True, 'windowsfilenames': True}) as ydl:
            return ydl.evaluate_outtmpl(template, {'ext': 'mp4', 'epoch': 1760000000, **info})

    def test_layout(self):
        from multidownloader.sources.yt_dlp_base import layout_template
        t = layout_template('youtube')
        self.assertEqual('youtube/jawed/2005-04-24_jNQXAC9IVRw.mp4',
                         self.evaluate(t, id='jNQXAC9IVRw', uploader='jawed', upload_date='20050424'))
        # no upload date: the download date; no account: "unknown"; items of a post are numbered
        self.assertEqual('youtube/unknown/2025-10-09_abc_2.mp4', self.evaluate(t, id='abc', playlist_index=2))
        self.assertEqual('x/reporter/2025-10-09_1890.mp4',
                         self.evaluate(layout_template('x'), id='1890', uploader_id='reporter'))
        name = self.evaluate(layout_template('youtube'), id='abc', channel='नेपाल टिभी', upload_date='20251009')
        self.assertEqual('youtube/नेपाल टिभी/2025-10-09_abc.mp4', name)

    def test_title_only_when_asked_and_cut_to_60(self):
        from multidownloader.sources.yt_dlp_base import layout_template
        self.assertNotIn('title', layout_template('youtube'))
        name = self.evaluate(layout_template('youtube', include_title=True), id='abc', uploader='u',
                             upload_date='20251009', title='T' * 100)
        self.assertEqual('youtube/u/2025-10-09_abc_' + 'T' * 60 + '.mp4', name)

    def test_long_output_folder_keeps_files_inside_it(self):
        # regression: with the folder inside the template, trim_file_name cut
        # the whole path and files landed outside the output folder
        import yt_dlp

        from multidownloader.sources.yt_dlp_base import YtDlpHandler
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / ('very-long-folder-name-' * 10) / '_staging' / 'run_0001'
            self.assertGreater(len(str(out)), 200)
            with mock.patch.object(yt_dlp_base.shutil, 'which', return_value='ffmpeg'):
                opts = YtDlpHandler('YouTube')._build_opts(str(out), {}, None)
            info = {'id': 'jNQXAC9IVRw', 'uploader': 'jawed', 'upload_date': '20050424', 'ext': 'webm',
                    'title': 'Me at the zoo', 'epoch': 1760000000}
            with yt_dlp.YoutubeDL({**opts, 'quiet': True}) as ydl:
                # yt-dlp names the .info.json and thumbnail after this file
                name = Path(ydl.prepare_filename(info))
            self.assertEqual(out.resolve() / 'youtube' / 'jawed', name.resolve().parent)
            self.assertEqual('2005-04-24_jNQXAC9IVRw.webm', name.name)

    def test_handlers_use_the_layout(self):
        from multidownloader.sources.yt_dlp_base import YtDlpHandler
        for key, folder in (('YouTube', 'youtube'), ('Twitter', 'x'), ('TikTok', 'tiktok'), ('Reddit', 'reddit'),
                            ('Facebook', 'facebook'), ('Threads', 'threads')):
            with self.subTest(key=key):
                opts = YtDlpHandler(key)._build_opts('OUT', {}, None)
                self.assertEqual('OUT', opts['paths']['home'])
                self.assertTrue(opts['outtmpl'].startswith(f'{folder}/%(uploader'))
                self.assertTrue(opts['windowsfilenames'])
                self.assertEqual(120, opts['trim_file_name'])

if __name__ == '__main__':
    unittest.main()
