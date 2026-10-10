import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import core, errors, session_store, updates
from multidownloader.backends import gallerydl
from multidownloader.core import Downloader

URL = 'https://www.instagram.com/reels/DeUOco6xUbG/'
GRAPHQL = 'Failed to download Instagram post DeUOco6xUbG: JSON Query to graphql/query: Expecting value: line 1 column 1 (char 0)'


class InstagramFallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', root / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.stage = root / 'out' / '_staging' / 'run_0001'
        self.stage.mkdir(parents=True)
        self.downloader = Downloader(str(root / 'out'))
        self.downloader._refreshed.update(self.downloader.handlers)  # no browser refresh in tests
        self.instaloader = mock.Mock(available=mock.Mock(return_value=(True, '')))
        self.downloader.handlers['Instagram'] = self.instaloader
        self.ytdlp = mock.Mock(name='yt-dlp handler')
        self.ytdlp.name = 'yt-dlp'
        self.downloader._ytdlp_instagram = self.ytdlp

    def fail(self, message):
        def broken(url, out_dir, options):
            (Path(out_dir) / 'half.jpg').write_bytes(b'partial')  # what Instaloader may leave behind
            raise RuntimeError(message)
        self.instaloader.download.side_effect = broken

    def download(self, options=None):
        return self.downloader.download('Instagram', URL, options or {'auth': 'auto'}, out_dir=str(self.stage))

    def test_api_change_falls_back_to_ytdlp_with_the_same_sign_in(self):
        self.fail(GRAPHQL)
        self.ytdlp.download.return_value = {'streams': [{'id': 'DeUOco6xUbG', 'merged': False, 'formats': ['1']}]}
        outcome = self.download()
        self.assertEqual('yt-dlp', outcome['backend'])
        self.assertEqual(['1'], outcome['streams'][0]['formats'])
        options = self.ytdlp.download.call_args.args[2]
        self.assertTrue(options['use_session'])
        self.assertNotIn('auth', options)
        self.assertEqual([], list(self.stage.iterdir()))  # Instaloader's half-saved file was cleared first

    def test_sign_in_and_post_problems_are_not_retried(self):
        for message in ('This post needs you to be signed in to Instagram.', 'Instagram did not accept your sign-in.',
                        'Post not found', '429 Too Many Requests', 'Instagram wants you to confirm this sign-in.'):
            with self.subTest(message=message):
                self.ytdlp.reset_mock()
                self.fail(message)
                with self.assertRaises(RuntimeError):
                    self.download()
                self.ytdlp.download.assert_not_called()

    def test_both_fail_reports_the_first_error(self):
        self.fail(GRAPHQL)
        self.ytdlp.download.side_effect = RuntimeError('yt-dlp failed for Instagram: ERROR: unable to extract shared data')
        with self.assertRaises(RuntimeError) as ctx:
            self.download()
        self.assertIn('graphql', str(ctx.exception))
        self.assertEqual(errors.OUTDATED, errors.classify(ctx.exception, platform='Instagram').kind)

    def test_photo_post_goes_on_to_gallery_dl(self):
        self.fail(GRAPHQL)
        self.ytdlp.download.side_effect = RuntimeError('ERROR: [Instagram] x: There is no video in this post')
        self.assertEqual(errors.NO_MEDIA, errors.classify(self.ytdlp.download.side_effect).kind)
        with mock.patch.object(gallerydl, 'download', return_value=['a.jpg']) as gdl:
            outcome = self.download()
        self.assertEqual({'backend': 'gallery-dl', 'files': ['a.jpg']}, outcome)
        self.assertEqual('instagram', gdl.call_args.kwargs['platform_key'])

    def test_staging_clear_never_touches_other_folders(self):
        folder = self.stage.parent.parent / 'instagram'
        folder.mkdir()
        (folder / 'keep.jpg').write_bytes(b'keep')
        core._clear_staging(folder)
        self.assertTrue((folder / 'keep.jpg').exists())


class AccountFolderTests(unittest.TestCase):
    def test_instagram_folder_is_the_handle_with_either_program(self):
        from multidownloader.sources import yt_dlp_base
        template = yt_dlp_base.layout_template('instagram')
        info = {'uploader': 'SouthernAdventures', 'channel': 'southernadventures__', 'uploader_id': '66339014157',
                'id': 'DeUOco6xUbG', 'upload_date': '20261010', 'ext': 'mp4'}
        import yt_dlp
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            path = ydl.prepare_filename(info, outtmpl=template)
        self.assertEqual('instagram/southernadventures__/2026-10-10_DeUOco6xUbG.mp4', path.replace('\\', '/'))
        # other platforms keep the uploader first
        self.assertIn('%(uploader,channel', yt_dlp_base.layout_template('x'))


class ErrorTextTests(unittest.TestCase):
    def test_instagram_api_change_says_update(self):
        info = errors.classify(RuntimeError(GRAPHQL), platform='Instagram')
        self.assertEqual(errors.OUTDATED, info.kind)
        self.assertEqual('Instagram changed something and the downloader needs an update.', info.message)
        self.assertEqual(errors.HOW_TO_UPDATE, info.action)


class InstaloaderUpdateTests(unittest.TestCase):
    def test_newer_instaloader_is_announced(self):
        found = updates.notices({'instaloader': '4.15'}, {'instaloader': '4.15.3'}, today=dt.date(2026, 10, 10))
        self.assertEqual(['instaloader'], [n.package for n in found])
        self.assertEqual('Instaloader 4.15.3 is available (you have 4.15). Instagram downloads may need it.',
                         found[0].message)
        self.assertEqual([], updates.notices({'instaloader': '4.15.3'}, {'instaloader': '4.15.3'}))
        self.assertIn('instaloader', updates.PACKAGES)


if __name__ == '__main__':
    unittest.main()
