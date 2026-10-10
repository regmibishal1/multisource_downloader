import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from multidownloader import detect, errors, history, runs, signins
from multidownloader.backends import gallerydl
from multidownloader.core import THREADS_IMAGES, Downloader
from multidownloader.platforms import BY_KEY


class FakeProc:
    """Stands in for gallery-dl's process: writes files where the config says, then exits."""

    def __init__(self, args, returncode=0, files=(), stderr='', hang=False, **kwargs):
        self.args = args
        self.returncode = None
        self._code = returncode
        self._stderr = stderr
        self._hang = hang
        self.terminated = False
        config = json.loads(Path(args[args.index('--config-json') + 1]).read_text(encoding='utf-8'))
        self.config = config
        base = Path(config['extractor']['base-directory'])
        for rel in files:
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'x')
        cookies = config['extractor'].get('cookies')
        self.saw_cookies = Path(cookies).read_text(encoding='utf-8') if cookies else None

    def communicate(self, timeout=None):
        if self._hang and not self.terminated:
            raise subprocess.TimeoutExpired(self.args, timeout)
        self.returncode = -15 if self.terminated else self._code
        return '', self._stderr

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class BackendTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name) / 'stage'
        self.out.mkdir()
        self.made = []
        # the fake process stands in for gallery-dl, installed or not
        self.command = mock.patch.object(gallerydl, 'command', return_value=['gallery-dl'])
        self.command.start()
        self.addCleanup(self.command.stop)

    def runner(self, **kw):
        def make(args, **popen_kw):
            proc = FakeProc(args, **kw)
            self.made.append(proc)
            return proc
        return make

    def test_config_layout(self):
        config = gallerydl.build_config('OUT', 'x', cookies='c.txt', user_agent='UA')['extractor']
        self.assertEqual('OUT', config['base-directory'])
        self.assertEqual('x', config['directory'][0])
        self.assertTrue(config['filename'].endswith('_{num}.{extension}'))
        self.assertEqual('windows', config['path-restrict'])
        self.assertEqual(('c.txt', False, 'UA'), (config['cookies'], config['cookies-update'], config['user-agent']))
        self.assertEqual('metadata', config['postprocessors'][0]['name'])
        self.assertNotIn('cookies', gallerydl.build_config('OUT', 'x')['extractor'])

    @unittest.skipUnless(__import__('importlib').util.find_spec('gallery_dl'), 'gallery-dl not installed')
    def test_layout_fields_evaluate_with_gallery_dl(self):
        import datetime

        from gallery_dl import formatter
        config = gallerydl.build_config('OUT', 'x')['extractor']
        tweet = {'tweet_id': 1890, 'author': {'name': 'reporter'}, 'date': datetime.datetime(2025, 10, 9), 'num': 2,
                 'extension': 'jpg'}
        self.assertEqual('reporter', formatter.parse(config['directory'][1]).format_map(tweet))
        self.assertEqual('2025-10-09_1890_2.jpg', formatter.parse(config['filename']).format_map(tweet))
        reddit = {'id': 'abc12', 'author': 'someone', 'date': datetime.datetime(2025, 10, 9), 'num': 1,
                  'extension': 'png'}
        self.assertEqual('someone', formatter.parse(config['directory'][1]).format_map(reddit))

    def test_download_runs_a_separate_program_with_its_own_config(self):
        cookie_file = self.out.parent / 'stored-cookies.txt'
        cookie_file.write_text('stored', encoding='utf-8')
        files = gallerydl.download('https://x.com/a/status/1', str(self.out), platform_key='x',
                                   cookie_file=cookie_file, runner=self.runner(files=['x/a/2025-10-09_1_1.jpg']))
        proc = self.made[0]
        self.assertIn('--config-ignore', proc.args)  # the user's own gallery-dl config is never used
        self.assertEqual('https://x.com/a/status/1', proc.args[-1])
        self.assertEqual('stored', proc.saw_cookies)  # a copy of the stored sign-in
        self.assertNotEqual(str(cookie_file), proc.config['extractor']['cookies'])
        self.assertFalse(os.path.exists(proc.config['extractor']['cookies']))  # copy removed afterwards
        self.assertEqual([str(self.out / 'x' / 'a' / '2025-10-09_1_1.jpg')], files)

    def test_failure_raises_the_last_error_line(self):
        stderr = '[twitter][info] ...\n[twitter][error] AuthorizationError: Login required\n'
        with self.assertRaises(RuntimeError) as ctx:
            gallerydl.download('https://x.com/a/status/1', str(self.out), platform_key='x',
                               runner=self.runner(returncode=16, stderr=stderr))
        self.assertEqual('gallery-dl failed: AuthorizationError: Login required', str(ctx.exception))
        self.assertEqual(errors.AUTH_REQUIRED, errors.classify(ctx.exception, platform='X').kind)

    def test_stop_terminates_the_program(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(RuntimeError) as ctx:
            gallerydl.download('https://x.com/a/status/1', str(self.out), platform_key='x', cancel_event=cancel,
                               runner=self.runner(hang=True))
        self.assertTrue(self.made[0].terminated)
        self.assertEqual('Stopped', str(ctx.exception))

    def test_not_installed(self):
        self.command.stop()
        self.addCleanup(self.command.start)  # so the cleanup's stop() has something to stop
        with mock.patch('importlib.util.find_spec', return_value=None), \
                mock.patch.object(gallerydl.shutil, 'which', return_value=None):
            self.assertEqual((False, gallerydl.NOT_INSTALLED), gallerydl.available())
            with self.assertRaises(RuntimeError) as ctx:
                gallerydl.download('u', str(self.out), platform_key='x')
        info = errors.classify(ctx.exception)
        self.assertEqual((errors.MISSING_COMPONENT, errors.HOW_TO_FIX), (info.kind, info.action))


class FallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out = tmp.name
        from multidownloader import session_store
        patcher = mock.patch.object(session_store, 'SESSION_ROOT', Path(tmp.name) / '.sessions')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.downloader = Downloader(self.out)
        self.downloader._refreshed.update(self.downloader.handlers)  # no browser refresh in tests

    def fail_with(self, handler, message):
        fake = mock.Mock(available=mock.Mock(return_value=(True, '')))
        fake.download.side_effect = RuntimeError(message)
        self.downloader.handlers[handler] = fake

    def test_photo_only_tweet_gets_its_images(self):
        self.fail_with('Twitter', 'yt-dlp failed for Twitter: ERROR: [twitter] 1: No video could be found in this tweet')
        with mock.patch.object(gallerydl, 'download', return_value=['a.jpg', 'b.jpg']) as gdl:
            outcome = self.downloader.download('Twitter', 'https://x.com/a/status/1', {}, out_dir=self.out)
        self.assertEqual({'backend': 'gallery-dl', 'files': ['a.jpg', 'b.jpg']}, outcome)
        self.assertEqual('x', gdl.call_args.kwargs['platform_key'])

    def test_threads_images_say_not_supported(self):
        self.fail_with('Threads', 'ERROR: [Threads] 1: No video could be found')
        with self.assertRaises(RuntimeError) as ctx:
            self.downloader.download('Threads', 'https://www.threads.net/@u/post/C1', {}, out_dir=self.out)
        self.assertEqual(THREADS_IMAGES, str(ctx.exception))
        self.assertEqual(errors.UNSUPPORTED, errors.classify(ctx.exception).kind)

    def test_no_fallback_for_other_errors_or_video_only_sites(self):
        self.fail_with('Twitter', 'HTTP Error 404: Not Found')
        with mock.patch.object(gallerydl, 'download') as gdl, self.assertRaises(RuntimeError):
            self.downloader.download('Twitter', 'https://x.com/a/status/1', {}, out_dir=self.out)
        gdl.assert_not_called()
        self.fail_with('YouTube', 'No video formats found')
        with mock.patch.object(gallerydl, 'download') as gdl, self.assertRaises(RuntimeError):
            self.downloader.download('YouTube', 'https://youtu.be/abc', {}, out_dir=self.out)
        gdl.assert_not_called()

    def test_no_images_either_keeps_the_no_media_error(self):
        self.fail_with('Reddit', 'No video could be found')
        with mock.patch.object(gallerydl, 'download', return_value=[]), self.assertRaises(RuntimeError) as ctx:
            self.downloader.download('Reddit', 'https://www.reddit.com/r/a/comments/b', {}, out_dir=self.out)
        self.assertEqual(errors.NO_MEDIA, errors.classify(ctx.exception).kind)

    def test_run_records_the_backend_that_did_the_work(self):
        class ImagesInstead:
            def download(self, source, url, options, out_dir=None):
                (Path(out_dir) / 'x').mkdir()
                (Path(out_dir) / 'x' / 'p_1.jpg').write_bytes(b'1')
                return {'backend': 'gallery-dl', 'files': []}

        with runs.Run(Path(self.out) / 'out') as run:
            result = run.download(ImagesInstead(), 'Twitter', 'https://x.com/a/status/1')
        self.assertEqual(('saved', 'gallery-dl'), (result.status, result.backend))


class BlueskyTests(unittest.TestCase):
    def test_platform(self):
        platform = detect.platform_for_url('https://bsky.app/profile/bsky.app/post/3mv3shqdfuc2e')
        self.assertEqual(('bluesky', 'Bluesky'), (platform.key, platform.name))
        self.assertEqual('none', platform.signin_need)
        self.assertIsNone(platform.terms)
        self.assertEqual('3mv3shqdfuc2e', history.post_id('https://bsky.app/profile/bsky.app/post/3mv3shqdfuc2e'))
        self.assertEqual(('gallery-dl', 'yt-dlp'), BY_KEY['bluesky'].backends)

    def test_no_signin_ever(self):
        with tempfile.TemporaryDirectory() as tmp:
            from multidownloader import session_store
            with mock.patch.object(session_store, 'SESSION_ROOT', Path(tmp) / '.sessions'):
                self.assertEqual('Not needed for public posts', signins.status(BY_KEY['bluesky']).text)
                self.assertEqual([], signins.preflight_warnings(['https://bsky.app/profile/a/post/b']))

    def test_gallery_dl_post_details(self):
        data = {'category': 'bluesky', 'subcategory': 'post', 'post_id': '3mv3', 'date': '2026-09-09 15:00:07',
                'author': {'handle': 'bsky.app', 'displayName': 'Bluesky'}, 'text': 'hello'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'p.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            post = runs.post_from_metadata(path)
        self.assertEqual({'id': '3mv3', 'uploader': 'bsky.app', 'posted_at': '2026-09-09T15:00:07Z', 'title': 'hello'},
                         post)


class ErrorTextTests(unittest.TestCase):
    def test_gallery_dl_messages(self):
        self.assertEqual(errors.NOT_FOUND, errors.classify('Requested post could not be found').kind)
        self.assertEqual(errors.UNSUPPORTED, errors.classify('No suitable extractor found for "x"').kind)


@unittest.skipUnless(os.environ.get('MSDL_LIVE') == '1', 'set MSDL_LIVE=1 for tests that use the network')
class LiveTests(unittest.TestCase):
    def test_public_bluesky_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = gallerydl.download('https://bsky.app/profile/bsky.app/post/3mv3shqdfuc2e', tmp,
                                       platform_key='bluesky')
            names = sorted(Path(f).name for f in files)
        self.assertEqual(['2026-09-09_3mv3shqdfuc2e.json', '2026-09-09_3mv3shqdfuc2e_1.jpg'], names)


if __name__ == '__main__':
    unittest.main()
