import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import session_store
from multidownloader.paths import MAX_NAME_LENGTH, path_inside, safe_filename
from multidownloader.sources import gdrive, instagram
from multidownloader.sources.gdrive import GoogleDriveHandler, parse_drive_id
from multidownloader.sources.instagram import InstagramHandler, parse_shortcode
from multidownloader.sources.yt_dlp_base import YtDlpHandler


class TempSessionRoot(unittest.TestCase):
    """Point session_store at a temp folder so tests never touch real sessions."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', self.tmp / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)


class InstagramShortcodeTests(unittest.TestCase):
    def test_link_shapes(self):
        cases = {
            'https://www.instagram.com/p/AbC_12-3/': 'AbC_12-3',
            'https://www.instagram.com/reel/AbC123/?igsh=xyz': 'AbC123',
            'https://www.instagram.com/reels/AbC123/': 'AbC123',
            'https://www.instagram.com/tv/AbC123': 'AbC123',
            'https://www.instagram.com/some.user_1/p/AbC123/': 'AbC123',
            'https://www.instagram.com/some.user/reel/AbC123/': 'AbC123',
            'https://instagram.com/p/AbC123': 'AbC123',
            'https://www.instagram.com/some.user/': None,
            'https://www.instagram.com/stories/user/123/': None,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(expected, parse_shortcode(url))


class InstagramDownloadTests(TempSessionRoot):
    def test_download_does_not_change_working_directory(self):
        # B5: the handler must write into out_dir without os.chdir.
        seen = {}

        class FakeLoader:
            dirname_pattern = '{target}'
            context = object()

            def download_post(self, post, target):
                seen['cwd'] = os.getcwd()
                seen['pattern'] = self.dirname_pattern
                seen['target'] = target

        fake_post = mock.Mock()
        fake_post.from_shortcode.return_value = 'post'
        loader = FakeLoader()
        out_dir = str(self.tmp / 'out {weird}')
        cwd = os.getcwd()
        with mock.patch.object(instagram, 'INSTALOADER_AVAILABLE', True), \
                mock.patch.object(instagram, 'Post', fake_post):
            InstagramHandler().download(
                'https://www.instagram.com/user/p/AbC123/', out_dir, {'instaloader_client': loader}
            )
        self.assertEqual(cwd, seen['cwd'])
        self.assertEqual('AbC123', seen['target'])
        self.assertEqual(os.path.join(out_dir, 'AbC123'), seen['pattern'].format(target='AbC123'))
        self.assertEqual('{target}', loader.dirname_pattern)


class DriveIdTests(unittest.TestCase):
    def test_link_shapes(self):
        cases = {
            'https://drive.google.com/file/d/FILE1/view?usp=sharing': ('FILE1', 'file'),
            'https://docs.google.com/document/d/DOC1/edit': ('DOC1', 'file'),
            'https://drive.google.com/uc?id=FILE2&export=download': ('FILE2', 'file'),
            'https://drive.google.com/uc?export=download&id=FILE3': ('FILE3', 'file'),
            'https://drive.google.com/drive/folders/FOLDER1': ('FOLDER1', 'folder'),
            'https://drive.google.com/drive/u/0/folders/FOLDER2?usp=sharing': ('FOLDER2', 'folder'),
            'https://drive.google.com/drive/folders/FOLDER3?id=zzz': ('FOLDER3', 'folder'),
            'https://drive.google.com/folderview?id=FOLDER4': ('FOLDER4', 'folder'),
            'https://drive.google.com/open?id=EITHER1': ('EITHER1', 'unknown'),
            'https://drive.google.com/': (None, None),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(expected, parse_drive_id(url))


class DriveDownloadTests(TempSessionRoot):
    def test_open_id_falls_back_to_folder(self):
        # B18: open?id= may be a folder; try the file path, then the folder path.
        handler = GoogleDriveHandler()
        with mock.patch.object(handler, '_download_public_file', side_effect=RuntimeError('not a file')) as file_dl, \
                mock.patch.object(handler, '_download_folder', return_value=True) as folder_dl:
            self.assertTrue(handler.download('https://drive.google.com/open?id=X1', str(self.tmp), {}))
        file_dl.assert_called_once()
        folder_dl.assert_called_once_with('X1', str(self.tmp))

    def test_gdown_returning_none_is_a_failure(self):
        fake_gdown = mock.Mock()
        fake_gdown.download.return_value = None
        handler = GoogleDriveHandler()
        with mock.patch.object(gdrive, 'GDOWN_AVAILABLE', True), mock.patch.object(gdrive, 'gdown', fake_gdown):
            with self.assertRaises(RuntimeError):
                handler.download('https://drive.google.com/file/d/F1/view', str(self.tmp), {})

    def test_authenticated_title_cannot_escape_output_folder(self):
        # B12: a Drive title such as "../../x" must stay inside out_dir.
        out_dir = self.tmp / 'out'
        out_dir.mkdir()
        written = []
        file_obj = mock.MagicMock()
        file_obj.get.side_effect = lambda key: '../../evil.txt' if key == 'title' else None
        file_obj.GetContentFile.side_effect = written.append
        drive = mock.Mock()
        drive.CreateFile.return_value = file_obj
        handler = GoogleDriveHandler()
        with mock.patch.object(gdrive, 'PYDRIVE2_AVAILABLE', True), \
                mock.patch.object(handler, '_get_drive_client', return_value=drive):
            handler.download('https://drive.google.com/file/d/F1/view', str(out_dir), {'method': 'authenticated'})
        self.assertEqual(1, len(written))
        self.assertEqual(out_dir.resolve(), Path(written[0]).parent)


class SafeFilenameTests(unittest.TestCase):
    def test_names(self):
        cases = {
            '../../x': '_.._x',
            'a/b\\c': 'a_b_c',
            '..': 'download',
            ' . ': 'download',
            '': 'download',
            'CON': '_CON',
            'nul.txt': '_nul.txt',
            'COM1.mp4': '_COM1.mp4',
            'console.txt': 'console.txt',
            'what?.mp4': 'what_.mp4',
            'trailing. ': 'trailing',
            'नेपाल.jpg': 'नेपाल.jpg',
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(expected, safe_filename(name))

    def test_long_name_keeps_extension(self):
        result = safe_filename('a' * 300 + '.mp4')
        self.assertEqual(MAX_NAME_LENGTH, len(result))
        self.assertTrue(result.endswith('.mp4'))

    def test_path_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self.assertEqual(root / '_.._x', path_inside(tmp, '../../x'))
            self.assertEqual(root / 'f1', path_inside(tmp, '', fallback='f1'))


class SessionStoreTests(TempSessionRoot):
    def test_write_json_is_atomic_and_round_trips(self):
        session_store.write_json('Instagram', 'meta.json', {'username': 'a'})
        self.assertEqual({'username': 'a'}, session_store.read_json('Instagram', 'meta.json'))
        folder = session_store.ensure_session_dir('Instagram')
        self.assertEqual(['meta.json'], sorted(p.name for p in folder.iterdir()))

    def test_failed_write_keeps_old_file(self):
        # B16: a crash mid-write must not leave a broken file behind.
        session_store.write_json('Instagram', 'meta.json', {'username': 'old'})
        with mock.patch.object(session_store.os, 'replace', side_effect=OSError('disk gone')):
            with self.assertRaises(OSError):
                session_store.write_json('Instagram', 'meta.json', {'username': 'new'})
        self.assertEqual({'username': 'old'}, session_store.read_json('Instagram', 'meta.json'))
        folder = session_store.ensure_session_dir('Instagram')
        self.assertEqual(['meta.json'], sorted(p.name for p in folder.iterdir()))

    def test_unreadable_json_is_backed_up(self):
        path = session_store.path_for('Instagram', 'meta.json')
        path.write_text('{"username": ', encoding='utf-8')
        with self.assertLogs('multidownloader.session_store', level='WARNING'):
            self.assertIsNone(session_store.read_json('Instagram', 'meta.json'))
        self.assertEqual('{"username": ', path.with_name('meta.json.bak').read_text(encoding='utf-8'))


class YtDlpCookieTests(TempSessionRoot):
    def test_no_cookiefile_until_one_is_imported(self):
        # B19: without an imported cookies.txt, yt-dlp must not get a cookie path
        # (it would save anonymous visitor cookies there).
        handler = YtDlpHandler('Twitter')
        self.assertIsNone(handler._resolve_cookie_path({'use_session': True}))
        self.assertNotIn('cookiefile', handler._build_opts(str(self.tmp), {}, None))

        cookie_path = session_store.default_cookie_path('Twitter')
        cookie_path.write_text('# Netscape HTTP Cookie File\n', encoding='utf-8')
        self.assertEqual(cookie_path, handler._resolve_cookie_path({'use_session': True}))
        self.assertIsNone(handler._resolve_cookie_path({'use_session': False}))

    def test_retry_settings(self):
        opts = YtDlpHandler('Twitter')._build_opts(str(self.tmp), {}, None)
        self.assertEqual(10, opts['retries'])
        self.assertEqual(10, opts['fragment_retries'])
        self.assertTrue(opts['continuedl'])


if __name__ == '__main__':
    unittest.main()
